import hashlib
import json
import sqlite3

import pytest

from console.channels.wechat import WechatAdapter
from console.names import NamesResolver, country_from_phone
from console.names.clean import clean_name
from console.names.store import MembersCache, member_rows
from console.ops_queue.inbox import wechat
from conftest import GROUP, NOW, directory, fingerprint


def test_group_priority_latest_valid_timestamp_dossier_and_placeholder(state, tmp_path):
    with sqlite3.connect(state) as db:
        for table in ('onboarding_candidates', 'auto_collection_sources', 'auto_collection_audit', 'group_closures', 'onboarding_notices'):
            db.execute(f'CREATE TABLE {table}(group_id TEXT,group_name TEXT,updated_at REAL)')
        db.execute('INSERT INTO onboarding_candidates VALUES(?,?,?)', (GROUP, '虚构旧群', 1))
        db.execute('INSERT INTO auto_collection_sources VALUES(?,?,?)', (GROUP, '虚构采集群', 2))
        db.execute('INSERT INTO auto_collection_audit VALUES(?,?,?)', (GROUP, '虚构审计群', 3))
        db.execute('INSERT INTO group_closures VALUES(?,?,?)', (GROUP, '虚构较新群', 4))
        db.execute('INSERT INTO onboarding_notices VALUES(?,?,?)', (GROUP, '微信群 ·room', 99))
    dossier = tmp_path / 'dossiers.sqlite3'
    with sqlite3.connect(dossier) as db:
        db.execute('CREATE TABLE sd_supplier(supplier_id TEXT,contacts_json TEXT)')
        db.execute('INSERT INTO sd_supplier VALUES(?,?)', ('fiction-supplier', json.dumps([
            dict(channel='wechat_group', group_id=GROUP, name='虚构档案群'),
            dict(channel='wechat_group', group_id='fiction-only-dossier@chatroom', name='虚构独有群')])))
    cache = tmp_path / 'wechat-directory.sqlite3'
    directory(cache, [(GROUP, 'group', '虚构\x00备注@甲', '虚构昵称')])
    resolver = NamesResolver(state)
    before = fingerprint(tmp_path)
    assert resolver.group_name(GROUP) == '虚构备注＠甲'
    assert resolver.group_name('fiction-only-dossier@chatroom') == '虚构独有群'
    assert fingerprint(tmp_path) == before
    directory(cache, [(GROUP, 'group', '微信群 ·room', '微信群 ·room')])
    assert resolver.group_name(GROUP) == '虚构较新群'
    with sqlite3.connect(state) as db:
        db.execute('INSERT INTO onboarding_notices VALUES(?,?,?)', (GROUP, '虚构最新群', 100))
    assert resolver.group_name(GROUP) == '虚构最新群'
    with sqlite3.connect(state) as db:
        for table in ('onboarding_candidates', 'auto_collection_sources', 'auto_collection_audit', 'group_closures', 'onboarding_notices'):
            db.execute(f'DELETE FROM {table}')
    assert resolver.group_name(GROUP) == '虚构档案群'
    assert resolver.snapshot().group('fiction-missing@chatroom').named is False


@pytest.mark.parametrize('value,expected', [(None, None), ('微信群 ·room', None), ('成员 abcd', None),
    ('WhatsApp 群 ·abcd', None), ('未命名联系人', None), ('\u202e虚构\x00甲@乙\u200b', '虚构甲＠乙'),
    ('虚构' * 100, ('虚构' * 100)[:128])])
def test_cleaning(value, expected):
    assert clean_name(value) == expected


def test_members_are_scoped_manual_remark_wins_and_empty_reply_keeps_old_name(state, tmp_path):
    resolver = NamesResolver(state)
    members = MembersCache(resolver.members_path)
    rows = member_rows({'ret': 200, 'data': {'memberList': [dict(wxid='fiction-member', displayName='虚构群昵称', nickName='虚构昵称')]}})
    members.save(GROUP, rows, NOW)
    members.save('fiction-room-b@chatroom', {'fiction-member': ('虚构乙群昵称', None)}, NOW)
    assert resolver.member_name(GROUP, 'fiction-member') == '虚构群昵称'
    assert resolver.member_name('fiction-room-b@chatroom', 'fiction-member') == '虚构乙群昵称'
    assert resolver.member_name('fiction-room-c@chatroom', 'fiction-member') == '成员 mber'
    members.save(GROUP, {'fiction-member': (None, None)}, NOW + 1)
    assert resolver.member_name(GROUP, 'fiction-member') == '虚构群昵称'
    directory(resolver.directory_path, [('fiction-member', 'private', '虚构人工备注', '虚构通讯录昵称')])
    assert resolver.member_name(GROUP, 'fiction-member') == '虚构人工备注'


def test_channel_queue_and_cache_invalidation_without_message_change(state, tmp_path):
    resolver = NamesResolver(state)
    adapter = WechatAdapter(state, media_tokens=False, names_resolver=resolver)
    first, = adapter.list_conversations(NOW).values()
    assert first['name_missing'] and first['name_missing_reason'] == '尚未刷新通讯录'
    original = state.read_bytes(), state.stat().st_mtime_ns
    directory(resolver.directory_path, [(GROUP, 'group', '虚构显示群', None)])
    MembersCache(resolver.members_path).save(GROUP, {'fiction-member': ('虚构显示成员', None)}, NOW)
    conv, = adapter.list_conversations(NOW).values()
    queue, = wechat(state, NOW, names_resolver=resolver).values()
    assert conv['buyer_name'] == queue['buyer_name'] == '虚构显示群'
    assert conv['messages'][0]['sender_label'] == queue['messages'][0]['sender_label'] == '虚构显示成员'
    assert not conv['name_missing']
    assert (state.read_bytes(), state.stat().st_mtime_ns) == original
    with sqlite3.connect(resolver.directory_path) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('UPDATE directory_contacts SET remark=? WHERE id=?', ('虚构更新群', GROUP))
        db.commit()
        conv, = adapter.list_conversations(NOW).values()
        assert conv['buyer_name'] == '虚构更新群'


