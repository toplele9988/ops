from datetime import datetime, timezone
import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock

from console.names import NamesResolver
from console.names.report import report
from console.names.resolver import Snapshot
from console.channels.whatsapp import WhatsAppAdapter
from conftest import GROUP, NOW, directory, fingerprint


def test_whatsapp_fallback_and_country_with_injected_transport(state, tmp_path):
    config = tmp_path / 'whatsapp.json'
    config.write_text(json.dumps(dict(waha_url='http://127.0.0.1:12345', waha_api_key='fiction-token', session='fiction-session')))
    config.chmod(0o640)
    message = dict(body='虚构问题', timestamp=NOW - 10)
    chats = [dict(id='447700900123@c.us', name='', lastMessage=message),
             dict(id='fiction-missing@g.us', name='WhatsApp ·sing', lastMessage=message),
             dict(id='fiction-known@g.us', name='虚构@群\x00', lastMessage=message)]
    def fetch(method, url, headers, timeout):
        assert method == 'GET'
        return {'status': 'WORKING', 'me': {}} if '/sessions/' in url else chats
    def stat(path):
        info = path.stat()
        return SimpleNamespace(**{key: getattr(info, key) for key in ('st_mode', 'st_size', 'st_ino', 'st_dev', 'st_mtime_ns')}, st_uid=0, st_gid=731)
    adapter = WhatsAppAdapter(config, fetch=fetch, clock=lambda: 0, stat_fn=stat, group_gid=731)
    rows = adapter.list_conversations(NOW)
    assert {r['buyer_name'] for r in rows.values()} == {'+44 7700900123（英国）', 'WhatsApp 群 ·sing', '虚构＠群'}
    assert next(r for r in rows.values() if not r['is_group'])['country'] == 'GB'
    assert all(not r['country'] for r in rows.values() if r['is_group'])
    assert Snapshot().contact_name('fiction@lid', channel='whatsapp') == 'WhatsApp ·tion'
    assert Snapshot().contact_name('447700900123@c.us', channel='whatsapp', provided_name='+44 7700 900123') == '+44 7700900123（英国）'


def test_report_counts_only_never_writes_refreshes_or_registers_media(state, tmp_path, monkeypatch):
    resolver = NamesResolver(state)
    directory(resolver.directory_path, [(GROUP, 'group', '虚构秘密群名不输出', None)])
    alibaba = tmp_path / 'inbound.sqlite3'
    with sqlite3.connect(alibaba) as db:
        db.execute('CREATE TABLE inbound(id INTEGER PRIMARY KEY,payload TEXT)')
        row = dict(account='fiction-account', conversation_id='fiction-buyer', buyer_name='虚构秘密买家不输出',
                   content='虚构聊天原文不输出', role='buyer', sent_at=datetime.fromtimestamp(NOW, timezone.utc).isoformat())
        db.execute('INSERT INTO inbound(payload) VALUES(?)', (json.dumps(row),))
    calls = []
    def get(config, path):
        calls.append(path)
        if '/sessions/' in path:
            return {'status': 'WORKING'}
        return [dict(id='447700900123@c.us', name=None, lastMessage=dict(timestamp=NOW, hasMedia=True)),
                dict(id='fiction-group@g.us', name='虚构秘密群名不输出', lastMessage=dict(body='虚构原文', timestamp=NOW))]
    wa = SimpleNamespace(_configuration=lambda: {'session': 'fiction-session'}, _get=get)
    monkeypatch.setattr('console.media.tokens.make', Mock(side_effect=AssertionError('no signing key')))
    monkeypatch.setattr('console.wamedia.ingest.IngestWorker.enqueue', Mock(side_effect=AssertionError('no ingestion')))
    before = fingerprint(tmp_path)
    result = report(state_path=state, resolver=resolver, alibaba_path=alibaba, whatsapp_adapter=wa, now=NOW)
    assert fingerprint(tmp_path) == before
    assert result['微信']['有名字'] == 1 and result['微信']['没名字'] == 0
    assert result['阿里国际站']['有名字'] == 1
    assert result['WhatsApp']['会话总数'] == 2 and result['WhatsApp']['没名字'] == 1
    assert result['WhatsApp']['原因'] == {'接口没返回联系人姓名': 1}
    encoded = json.dumps(result, ensure_ascii=False)
    assert '虚构' not in encoded and 'fiction' not in encoded and '4477' not in encoded
    def numbers_only(value):
        assert isinstance(value, dict) or type(value) is int
        if isinstance(value, dict):
            for child in value.values():
                numbers_only(child)
    numbers_only(result)
    assert len(calls) == 2


def test_unavailable_source_is_explicit_in_numeric_report(state, tmp_path):
    result = report(state_path=state, alibaba_path=tmp_path / 'missing-inbound',
                    whatsapp_config=tmp_path / 'missing-config', now=NOW)
    assert result['微信']['会话总数'] == 1
    assert result['微信']['原因'] == {'尚未刷新通讯录': 1}
    assert result['阿里国际站']['来源可读'] == 0 and result['WhatsApp']['来源可读'] == 0


def test_cli_outputs_numeric_report_and_nonzero_for_incomplete_sources(tmp_path, monkeypatch, capsys):
    from console.names.__main__ import main
    monkeypatch.setattr('sys.argv', ['names', 'report', '--wechat-state', str(tmp_path / 'missing-state'),
        '--alibaba-inbound', str(tmp_path / 'missing-inbound'), '--whatsapp-config', str(tmp_path / 'missing-config')])
    assert main() == 1
    result = json.loads(capsys.readouterr().out)
    assert all(row['会话总数'] == 0 and row['来源可读'] == 0 for row in result.values())
    assert not list(tmp_path.iterdir())


def test_directory_name_health_counts_message_conversations_and_explains_missing(state):
    from console.directory import WechatDirectory
    resolver = NamesResolver(state)
    service = WechatDirectory(state_path=state, clock=lambda: NOW, discovery_reader=lambda _: [],
                              config_reader=lambda _: {}, conversation_reader=lambda *_: {})
    assert service.summary()['name_health'] == dict(total=1, named=0, missing=1, reasons={'尚未刷新通讯录': 1})
    directory(resolver.directory_path, [(GROUP, 'group', None, None), ('fiction-no-messages@chatroom', 'group', None, None)])
    assert service.summary()['name_health'] == dict(total=1, named=0, missing=1, reasons={'接口没返回群名': 1})
    directory(resolver.directory_path, [(GROUP, 'group', '虚构补齐的群名', None)])
    assert service.summary()['name_health'] == dict(total=1, named=1, missing=0, reasons={})


def test_message_only_groups_are_included_in_directory_name_refresh(state):
    from console.directory import WechatDirectory
    calls = []
    def brief(ids):
        calls.append(ids)
        return {'ret': 200, 'data': [dict(userName=GROUP, nickName='虚构消息群名')]}
    client = SimpleNamespace(fetch_contacts_list=lambda: {'ret': 200, 'data': dict(friends=[], chatrooms=[], ghs=[])},
                             get_brief_info=brief)
    service = WechatDirectory(state_path=state, clock=lambda: NOW, discovery_reader=lambda _: [],
                              config_reader=lambda _: {}, readonly_client=client)
    result = service.refresh()
    assert result['groups_named'] == 1 and calls == [[GROUP]]
    assert NamesResolver(state).group_name(GROUP) == '虚构消息群名'
