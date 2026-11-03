import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import urlencode

import pytest
from starlette.datastructures import QueryParams
from starlette.responses import JSONResponse

from console.archive.service import Archive
from console.archive.validation import BadRequest, Conflict, Missing
from console.classify import Classifier, Policy, PolicyInbox, PolicyLang
from console.classify.rules import suggest
from console.classify.service import day_at
from console.classify.store import Store
from console.inbox.model import make_key
from console import v3_classify_api, v3_inbox_api

NOW = 1793520000.0
BODY = '虚构正文不应被留存 need a quotation for fictional lights'


def conversation(identity='fictional-one', name='CUS 虚构灯光买家', channel='alibaba', first=None):
    key = make_key('conversation', channel, '虚构账号', identity)
    return dict(key=key, channel=channel, account='虚构账号', buyer_name=name, country='',
                first_seen=NOW - 60 if first is None else first, last_seen=NOW - 30,
                messages=(dict(text=BODY, role='buyer', sent_at=NOW - 30, sender_label='虚构联系人'),),
                n_messages=1, waiting_reply=True, waiting_since=NOW - 30, waiting_hours=1,
                dormant=False, counterpart_role='customer', channel_label='虚构渠道', is_group=False,
                source_url=None, can_send=False, copy_hint='请核对后复制')


class Inbox:
    def __init__(self, rows):
        self.rows = {c['key']: c for c in rows}

    def read(self, now=None):
        return dict(ready=True, conversations=deepcopy(self.rows))

    def detail(self, key, now=None):
        return deepcopy(self.rows.get(key))

    def summary(self, now):
        return dict(ready=True, waiting_total=len(self.rows), supplier_waiting=0, channels=[],
                    by_channel=[dict(channel='alibaba', waiting=len(self.rows))])


@pytest.fixture
def env(tmp_path):
    archive_path = tmp_path / 'archive.sqlite3'
    archive = Archive(archive_path)
    archive.store.initialize()
    a, b = conversation(), conversation('fictional-two', 'SUP 虚构工厂')
    inbox = Inbox([a, b])
    service = Classifier(inbox=inbox, path=tmp_path / 'classify.sqlite3', archive_path=archive_path, clock=lambda: NOW)
    return SimpleNamespace(service=service, inbox=inbox, a=a, b=b, archive=archive, root=tmp_path)


def decision(env, conv=None, role='customer', revision=0, **extra):
    return dict(key=(conv or env.a)['key'], revision=revision, role=role, **extra)


@pytest.mark.parametrize('name,channel,text,role', [
    ('SUP001 虚构甲', 'wechat', '', 'supplier'), ('CUS002 虚构乙', 'wechat', '', 'customer'),
    ('虚构物流', 'wechat', '', 'forwarder'), ('虚构线材配套', 'wechat', '', 'vendor'),
    ('虚构员工内部群', 'wechat', '', 'internal'), ('虚构测试群', 'wechat', '', 'test'),
    ('虚构垃圾推广', 'wechat', '', 'spam'), ('虚构制造工厂', 'wechat', '', 'supplier'),
    ('虚构联系人', 'whatsapp', '请报价多少钱', 'customer'),
    ('虚构联系人', 'wechat', '报价如下我司供应', 'supplier'),
    ('虚构联系人', 'wechat', '物流单号 ABC12345678', 'forwarder'),
    ('虚构联系人', 'wechat', '刷单返利', 'spam'),
])
def test_rule_categories(name, channel, text, role):
    result = suggest(name, channel, [dict(role='buyer', text=text)])
    assert result['suggested_role'] == role
    assert result['confidence'] > .5
    assert text not in result['reasons'] or not text


@pytest.mark.parametrize('name,text', [('SUP 虚构工厂', 'need a price please'),
                                    ('SUP CUS 虚构冲突', ''), ('虚构内部测试群', '')])
def test_conflicting_clues_are_low_confidence(name, text):
    result = suggest(name, 'wechat', [dict(role='buyer', text=text)])
    assert result['conflict'] and result['confidence'] <= .4
    assert '线索方向有冲突' in result['reasons'][-1]


