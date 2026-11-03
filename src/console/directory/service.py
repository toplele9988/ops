"""WeChat discovery, enrollment and inbox visibility are separate facts."""
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import sqlite3
import time

from console.channels.common import ROLES, timestamp
from console.channels.wechat import WechatAdapter
from console.inbox.model import decode_key
from console.inbox.store import read_database
from console.roles.common import party_name_parts
from console.roles.triage import named_kind, placeholder_name, rules
from .refresh import RefreshCache, cached_name
from .sources import (CACHE_PATH, CONFIG_PATH, DISCOVERY_PATH, STATE_PATH, KINDS,
                      identity_kind, read_config, read_discovery, read_role_metadata, read_state, text)

STATES = ('collecting', 'enrolled_silent', 'enrolled_no_messages', 'not_enrolled')
ROLE_LABELS = dict(supplier='供应商', customer='客户', learning='学习', internal='内部',
                   unclassified='未分类', unknown='未知', forwarder='快递/物流/货代', vendor='配套供应商')
EMPTY_INPUTS = SimpleNamespace(rows={'identities': [], 'parties': []}, deals={})


def naming(name):
    """Reuse triage's exact code and keyword rules, without models or messages."""
    kind = named_kind(name.casefold())
    if kind is None:
        hint = rules(dict(channel='wechat', buyer_name=name, key='directory', is_group=True,
                          counterpart_role='unclassified', messages=()), EMPTY_INPUTS)
        if hint['certain'] and hint['action'] != 'ignore':
            kind = hint['suggested_party_kind']
    codes = sorted(party_name_parts(name)[1])
    return kind, codes


def broad_role(role):
    return 'supplier' if role in ('forwarder', 'vendor') else role


