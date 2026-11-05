"""One local, read-only name projection shared by inbox and directory views."""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sqlite3

from .clean import clean_name, display_text, first_name
from .country import country_from_phone, phone_digits, phone_label
from .sources import (STATE_PATH, DIRECTORY_PATH, MEMBERS_PATH, DOSSIERS_PATH,
                      CONTACTS_PATH, quote, readonly, schema, seconds, stamp)


@dataclass(frozen=True)
class Name:
    value: str
    source: str
    reason: str | None = None

    @property
    def named(self):
        return self.source != 'fallback'


def _state_names(db):
    groups, members = {}, {}
    # A timestamp wins across tables. The old preferred source breaks ties only
    # in schemas without timestamps; never let a placeholder mask older names.
    for table, columns in schema(db).items():
        identity = next((c for c in ('group_id', 'room_id', 'chatroom_id') if c in columns), None)
        labels = [c for c in ('group_name', 'room_name', 'chatroom_name') if c in columns]
        target = groups
        if not identity or not labels:
            identity = next((c for c in ('sender_id', 'wxid', 'member_id', 'user_id', 'contact_id') if c in columns), None)
            labels = [c for c in ('remark', 'nickname', 'nick_name', 'display_name', 'member_name', 'contact_name') if c in columns]
            target = members
        if not identity or not labels:
            continue
        times = [c for c in ('updated_at', 'last_seen', 'observed_at', 'fetched_at', 'created_at', 'closed_at', 'at') if c in columns]
        keys = [identity, *labels, *times]
        for row in db.execute('SELECT ' + ','.join(map(quote, keys)) + ' FROM ' + quote(table)):
            key, value = row[0], first_name(*row[1:1 + len(labels)])
            if not isinstance(key, str) or not value:
                continue
            rank = (max((seconds(v) for v in row[1 + len(labels):]), default=0), table == 'onboarding_candidates')
            if key not in target or rank > target[key][0]:
                target[key] = rank, value
    return ({k: value[1] for k, value in groups.items()},
            {k: value[1] for k, value in members.items()})