def test_no_false_sup_or_company_direction_or_language_inference():
    result = suggest('SUPPORT CUSTOM 虚构有限公司', 'whatsapp', native_language='es')
    assert result['suggested_role'] == 'unclassified'
    assert result['confidence'] < .3
    assert suggest('虚构群', 'wechat', member_roles=['supplier', 'supplier'])['suggested_role'] == 'supplier'
    assert suggest('虚构群', 'wechat', [dict(role='seller', text='请报价')])['suggested_role'] == 'unclassified'


def test_get_does_not_create_and_scan_is_daily_idempotent_private_and_no_bodies(env, caplog):
    assert env.service.summary()['total'] == 0
    assert not env.service.store.path.exists()
    assert env.service.scan('虚构老板')['added'] == 2
    assert env.service.scan('虚构老板')['added'] == 0
    assert env.service.summary()['pending'] == 2
    result = env.service.list()
    assert result['items'] == sorted(result['items'], key=lambda r: (-r['confidence'], r['key']))
    with sqlite3.connect(env.service.store.path) as db:
        all_values = repr(db.execute('SELECT * FROM suggestions').fetchall())
        all_values += repr(db.execute('SELECT * FROM classification_events').fetchall())
    assert BODY not in all_values + caplog.text
    assert env.service.store.path.stat().st_mode & 0o777 == 0o600
    assert not env.service.list('2020-01-01')['items']
    assert env.service.list('all')['total'] == 2


def test_pending_advice_refreshes_after_first_messages_without_overwriting_confirmed(env):
    env.b = conversation('fictional-two', 'SUP 虚构工厂', channel='wechat')
    env.inbox.rows = {env.b['key']: deepcopy(env.b)}
    env.inbox.rows[env.b['key']]['messages'] = ()
    env.service.scan('虚构老板')
    row = next(r for r in env.service.list()['items'] if r['key'] == env.b['key'])
    assert not row['conflict']
    env.inbox.rows[env.b['key']]['messages'] = env.b['messages']
    assert env.service.scan('虚构老板')['updated'] == 1
    row = next(r for r in env.service.list()['items'] if r['key'] == env.b['key'])
    assert row['conflict'] and row['revision'] == 1 and row['confidence'] <= .4
    with pytest.raises(Conflict):
        env.service.decide([decision(env, env.b, 'supplier')], 'confirm', '虚构老板')
    env.service.decide([decision(env, env.b, 'supplier', revision=1)], 'confirm', '虚构老板')
    env.inbox.rows[env.b['key']]['buyer_name'] = '虚构测试新名称'
    assert env.service.scan('虚构老板')['updated'] == 0
    assert Policy(env.service.store.path).flags(env.b['key'])['classification'] == 'supplier'


def test_directory_contacts_without_messages_and_unknown_first_seen(env):
    class Directory:
        def list(self, values):
            return dict(total=2, notes=['虚构来源不完整'], items=[
                dict(id='fictional-new@chatroom', kind='group', name='SUP 虚构新群', first_seen=NOW - 10),
                dict(id='fictional-private', kind='private', name='虚构测试联系人', first_seen=None)])
    lang = Mock()
    lang.read.return_value = {'contact': {'native_language': 'es'}}
    env.service.directory, env.service.lang = Directory(), lang
    assert env.service.scan('虚构老板')['added'] == 4
    assert lang.read.call_count == 2
    assert env.service.summary()['unknown_first_seen'] == 1
    assert len(env.service.list()['items']) == 4


def test_public_language_reader_uses_scan_snapshot_without_fetching_chat_history(env):
    from console.lang import Lang
    env.inbox.detail = Mock(side_effect=AssertionError('不能为了读取语言逐会话取历史'))
    lang_path = env.root / 'lang.sqlite3'
    env.service.lang_factory = lambda snapshot: Lang(lang_path, inbox=snapshot)
    assert env.service.scan('虚构老板')['added'] == 2
    env.inbox.detail.assert_not_called()
    assert not lang_path.exists()


