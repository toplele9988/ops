import pytest

from console.profiles.schema import save_profile
from console.profiles.store import working_database
from console.profiles.worker import base_profile


def seed(env, key, *, kind='supplier', confidence=.85, state='ready'):
    conv = env.inbox.conversations[key]
    profile = base_profile(conv, conv['messages'], env.clock())
    profile.update(kind=kind, confidence=confidence, state=state, messages_read=70, messages_total=90,
                   summary='虚构供应商，讨论灯具打样和发货')
    path = env.path.parent / 'profiles.sqlite3'
    with working_database(path) as db:
        with db:
            save_profile(db, profile)


@pytest.mark.parametrize('kind,action,party_kind', [('customer', 'create', 'customer'), ('supplier', 'identity', 'supplier'),
    ('forwarder', 'identity', 'forwarder'), ('vendor', 'identity', 'vendor'), ('peer', 'ignore', 'customer'),
    ('industry', 'ignore', 'customer'), ('personal', 'ignore', 'customer'), ('system', 'ignore', 'customer')])
def test_triage_uses_profiles_without_live_ai_and_preserves_source(env, kind, action, party_kind):
    key = env.conv(name='虚构不明对象', role='seller')
    seed(env, key, kind=kind, state='partial')
    result = env.roles.scan_triage('fictional-owner', use_ai=False)
    item = result['items'][0]
    assert item['source'] == 'ai_profile' and item['action'] == action
    assert item['suggested_party_kind'] == party_kind
    assert 'AI 读了 70 条消息：' in item['reason']
    assert result['runs'][0]['source'] == 'ai_profile'
    assert env.roles.read('runs')['items'][0]['source'] == 'ai_profile'
    assert env.roles.read('triage')['items'][0]['source'] == 'ai_profile'
    assert env.roles.scan_triage('fictional-owner', use_ai=False)['runs'][0]['reused']
    assert env.roles.scan_triage('fictional-owner', apply=False)['items'][0]['action'] == action


@pytest.mark.parametrize('state,confidence', [('failed', .9), ('skipped', .9), ('ready', .69)])
def test_unreliable_profiles_do_not_override_rules(env, state, confidence):
    key = env.conv(name='虚构供应商工厂')
    seed(env, key, kind='customer', state=state, confidence=confidence)
    item = env.roles.scan_triage('fictional-owner', use_ai=False)['items'][0]
    assert item['action'] == 'identity' and item['source'] == 'rules'


def test_dossier_rule_stays_above_profile(env, monkeypatch):
    from console.roles import dossier_links
    key = env.conv(name='虚构工作群', channel='wechat')
    seed(env, key, kind='customer')
    monkeypatch.setattr(dossier_links, 'load', lambda *a, **kw: [dict(conversation_key=key, supplier_id='fictional-supplier',
        name='虚构供应商', aliases=[], source_id='fictional-source', kind='supplier', party_ids=[])])
    item = env.roles.scan_triage('fictional-owner', use_ai=False)['items'][0]
    assert item['confidence'] == .9 and item['source'] == 'rules' and item['reason'].startswith('档案：')


def test_customer_profile_keeps_safe_existing_deal_link(env):
    original = env.conv(name='虚构星桥买家')
    did = env.deal(key=original)
    key = env.conv(name='虚构星桥买家')
    seed(env, key, kind='customer')
    item = env.roles.scan_triage('fictional-owner', use_ai=False)['items'][0]
    assert item['source'] == 'ai_profile' and item['action'] == 'link' and item['suggested_deal_id'] == did


def test_matching_dossier_party_keeps_95_percent_priority(env, monkeypatch):
    from console.roles import dossier_links
    key = env.conv(name='虚构群', channel='wechat')
    pid = env.aw('party_create', name='虚构工厂', kind='supplier')
    seed(env, key, kind='customer')
    monkeypatch.setattr(dossier_links, 'load', lambda *a, **kw: [dict(conversation_key=key, supplier_id='fictional-supplier',
        name='虚构工厂', aliases=[], source_id='fictional-source', kind='supplier', party_ids=[str(pid)])])
    item = env.roles.scan_triage('fictional-owner', use_ai=False)['items'][0]
    assert item['source'] == 'rules' and item['confidence'] == .95 and item['suggested_party_id'] == pid