class Snapshot:
    def __init__(self):
        self.directory, self.members, self.groups, self.legacy_members = {}, {}, {}, {}
        self.dossier_groups, self.dossier_contacts, self.private_contacts, self.phones = {}, {}, {}, {}
        self.directory_remarks, self.private_remarks, self.directory_ids = {}, {}, set()
        self.status = {}
        self.group_members = {}

    def group(self, group_id, fallback=None):
        for source, names in (('directory', self.directory), ('state', self.groups), ('dossier', self.dossier_groups)):
            if names.get(group_id):
                return Name(names[group_id], source)
        # A group nobody named: WeChat itself shows member names, so do the same.
        members = [n for n in dict.fromkeys(self.group_members.get(group_id, [])) if n]
        if members:
            label = '、'.join(members[:3]) + (f'等{len(members)}人' if len(members) > 3 else '')
            return Name(display_text(label), 'members')
        reason = ('尚未刷新通讯录' if self.status.get('directory') == 'missing' else
                  '通讯录缓存暂不可读' if self.status.get('directory') != 'ok' else
                  '接口没返回群名' if group_id in self.directory_ids else '通讯录还没有这个群的名字')
        return Name(display_text(fallback) or display_text('微信群 ·' + group_id[-4:]), 'fallback', reason)

    def group_name(self, group_id, fallback=None):
        return self.group(group_id, fallback).value

    def member(self, group_id, sender_id):
        for source, value in (('directory', self.directory.get(sender_id)),
                              ('members', self.members.get((group_id, sender_id))),
                              ('state', self.legacy_members.get(sender_id))):
            if value:
                return Name(value, source)
        return Name(display_text('成员 ' + sender_id[-4:]), 'fallback', '接口没返回成员昵称')

    def member_name(self, group_id, sender_id):
        return self.member(group_id, sender_id).value

    def contact(self, identity, *, channel='wechat', provided_name=None, fallback=None,
                group_id=None, app=None, own=None):
        account = hashlib.sha256((app + '\0' + own).encode()).hexdigest() if app is not None and own is not None else None
        if channel == 'wechat':
            # Manual remarks in either directory precede the nickname caches.
            for source, value in (('directory', self.directory_remarks.get(identity)),
                                  ('directory', self.private_remarks.get((account, identity))),
                                  ('directory', self.directory.get(identity)),
                                  ('directory', self.private_contacts.get((account, identity))),
                                  ('members', self.members.get((group_id, identity))),
                                  ('state', self.legacy_members.get(identity)),
                                  ('dossier', self.dossier_contacts.get((channel, app, own, identity)))):
                if value:
                    return Name(value, source)
        elif channel == 'whatsapp':
            provided = clean_name(provided_name)
            if isinstance(provided_name, str) and (provided_name.strip() in (identity, identity.split('@')[0])
                    or identity.endswith('@c.us') and phone_digits(identity.split('@')[0])
                    and phone_digits(provided_name) == phone_digits(identity.split('@')[0])):
                provided = None
            value = provided or self.dossier_contacts.get((channel, None, None, identity))
            if value:
                return Name(value, 'channel' if provided else 'dossier')
            if identity.endswith('@g.us'):
                return Name('WhatsApp 群 ·' + identity.split('@')[0][-4:], 'fallback', '接口没返回群名')
            if identity.endswith('@c.us'):
                label = phone_label(identity.removesuffix('@c.us'))
                if label:
                    return Name(label, 'fallback', '接口没返回联系人姓名')
            return Name(display_text('WhatsApp ·' + identity.split('@')[0][-4:]), 'fallback', '接口没返回联系人姓名')
        value = clean_name(provided_name)
        if value:
            return Name(value, 'channel')
        return Name(display_text(fallback) or '未命名联系人', 'fallback', '本地记录没有联系人姓名')

    def contact_name(self, identity, **kwargs):
        return self.contact(identity, **kwargs).value

    def country(self, identity, *, channel='wechat', phone=None, app=None, own=None):
        # Never interpret wxids, group IDs, arbitrary names or message text as phones.
        number = phone
        if channel == 'whatsapp' and identity.endswith('@c.us'):
            number = identity.removesuffix('@c.us')
        if number is None:
            account = hashlib.sha256((app + '\0' + own).encode()).hexdigest() if app is not None and own is not None else None
            number = (self.phones.get(('account', account, None, identity))
                      or self.phones.get((channel, app, own, identity))
                      or self.phones.get((channel, None, None, identity)))
        return country_from_phone(number)