def test_private_contacts_are_account_scoped_and_explicit_phone_provides_country(state, tmp_path):
    resolver = NamesResolver(state)
    account = hashlib.sha256(b'fiction-app\0fiction-own').hexdigest()
    with sqlite3.connect(resolver.contacts_path) as db:
        db.execute('CREATE TABLE contacts(account_sha256 TEXT,contact_id TEXT,remark TEXT,nickname TEXT,phone TEXT,updated_at REAL)')
        db.execute('INSERT INTO contacts VALUES(?,?,?,?,?,?)', (account, 'fiction-peer', '虚构私聊备注', '虚构昵称', '+44 7700 900123', NOW))
    labels = resolver.snapshot()
    assert labels.contact_name('fiction-peer', app='fiction-app', own='fiction-own') == '虚构私聊备注'
    assert labels.contact_name('fiction-peer', app='fiction-other', own='fiction-own') == '未命名联系人'
    assert labels.country('fiction-peer', app='fiction-app', own='fiction-own') == ('GB', '英国')
    assert labels.country('wxid_447700900123') is None
    assert labels.country('447700900123@g.us', channel='whatsapp') is None
    directory(resolver.directory_path, [('fiction-peer', 'private', None, '虚构共享昵称')])
    assert resolver.contact_name('fiction-peer', app='fiction-app', own='fiction-own') == '虚构私聊备注'


@pytest.mark.parametrize('number,expected', [('+44 7700 900123', ('GB', '英国')), ('00447700900123', ('GB', '英国')),
    ('+1 202 555 0123', ('US', '美国')), ('+1 416 555 0123', ('CA', '加拿大')),
    ('+8613800000000', ('CN', '中国')), ('+919000000000', ('IN', '印度')),
    ('+971500000000', ('AE', '阿联酋')), ('+77000000000', ('KZ', '哈萨克斯坦')),
    ('+74950000000', ('RU', '俄罗斯')), ('+15550001111', None), ('999123456789', None),
    ('wxid_447700900123', None), ('1234', None), ('+44abc7700900123', None), (None, None)])
def test_phone_country(number, expected):
    assert country_from_phone(number) == expected


def test_member_cache_refuses_source_database_and_symlink_without_mutation(state, tmp_path):
    before = state.read_bytes(), state.stat().st_mode
    with pytest.raises(ValueError):
        MembersCache(state).save(GROUP, {}, NOW)
    link = tmp_path / 'linked.sqlite3'
    link.symlink_to(state)
    with pytest.raises(ValueError):
        MembersCache(link).save(GROUP, {}, NOW)
    assert (state.read_bytes(), state.stat().st_mode) == before


def test_private_conversation_display_and_country_use_same_resolver(state):
    resolver = NamesResolver(state)
    with sqlite3.connect(state) as db:
        db.executescript('''CREATE TABLE wechat_private_messages(app_id TEXT,message_id TEXT,own_wxid TEXT,peer_id TEXT,direction TEXT,scope_id TEXT,callback_sha256 TEXT);
            CREATE TABLE wechat_private_contacts(app_id TEXT,own_wxid TEXT,peer_id TEXT,business_role TEXT,display_name TEXT);''')
        payload = dict(group_id='fiction-peer', own_wxid='fiction-own', text='虚构私聊消息')
        db.execute('INSERT INTO messages VALUES(?,?,?,?,?)', ('fiction-app', 'fiction-private-mid', 'fiction-peer', NOW, json.dumps(payload)))
        db.execute('INSERT INTO wechat_private_messages VALUES(?,?,?,?,?,?,?)',
                   ('fiction-app', 'fiction-private-mid', 'fiction-own', 'fiction-peer', 'inbound', 'fiction-scope', 'fiction-hash'))
        db.execute('INSERT INTO wechat_private_contacts VALUES(?,?,?,?,?)',
                   ('fiction-app', 'fiction-own', 'fiction-peer', 'unknown', '虚构旧私聊名'))
    account = hashlib.sha256(b'fiction-app\0fiction-own').hexdigest()
    with sqlite3.connect(resolver.contacts_path) as db:
        db.execute('CREATE TABLE contacts(account_sha256 TEXT,contact_id TEXT,remark TEXT,nickname TEXT,phone TEXT,updated_at REAL)')
        db.execute('INSERT INTO contacts VALUES(?,?,?,?,?,?)',
                   (account, 'fiction-peer', '虚构私聊新备注', '虚构昵称', '+447700900123', NOW))
    adapter = WechatAdapter(state, media_tokens=False, contact_path=resolver.contacts_path, names_resolver=resolver)
    private = next(row for row in adapter.list_conversations(NOW).values() if not row['is_group'])
    assert private['buyer_name'] == '虚构私聊新备注' and private['country'] == 'GB'


def test_unnamed_group_shows_member_names_like_wechat(state):
    resolver = NamesResolver(state)
    members = MembersCache(resolver.members_path)
    group = 'fiction-unnamed@chatroom'
    assert resolver.group_name(group).startswith('微信群')
    members.save(group, {f'fiction-m{i}': (None, f'虚构成员{i}') for i in range(5)}, NOW)
    assert resolver.group_name(group) == '虚构成员0、虚构成员1、虚构成员2等5人'
    two = 'fiction-pair@chatroom'
    members.save(two, {'fiction-a': ('虚构甲', None), 'fiction-b': (None, '虚构乙')}, NOW)
    assert resolver.group_name(two) == '虚构甲、虚构乙'