def test_confirmation_correction_undo_and_archive_version_unchanged(env):
    with sqlite3.connect(env.archive.store.path) as db:
        versions = db.execute('SELECT * FROM schema_migrations').fetchall()
    env.service.scan('虚构老板')
    env.service.decide([decision(env)], 'confirm', '虚构老板')
    env.service.decide([decision(env)], 'confirm', '虚构老板')
    with sqlite3.connect(env.archive.store.path) as db:
        assert db.execute('SELECT COUNT(*) FROM parties').fetchone()[0] == 1
        assert db.execute('SELECT * FROM schema_migrations').fetchall() == versions
        assert db.execute('SELECT conversation_ref FROM parties').fetchone()[0] == env.a['key']
    env.service.decide([decision(env, role='supplier', revision=1)], 'confirm', '虚构老板')
    row = env.service.list(status='all')['items'][0]
    assert row['decision_role'] == 'supplier'
    env.service.decide([dict(key=env.a['key'], revision=2)], 'undo', '虚构老板')
    env.service.decide([dict(key=env.a['key'], revision=2)], 'undo', '虚构老板')
    assert Policy(env.service.store.path).flags(env.a['key'])['reply_eligible']
    with sqlite3.connect(env.archive.store.path) as db:
        assert db.execute('SELECT COUNT(*) FROM parties').fetchone()[0] == 2
    env.service.decide([decision(env, role='supplier', revision=3)], 'confirm', '虚构老板')
    assert env.service.summary()['confirmed'] == 1


def test_batch_rollback_on_collision_and_stale_revision(env):
    env.service.scan('虚构老板')
    env.archive.write('party_create', None, dict(kind='supplier', name=env.b['buyer_name']), '虚构操作者')
    with pytest.raises(Conflict):
        env.service.decide([decision(env), decision(env, env.b, 'supplier')], 'confirm', '虚构老板')
    assert env.service.summary()['pending'] == 2
    with sqlite3.connect(env.archive.store.path) as db:
        assert db.execute('SELECT COUNT(*) FROM parties').fetchone()[0] == 1
    env.service.decide([decision(env, role='internal')], 'confirm', '虚构老板')
    with pytest.raises(Conflict):
        env.service.decide([decision(env, role='customer')], 'confirm', '虚构老板')


def test_concurrent_confirmation_is_idempotent(env):
    env.service.scan('虚构老板')
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: env.service.decide([decision(env)], 'confirm', '虚构老板'), range(4)))
    assert all(r['ok'] for r in results)
    with sqlite3.connect(env.archive.store.path) as db:
        assert db.execute('SELECT COUNT(*) FROM parties').fetchone()[0] == 1


@pytest.mark.parametrize('role', ['internal', 'test', 'spam', 'ignore'])
def test_ignore_waiting_detail_customers_and_kb_flags_without_mutating_source(env, role):
    env.service.scan('虚构老板')
    env.service.decide([decision(env, role=role)], 'ignore' if role == 'ignore' else 'confirm', '虚构老板')
    wrapped = PolicyInbox(env.inbox, Policy(env.service.store.path))
    assert wrapped.summary(NOW)['waiting_total'] == 1
    assert wrapped.summary(NOW)['by_channel'][0]['waiting'] == 1
    assert not wrapped.detail(env.a['key'])['waiting_reply']
    assert not wrapped.detail(env.a['key'])['kb_eligible']
    assert not wrapped.detail(env.a['key'])['draft_eligible']
    assert env.inbox.detail(env.a['key'])['waiting_reply']
    env.service.decide([dict(key=env.a['key'], revision=1)], 'undo', '虚构老板')
    assert wrapped.summary(NOW)['waiting_total'] == 2
    assert wrapped.detail(env.a['key'])['waiting_reply']


def request(fields=None, query=None, *, authenticated=True, rechecked=True, key=None, content_type='application/x-www-form-urlencoded'):
    raw = urlencode(fields or {}).encode()
    async def body():
        return raw
    return SimpleNamespace(state=SimpleNamespace(password_principal=SimpleNamespace(username='虚构老板'),
        session=SimpleNamespace(authenticated=authenticated), password_recheck=lambda: rechecked),
        query_params=QueryParams(query or {}), path_params={'key': key} if key else {}, body=body,
        headers={'content-type': content_type, 'content-length': str(len(raw))})


def endpoints(env):
    gate = SimpleNamespace(error_response=lambda req, code: JSONResponse({'error': '请先登录'}, status_code=code))
    return {r.name: r.endpoint for r in v3_classify_api.build(gate, service=env.service)}, gate