class NamesResolver:
    def __init__(self, state_path=STATE_PATH, *, directory_path=None, members_path=None,
                 dossiers_path=None, contacts_path=None):
        self.state_path = Path(state_path)
        # Fictional/custom sources never silently open a production side store.
        root = self.state_path.parent if self.state_path != STATE_PATH else None
        def source(path, default, local):
            return Path(path) if path is not None else root / local if root else default
        self.directory_path = source(directory_path, DIRECTORY_PATH, 'wechat-directory.sqlite3')
        self.members_path = source(members_path, MEMBERS_PATH, 'names/names.sqlite3')
        self.dossiers_path = source(dossiers_path, DOSSIERS_PATH, 'dossiers.sqlite3')
        self.contacts_path = source(contacts_path, CONTACTS_PATH, 'disabled-contacts.sqlite3')
        self._stamp, self._snapshot = object(), None

    def fingerprint(self):
        return tuple(stamp(p) for p in (self.state_path, self.directory_path, self.members_path,
                                       self.dossiers_path, self.contacts_path))

    def snapshot(self, state_db=None):
        fingerprint = self.fingerprint()
        if fingerprint == self._stamp and self._snapshot is not None:
            return self._snapshot
        result = Snapshot()
        def read(key, path, reader):
            try:
                with readonly(path) as db:
                    reader(db)
            except FileNotFoundError:
                result.status[key] = 'missing'
            except (OSError, ValueError, TypeError, RecursionError, sqlite3.Error):
                result.status[key] = 'unavailable'
            else:
                result.status[key] = 'ok'

        def directory(db):
            columns = schema(db).get('directory_contacts', set())
            if not {'id', 'kind'} <= columns:
                raise ValueError
            keys = ['id', 'kind', *[k for k in ('remark', 'nickname', 'phone') if k in columns]]
            for row in db.execute('SELECT ' + ','.join(keys) + ' FROM directory_contacts'):
                row = dict(zip(keys, row))
                result.directory_ids.add(row['id'])
                remark = clean_name(row.get('remark'))
                if remark:
                    result.directory_remarks[row['id']] = remark
                name = first_name(row.get('remark'), row.get('nickname'))
                if name:
                    result.directory[row['id']] = name
                if row['kind'] == 'private' and phone_digits(row.get('phone')):
                    result.phones['wechat', None, None, row['id']] = row['phone']

        def members(db):
            for group, identity, display, nick in db.execute('SELECT group_id,wxid,display_name,nick_name FROM nm_members ORDER BY rowid'):
                name = first_name(display, nick)
                if name:
                    result.members[group, identity] = name
                    result.group_members.setdefault(group, []).append(name)

        def state(db):
            result.groups, result.legacy_members = _state_names(db)

        def dossiers(db):
            for raw, in db.execute('SELECT contacts_json FROM sd_supplier ORDER BY supplier_id'):
                try:
                    contacts = json.loads(raw)
                except (ValueError, TypeError):
                    continue
                for contact in contacts if isinstance(contacts, list) else []:
                    if not isinstance(contact, dict):
                        continue
                    kind = contact.get('channel')
                    if kind == 'wechat_group' and isinstance(contact.get('group_id'), str):
                        name = first_name(contact.get('group_name'), contact.get('name'))
                        if name:
                            result.dossier_groups.setdefault(contact['group_id'], name)
                    elif kind in ('wechat_private', 'whatsapp'):
                        channel = 'wechat' if kind == 'wechat_private' else 'whatsapp'
                        identity = contact.get('peer_id') if channel == 'wechat' else contact.get('chat_id')
                        if not isinstance(identity, str):
                            continue
                        key = (channel, contact.get('app_id') if channel == 'wechat' else None,
                               contact.get('own_wxid') if channel == 'wechat' else None, identity)
                        name = first_name(contact.get('remark'), contact.get('name'), contact.get('nickname'))
                        if name:
                            result.dossier_contacts.setdefault(key, name)
                        if phone_digits(contact.get('phone')):
                            result.phones.setdefault(key, contact['phone'])

        def private(db):
            columns = schema(db).get('contacts', set())
            keys = ['account_sha256', 'contact_id', 'remark', 'nickname']
            if not set(keys) <= columns:
                raise ValueError
            keys += [k for k in ('phone',) if k in columns]
            ordering = ' ORDER BY updated_at ASC' if 'updated_at' in columns else ''
            for row in db.execute('SELECT ' + ','.join(keys) + ' FROM contacts' + ordering):
                row = dict(zip(keys, row))
                remark = clean_name(row['remark'])
                if remark:
                    result.private_remarks[row['account_sha256'], row['contact_id']] = remark
                name = first_name(row['remark'], row['nickname'])
                if name:
                    result.private_contacts[row['account_sha256'], row['contact_id']] = name
                if phone_digits(row.get('phone')):
                    result.phones['account', row['account_sha256'], None, row['contact_id']] = row['phone']

        read('directory', self.directory_path, directory)
        read('members', self.members_path, members)
        if state_db is not None:
            state(state_db)
            result.status['state'] = 'ok'
        else:
            read('state', self.state_path, state)
        read('dossiers', self.dossiers_path, dossiers)
        read('private', self.contacts_path, private)
        # Do not pin a snapshot taken while one of its sources was changing.
        if fingerprint == self.fingerprint():
            self._stamp, self._snapshot = fingerprint, result
        return result

    def group_name(self, group_id, **kwargs):
        return self.snapshot().group_name(group_id, **kwargs)

    def member_name(self, group_id, sender_id):
        return self.snapshot().member_name(group_id, sender_id)

    def contact_name(self, identity, **kwargs):
        return self.snapshot().contact_name(identity, **kwargs)