def name_update_label(value):
    if value is None:
        return None
    try:
        return '名称更新于 ' + datetime.fromtimestamp(value, timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
    except (OverflowError, OSError, ValueError):
        return None


def _source_error(error):
    """Fixed audit-only classifications; never retain an exception or its text."""
    if isinstance(error, FileNotFoundError):
        return 'missing'
    if isinstance(error, PermissionError):
        return 'permission'
    if (isinstance(error, TimeoutError) or isinstance(error, sqlite3.OperationalError) and
            (getattr(error, 'sqlite_errorcode', None) == sqlite3.SQLITE_INTERRUPT or str(error) == 'interrupted')):
        return 'timeout'
    if isinstance(error, sqlite3.DatabaseError):
        return 'database'
    if isinstance(error, (ValueError, TypeError, KeyError, AttributeError)):
        return 'format'
    return 'unavailable'


class WechatDirectory:
    def __init__(self, *, discovery_path=None, config_path=None, state_path=None, cache_path=None,
                 discovery_reader=read_discovery, config_reader=read_config, state_reader=read_state,
                 conversation_reader=None, clock=None, contact_fetcher=None, refresh_timeout=25,
                 readonly_client=None, readonly_client_factory=None,
                 include_roles=('customer', 'supplier', 'unclassified')):
        # An explicit test source isolates every unspecified source. A cache-only
        # override (rehearsal) deliberately keeps the real, read-only sources.
        overrides = ((discovery_path, DISCOVERY_PATH), (config_path, CONFIG_PATH), (state_path, STATE_PATH))
        root = next((Path(p).parent for p, default in overrides if p is not None and Path(p) != default), None)
        def source(value, default, local):
            return Path(value) if value is not None else root / local if root else default
        self.discovery_path = source(discovery_path, DISCOVERY_PATH, 'disabled-discovery.sqlite3')
        self.config_path = source(config_path, CONFIG_PATH, 'disabled-api-config.json')
        self.state_path = source(state_path, STATE_PATH, 'disabled-api-state.sqlite3')
        self.cache_path = source(cache_path, CACHE_PATH, 'wechat-directory.sqlite3')
        protected = (self.discovery_path, self.config_path, self.state_path, DISCOVERY_PATH, CONFIG_PATH, STATE_PATH)
        if any(self.cache_path.resolve() == p.resolve() or (self.cache_path.exists() and p.exists()
                   and self.cache_path.samefile(p)) for p in protected):
            raise ValueError('通讯录缓存必须独立于来源文件')
        self.clock = clock or time.time
        self.read_discovery, self.read_config, self.read_state = discovery_reader, config_reader, state_reader
        self.include_roles = frozenset(include_roles)
        if not self.include_roles <= ROLES:
            raise ValueError('群角色无效')
        self.adapter = WechatAdapter(self.state_path, include_roles=ROLES, media_tokens=False)
        self.read_conversations = conversation_reader or self._conversations
        self.cache = RefreshCache(self.cache_path, clock=self.clock, contact_fetcher=contact_fetcher, timeout=refresh_timeout,
                                  readonly_client=readonly_client, readonly_client_factory=readonly_client_factory,
                                  group_candidates=self._refresh_candidates)

    def _refresh_candidates(self):
        """Names only: do not read bodies, invoke discovery workers or enroll IDs."""
        notes = []
        cached = self.cache.read()
        identities = {r['id'] for r in cached if r['kind'] == 'group'}
        try:
            identities.update(r['id'] for r in self.read_discovery(self.discovery_path) if r['kind'] == 'group')
        except Exception:
            notes.append('本地发现库暂不可读，待补名群可能不完整')
        try:
            config = self.read_config(self.config_path)
        except Exception:
            config = {}
            notes.append('群采集登记暂不可读，本次无法排除已有配置名称的群')
        named = {r['id'] for r in cached if cached_name(r)}
        named.update(i for i, group in config.items() if text(group.get('group_name')))
        return sorted(identities - named), notes

    def _conversations(self, path, now):
        convs = self.adapter.list_conversations(now)
        if not self.adapter.ready:
            raise ValueError('收件箱投影不可用')
        return {decode_key(c['key'], 'conversation')[2]:
                {key: c[key] for key in ('buyer_name', 'counterpart_role')} for c in convs.values()}

    def audit_snapshot(self, now=None, *, database=read_database):
        """Reuse enrollment/naming rules without loading the inbox or media tokens.

        Roles in this projection come from grouped source metadata. Callers must
        not use its visibility fields as proof of an inbox-valid conversation.
        """
        return self._snapshot(now=now, audit_database=database)

    def _snapshot(self, *, now=None, audit_database=None):
        now, notes, available = self.clock() if now is None else now, [], {}
        source_errors = {}
        def read(key, fn, fallback, reason):
            try:
                result = fn()
                available[key] = True
                return result
            except Exception as error:
                available[key] = False
                if audit_database is not None:
                    source_errors[key] = _source_error(error)
                notes.append(reason)
                return fallback
        discovered = read('discovery', lambda: read_discovery(self.discovery_path, database=audit_database)
                          if audit_database and self.read_discovery is read_discovery else self.read_discovery(self.discovery_path),
                          [], '本地通讯录发现库暂不可读，发现数量可能不完整')
        config = read('config', lambda: self.read_config(self.config_path), {}, '群采集登记暂不可读，接入状态无法确认')
        state = read('messages', lambda: read_state(self.state_path, now, database=audit_database, metadata_fast=True)
                     if audit_database and self.read_state is read_state else self.read_state(self.state_path, now),
                     dict(messages={}, names={}), '消息归档暂不可读，不能判断是否收到消息')
        convs = read('inbox', lambda: read_role_metadata(self.state_path, database=audit_database) if audit_database
                     else self.read_conversations(self.state_path, now), {}, '收件箱投影暂不可读，无法确认可见性')
        cached = read('refresh_cache', lambda: self.cache.read(database=audit_database) if audit_database else self.cache.read(),
                      [], '独立通讯录缓存暂不可读，仍显示其他本地来源')
        cached_by_id = {row['id']: row for row in cached}
        identities = {}
        def add(identity, kind, origin, first=None, last=None):
            if not text(identity) or kind not in KINDS:
                return
            entry = identities.setdefault(identity, dict(id=identity, kind=kind, sources=set(), first_seen=None, last_seen=None))
            entry['sources'].add(origin)
            for field, value, choose in (('first_seen', first, min), ('last_seen', last, max)):
                value = timestamp(value)
                if value is not None:
                    entry[field] = value if entry[field] is None else choose(value, entry[field])
        for origin, records in (('discovery', discovered), ('refresh_cache', cached)):
            for row in records:
                add(row['id'], row['kind'], origin, row.get('first_seen'), row.get('last_seen'))
        for identity in config:
            add(identity, 'group', 'config')
        for identity, stats in state['messages'].items():
            add(identity, identity_kind(identity), 'messages', stats['first_message_at'], stats['last_message_at'])
        rows = []
        for identity, entry in identities.items():
            setting, conv = config.get(identity), convs.get(identity)
            is_group = entry['kind'] == 'group'
            configured_name = text(setting.get('group_name')) if setting else None
            observed_name = text(conv.get('buyer_name')) if conv else None
            observed_name = observed_name if observed_name and not placeholder_name(observed_name) else text(state['names'].get(identity))
            if observed_name and placeholder_name(observed_name):
                observed_name = None
            metadata = cached_by_id.get(identity, {})
            refreshed_name = cached_name(metadata)
            name = configured_name or refreshed_name or observed_name or {'group': '未命名群', 'private': '未命名联系人', 'official': '未命名公众号'}[entry['kind']]
            name_at = timestamp(metadata.get('name_updated_at')) if refreshed_name and not configured_name else None
            name_label = name_update_label(name_at)
            if name_label is None:
                name_at = None
            named, codes = naming(name)
            config_role = setting['business_role'] if setting else None
            counterpart_role = conv['counterpart_role'] if conv else None
            role = config_role or (counterpart_role if counterpart_role not in (None, 'unclassified', 'unknown') else broad_role(named)) or 'unclassified'
            conflict = (len(codes) > 1 or bool(named and config_role not in (None, 'unknown', 'unclassified') and broad_role(named) != config_role))
            stats = state['messages'].get(identity, dict(n_messages=0, first_message_at=None, last_message_at=None))
            count = stats['n_messages'] if available['messages'] and is_group else None
            last = stats['last_message_at'] if available['messages'] and is_group else None
            registered = identity in config if available['config'] and is_group else None
            enabled = setting.get('enabled') if setting else None
            collect_state = ('not_enrolled' if registered is False else 'enrolled_no_messages' if registered and count == 0 else
                             'collecting' if registered and stats.get('recent_messages', 0) > 0 else
                             'enrolled_silent' if registered and count and last is not None and last <= now else None)
            if registered and enabled is not True:
                collect_state = None
            reason = ('当前只核对群采集登记，私聊和公众号的采集授权未核验' if not is_group else
                      '已登记，但群启用标志为关闭；不能据此确认仍在采集，内部群可能使用独立工作入口' if enabled is False else
                      '采集登记、启用标记或消息时间无法确认' if collect_state is None else
                      '该群尚未登记群采集；历史消息仍可能保留' if not registered else
                      '已登记群采集；状态按已有消息判断，不代表在线或历史完整')
            visible, with_suppliers, visibility = self._visibility(entry['kind'], conv, count, available['inbox'])
            rows.append(dict(entry, sources=sorted(entry['sources']), name=name,
                name_source='config' if configured_name else 'refresh_cache' if refreshed_name else 'conversation' if observed_name else 'unknown',
                nickname=text(metadata.get('nickname'), 128), remark=text(metadata.get('remark'), 128),
                name_updated_at=name_at, name_update_label=name_label,
                in_directory=bool(entry['sources'] & {'discovery', 'refresh_cache'}), registered=registered, enabled=enabled,
                auto_collection_source=setting.get('auto_collection_source') if setting else None,
                collect_state=collect_state, collect_state_reason=reason,
                config_role=config_role, named_role=named, named_role_label=ROLE_LABELS.get(named), name_codes=codes,
                named_role_hint=('像是' + ROLE_LABELS[named] + ('群' if is_group else '联系人') + '（按名称识别）') if named in ROLE_LABELS else None,
                role=role, counterpart_role=counterpart_role, role_conflict=conflict,
                role_conflict_reason=('名称同时含 SUP 和 CUS，请人工核对' if len(codes) > 1 else
                    f'配置为{ROLE_LABELS.get(config_role, "未知")}，名称规则识别为{ROLE_LABELS.get(named, "未知")}，请人工核对') if conflict else None,
                n_messages=count, has_messages=count > 0 if count is not None else None, last_message_at=last,
                message_scope='群消息归档' if is_group else '当前仅核对群消息归档，私聊与公众号消息数量未核验',
                inbox_visible=visible, inbox_visible_with_suppliers=with_suppliers, inbox_visibility_reason=visibility))
        rows.sort(key=lambda row: (-(row['last_seen'] or 0), row['id']))
        latest_name_at = max((row['name_updated_at'] for row in rows if row['name_updated_at'] is not None), default=None)
        result = dict(ready=available['config'] and available['messages'] and (available['discovery'] or bool(cached)),
                      generated_at=now, sources=available, notes=notes, items=rows,
                      name_updated_at=latest_name_at, name_update_label=name_update_label(latest_name_at))
        if audit_database is not None:
            result['source_errors'] = source_errors
        return result

    def _visibility(self, kind, conv, count, ready):
        if kind != 'group':
            return False, False, '当前微信收件箱只支持群聊，私聊和公众号不展示'
        if not ready:
            return None, None, '收件箱数据暂不可读，无法确认是否可见'
        if conv is None:
            return False, False, '尚无收到的消息，收件箱不会生成会话' if count == 0 else '归档中有记录，但尚无符合收件箱规则的有效会话'
        role = conv['counterpart_role']
        if role not in self.include_roles:
            return False, False, f'消息分类为{ROLE_LABELS.get(role, "未知")}，当前收件箱未纳入这类群；打开供应商开关也不会显示'
        if role == 'supplier':
            return False, True, '供应商群默认隐藏，打开“同时显示供应商群”后可见'
        if role not in ('customer', 'unclassified'):
            return False, True, '该类群已由宿主纳入会话投影，但默认客户列表隐藏；选择全部角色后可见'
        return True, True, '已在默认收件箱显示；具体搜索、待回复筛选仍可能隐藏此会话'

    def list(self, values=None):
        values = {**dict(kind='group', state='', q='', page=1, page_size=50), **(values or {})}
        result = self._snapshot()
        query = values['q'].casefold()
        rows = [row for row in result.pop('items') if (values['kind'] in ('', 'all') or row['kind'] == values['kind'])
                and (not values['state'] or row['collect_state'] == values['state'])
                and (not query or query in row['name'].casefold() or query in row['id'].casefold())]
        start = (values['page'] - 1) * values['page_size']
        return dict(result, items=rows[start:start + values['page_size']], total=len(rows), page=values['page'], page_size=values['page_size'])

    def summary(self):
        result = self._snapshot()
        rows = result.pop('items')
        groups = [row for row in rows if row['kind'] == 'group']
        directory_groups = [row for row in groups if row['in_directory']]
        def counts(items):
            by_state = Counter(row['collect_state'] or 'unknown' for row in items)
            by_role = Counter(row['role'] for row in items)
            return dict(total=len(items), by_state={key: by_state[key] for key in (*STATES, 'unknown')},
                        by_role={key: by_role[key] for key in ('supplier', 'customer', 'learning', 'internal', 'unclassified', 'unknown')},
                        conflicts=sum(row['role_conflict'] for row in items))
        registered = sum(row['registered'] is True for row in groups)
        directory_registered = sum(row['registered'] is True for row in directory_groups)
        not_enrolled = sum(row['registered'] is False for row in directory_groups)
        no_messages = sum(row['registered'] is False and row['n_messages'] == 0 for row in directory_groups)
        suspected = Counter(broad_role(row['named_role']) for row in groups if row['registered'] is False)
        conclusion = (f'通讯录已发现 {len(directory_groups)} 个群，其中 {directory_registered} 个已登记采集，'
                      f'{not_enrolled} 个尚未登记；未登记群中 {no_messages} 个从未收到消息，系统看不到它们的消息。'
                      f'全部来源合并共 {len(groups)} 个群，登记采集共 {registered} 个。')
        if not result['sources']['config'] or not result['sources']['messages']:
            conclusion = f'目前合并发现 {len(groups)} 个群；采集登记或消息归档暂不可读，覆盖数量无法完整确认。'
        elif not result['sources']['discovery']:
            conclusion = '本地发现库暂不可读，以下数量仅来自可用来源。' + conclusion
        return dict(result, **counts(rows), by_kind={kind: sum(row['kind'] == kind for row in rows) for kind in KINDS},
                    groups=dict(counts(groups), directory_total=len(directory_groups), registered=registered,
                        directory_registered=directory_registered, directory_not_enrolled=not_enrolled,
                        outside_directory=len(groups) - len(directory_groups), not_enrolled_no_messages=no_messages,
                        disabled_registered=sum(row['registered'] is True and row['enabled'] is False for row in groups)),
                    not_enrolled_suspected={role: suspected[role] for role in ('supplier', 'customer')}, conclusion=conclusion)

    def refresh(self):
        return self.cache.refresh()