def test_http_classify_auth_recheck_validation_and_fixed_errors(env, caplog):
    handlers, _ = endpoints(env)
    for handler in handlers.values():
        for options in [dict(authenticated=False), dict(rechecked=False)]:
            result = asyncio.run(handler(request(**options)))
            assert result.status_code == 403
    assert not env.service.store.path.exists()
    result = asyncio.run(handlers['classify_scan'](request(dict(confirm='1'))))
    assert result.status_code == 200
    assert result.headers['cache-control'] == 'no-store'
    assert result.headers['x-content-type-options'] == 'nosniff'
    for query in [{'day': 'invalid'}, {'day': '2026-02-30'}, {'status': 'invalid'}, {'path': '/tmp/no'}]:
        assert asyncio.run(handlers['classify_list'](request(query=query))).status_code == 400
    assert asyncio.run(handlers['classify_scan'](request({'confirm': '1'}, content_type='application/json'))).status_code == 400
    assert asyncio.run(handlers['classify_decide'](request({'action': 'confirm', 'items': '{broken'}))).status_code == 400
    with patch.object(env.service, 'scan', side_effect=ValueError(BODY)):
        result = asyncio.run(handlers['classify_scan'](request({'confirm': '1'})))
    assert result.status_code == 503
    assert BODY not in result.body.decode() + caplog.text
    result = asyncio.run(handlers['classify_flags'](request(query={'key': env.a['key']})))
    assert json.loads(result.body)['kb_eligible']


def test_actual_inbox_api_waiting_and_runner_blocked_before_knowledge(env):
    handlers, gate = endpoints(env)
    env.service.scan('虚构老板')
    runner, hub = Mock(), Mock()
    routes = v3_inbox_api.build(gate, inbound_path=env.root / 'disabled.sqlite3',
        drafts_path=env.root / 'drafts.sqlite3', inbox=env.inbox, runner=runner,
        classify_path=env.service.store.path, hub=hub)
    inbox = {r.name: r.endpoint for r in routes}
    before = asyncio.run(inbox['inbox_summary'](request()))
    assert json.loads(before.body)['waiting_total'] == 2
    env.service.decide([decision(env, role='internal')], 'confirm', '虚构老板')
    after = asyncio.run(inbox['inbox_summary'](request()))
    assert json.loads(after.body)['waiting_total'] == 1
    with patch('console.v3_inbox_api.draft_context') as knowledge:
        result = asyncio.run(inbox['inbox_draft'](request(key=env.a['key'])))
    assert result.status_code == 409
    runner.assert_not_called()
    knowledge.assert_not_called()
    assert not (env.root / 'drafts.sqlite3').exists()
    detail = asyncio.run(inbox['inbox_detail'](request(key=env.a['key'])))
    assert not json.loads(detail.body)['conversation']['draft_eligible']


def test_ignore_during_model_call_discards_result(env):
    from console.inspiration.enrich import RunnerOutput
    env.service.scan('虚构老板')
    def run(prompt):
        env.service.decide([decision(env, role='internal')], 'confirm', '虚构老板')
        return RunnerOutput(json.dumps(dict(draft='虚构回复', language='zh-CN', needs_confirmation=[])), '虚构模型')
    _, gate = endpoints(env)
    routes = v3_inbox_api.build(gate, inbound_path=env.root / 'disabled.sqlite3',
        drafts_path=env.root / 'drafts.sqlite3', inbox=env.inbox, runner=run,
        classify_path=env.service.store.path)
    handler = next(r.endpoint for r in routes if r.name == 'inbox_draft')
    with patch('console.v3_inbox_api.draft_context', return_value={'text': '', 'sources': []}):
        result = asyncio.run(handler(request(key=env.a['key'])))
    assert result.status_code == 409
    assert not (env.root / 'drafts.sqlite3').exists()


def test_unreadable_policy_does_not_fall_back_to_drafting(env):
    env.service.store.path.write_bytes(b'fictional corrupt database')
    _, gate = endpoints(env)
    runner = Mock()
    routes = v3_inbox_api.build(gate, inbound_path=env.root / 'disabled.sqlite3',
        drafts_path=env.root / 'drafts.sqlite3', inbox=env.inbox, runner=runner,
        classify_path=env.service.store.path)
    handler = next(r.endpoint for r in routes if r.name == 'inbox_draft')
    assert asyncio.run(handler(request(key=env.a['key']))).status_code == 503
    runner.assert_not_called()


@pytest.mark.parametrize('items', [[], [dict(key='../../bad', revision=0, role='customer')],
    [dict(key=conversation()['key'], revision=True, role='customer')],
    [dict(key=conversation()['key'], revision=0, role='invalid')],
    [dict(key=conversation()['key'], revision=0, role='customer', path='/tmp/unsafe')]])
def test_bad_decision_never_opens_db(env, items):
    with pytest.raises(BadRequest):
        env.service.decide(items, 'confirm', '虚构老板')
    assert not env.service.store.path.exists()


def test_paths_symlinks_hardlinks_and_parameterized_names(env):
    link = env.root / 'link.sqlite3'
    link.symlink_to(env.archive.store.path)
    with pytest.raises(ValueError):
        with Store(link).write():
            pass
    link.unlink()
    os.link(env.archive.store.path, link)
    with pytest.raises(ValueError):
        with Store(link).write():
            pass
    link.unlink()
    with pytest.raises(ValueError):
        Classifier(inbox=env.inbox, path=env.archive.store.path, archive_path=env.archive.store.path)
    env.service.scan('虚构老板')
    env.service.decide([decision(env, name="虚构'; DROP TABLE parties; --")], 'confirm', '虚构老板')
    with sqlite3.connect(env.archive.store.path) as db:
        assert db.execute('SELECT COUNT(*) FROM parties').fetchone()[0] == 1


def test_daily_timezone_boundary_and_unknown_source(env):
    assert day_at(1793462400) == '2026-11-01'
    assert day_at(1793462399) == '2026-10-31'
    env.inbox.rows = {}
    assert env.service.scan('虚构老板')['added'] == 0
    with pytest.raises(Missing):
        env.service.decide([decision(env)], 'confirm', '虚构老板')


def test_native_composition_policy_wrapper_blocks_before_public_lang_service(env):
    env.service.scan('虚构老板')
    env.service.decide([decision(env, role='test')], 'confirm', '虚构老板')
    lang = Mock()
    guarded = PolicyLang(lang, Policy(env.service.store.path))
    with pytest.raises(Conflict):
        guarded.write('compose', {'conversation_key': env.a['key'], 'text': BODY}, '虚构老板')
    lang.write.assert_not_called()
    guarded.read('contact', {'conversation_key': env.a['key']})
    lang.read.assert_called_once()
    env.service.decide([dict(key=env.a['key'], revision=1)], 'undo', '虚构老板')
    guarded.write('compose', {'conversation_key': env.a['key'], 'text': BODY}, '虚构老板')
    lang.write.assert_called_once()


def test_csrf_is_enforced_by_host_middleware_without_network(env):
    from starlette.applications import Starlette
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.testclient import TestClient
    handlers, gate = endpoints(env)
    class Host(BaseHTTPMiddleware):
        async def dispatch(self, req, call_next):
            req.state.password_principal = SimpleNamespace(username='虚构老板')
            req.state.password_recheck = lambda: True
            req.state.session = SimpleNamespace(authenticated=True)
            if req.method == 'POST' and req.headers.get('X-CSRF-Token') != 'fictional-csrf':
                return JSONResponse({'error': '页面校验已失效，请刷新后重试'}, status_code=403)
            return await call_next(req)
    app = Starlette(routes=v3_classify_api.build(gate, service=env.service))
    app.add_middleware(Host)
    with TestClient(app) as client:
        assert client.post('/api/v1/classify/scan', data={'confirm': '1'}).status_code == 403
        assert not env.service.store.path.exists()
        assert client.post('/api/v1/classify/scan', data={'confirm': '1'},
                           headers={'X-CSRF-Token': 'fictional-csrf'}).status_code == 200
        # Duplicate fields fail validation after the host CSRF gate.
        assert client.post('/api/v1/classify/scan', content='confirm=1&confirm=1',
            headers={'X-CSRF-Token': 'fictional-csrf', 'Content-Type': 'application/x-www-form-urlencoded'}).status_code == 400
