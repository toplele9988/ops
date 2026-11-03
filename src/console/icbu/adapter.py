"""Safe adapter around the legacy Alibaba draft operations.

The legacy writer is deliberately used only for explicitly submitted POSTs.
Reads of its operation database use SQLite read-only mode because the legacy
``connect()`` helper runs schema DDL even when called by ``list_commands``.
"""
from __future__ import annotations

import hashlib
import hmac
import importlib.util
import json
import math
import re
import sqlite3
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

from console.inbox.model import make_key


LEGACY_OPERATIONS = Path('/opt/imgw/scripts/icbu_operations.py')
LEGACY_READONLY = Path('/opt/imgw/wechat/console/operations_readonly.py')
VALIDATION_PATH = Path('/var/lib/imgw/icbu/phase8-draft-validation.json')
UUID = re.compile(r'[a-fA-F0-9]{8}-(?:[a-fA-F0-9]{4}-){3}[a-fA-F0-9]{12}\Z')
TM_CONVERSATION = re.compile(r'tm:[A-Za-z0-9_-]{1,128}\Z')
SAFE_COMMAND_STATES = ('queued', 'claimed', 'filled', 'sent', 'blocked', 'unknown', 'expired', 'cancelled')
SAFE_DRAFT_STATES = ('draft_queued', 'draft_processing', 'draft_waiting', 'draft_hold', 'draft_ready', 'failed')
SAFE_PROBE_COUNTERS = ('contacts_scanned', 'contacts_loaded', 'queued', 'blocked', 'unbound', 'metadata_skipped')


class BadRequest(ValueError):
    """A safe, user-facing validation error."""


class Missing(LookupError):
    """The requested legacy draft does not exist."""


class Conflict(ValueError):
    """The draft or operation state changed during review."""


class Disabled(RuntimeError):
    """Writes are disabled by the host (for example, in rehearsal)."""


def digest(value: str) -> str:
    """Match icbu_operations.digest: SHA-256 of the UTF-8 text."""
    return hashlib.sha256(value.encode()).hexdigest()


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def _load_file(name: str, path: Path):
    if not path.is_file():
        raise RuntimeError('legacy_source_unavailable')
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError('legacy_source_unavailable')
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(name, None)
        raise
    return module


def _readonly_connection(path):
    target = Path(path)
    if not target.is_file():
        return None
    connection = sqlite3.connect(target.resolve().as_uri() + '?mode=ro', uri=True, timeout=1)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA query_only=ON')
    connection.execute('PRAGMA busy_timeout=1000')
    connection.execute('BEGIN')
    return connection


def _human_pause_ack(row):
    """Keep the legacy 0.3.5.5 retry fence without returning its raw receipt."""
    if row['state'] != 'blocked' or not row['client_id'] or not row['lease_token']:
        return False
    try:
        result = json.loads(row['result_json'])
    except (TypeError, ValueError):
        return False
    if not isinstance(result, dict) or set(result) != {
            'account', 'id', 'client_id', 'lease_token', 'state', 'platform_message_id', 'detail'}:
        return False
    return (result.get('account') == row['account'] == 'Amy'
            and result.get('id') == row['id']
            and result.get('client_id') == row['client_id']
            and isinstance(result.get('lease_token'), str)
            and hmac.compare_digest(result['lease_token'], row['lease_token'])
            and result.get('state') == 'blocked'
            and result.get('detail') == 'operations_user_active'
            and result.get('platform_message_id') == '')


def _read_commands(operations, inbound_id=None):
    connection = _readonly_connection(operations.db)
    if connection is None:
        return []
    try:
        if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='commands'").fetchone():
            return []
        if inbound_id is None:
            rows = connection.execute('SELECT * FROM commands ORDER BY rowid DESC LIMIT 50').fetchall()
        else:
            rows = connection.execute('SELECT * FROM commands WHERE inbound_id=? ORDER BY rowid DESC LIMIT 50',
                                      (inbound_id,)).fetchall()
        result = []
        for row in rows:
            latest = connection.execute('''SELECT id FROM commands WHERE inbound_id=? AND action=?
                AND text_sha256=? ORDER BY rowid DESC LIMIT 1''',
                (row['inbound_id'], row['action'], row['text_sha256'])).fetchone()
            result.append({
                'id': row['id'], 'inbound_id': row['inbound_id'], 'action': row['action'],
                'origin': row['origin'], 'state': row['state'], 'created_at': row['created_at'],
                'claimed_at': row['claimed_at'], 'finished_at': row['finished_at'],
                'result_json': row['result_json'],
                'human_pause_retry_available': bool(latest and latest['id'] == row['id']
                                                    and _human_pause_ack(row)),
            })
        return result
    finally:
        connection.rollback()
        connection.close()


def _read_command_counts(operations):
    counts = {state: 0 for state in SAFE_COMMAND_STATES}
    connection = _readonly_connection(operations.db)
    if connection is None:
        return {'available': False, 'total': 0, 'states': counts, 'other': 0, 'clients_seen': 0}
    try:
        exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='commands'").fetchone()
        if not exists:
            return {'available': False, 'total': 0, 'states': counts, 'other': 0, 'clients_seen': 0}
        rows = connection.execute('SELECT state,count(*) AS n FROM commands GROUP BY state').fetchall()
        other = 0
        for row in rows:
            if row['state'] in counts:
                counts[row['state']] = row['n']
            else:
                other += row['n']
        total = sum(counts.values()) + other
        clients = connection.execute("SELECT count(DISTINCT client_id) FROM commands WHERE client_id IS NOT NULL AND client_id!=''").fetchone()[0]
        return {'available': True, 'total': total, 'states': counts, 'other': other, 'clients_seen': clients}
    finally:
        connection.rollback()
        connection.close()


def _read_configuration(operations):
    """Read exactly the legacy configuration projection without creating tables."""
    policy_path = Path(operations.policy)
    policy = json.loads(policy_path.read_text())
    result = {'mode': policy.get('operations_mode', 'review'),
              'conversations': policy.get('auto_send_conversations', [])}
    connection = _readonly_connection(operations.db)
    if connection is None:
        return result
    try:
        if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='settings'").fetchone():
            return result
        row = connection.execute("SELECT value FROM settings WHERE key='operations'").fetchone()
        if row:
            override = json.loads(row[0])
            expected = digest(_canonical({key: policy.get(key) for key in
                ('auto_send', 'operations_mode', 'auto_send_conversations')}))
            if override.get('config_sha256') == expected:
                result.update({key: override[key] for key in ('mode', 'conversations') if key in override})
        return result
    finally:
        connection.rollback()
        connection.close()


def _stamp(value):
    if isinstance(value, (int, float)) and math.isfinite(value):
        return datetime.fromtimestamp(value, timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
    return value if isinstance(value, str) else '—'


def _safe_block_reason(command):
    if command.get('state') != 'blocked':
        return None
    try:
        detail = json.loads(command.get('result_json') or '{}').get('detail')
    except (TypeError, ValueError, AttributeError):
        detail = None
    if detail == 'operations_user_active':
        return '执行器检测到操作人员仍在操作；请重新核对草稿后人工批准'
    return '执行器标记为阻塞；请核对执行状态后再决定是否重试'


def _command_view(command):
    command_id = command.get('id')
    return {
        'id': command_id if isinstance(command_id, str) and UUID.fullmatch(command_id) else None,
        'action': command.get('action') if command.get('action') in ('fill', 'send') else 'other',
        'origin': command.get('origin') if command.get('origin') in ('owner_console', 'automatic') else 'other',
        'state': command.get('state') if command.get('state') in SAFE_COMMAND_STATES else 'unknown',
        'created_at': _stamp(command.get('created_at')),
        'claimed_at': _stamp(command.get('claimed_at')) if command.get('claimed_at') is not None else None,
        'finished_at': _stamp(command.get('finished_at')) if command.get('finished_at') is not None else None,
        'human_pause_retry_available': bool(command.get('human_pause_retry_available')),
        'blocked_reason': _safe_block_reason(command),
    }


def _updated_at(item, commands):
    if commands:
        current = commands[0]
        value = current.get('finished_at')
        if value is None:
            value = current.get('claimed_at')
        if value is None:
            value = current.get('created_at')
        return _stamp(value)
    return item.get('ready_at') or item.get('created_at') or '—'


class ICBUAdapter:
    """Injectable boundary between the JSON API and legacy ICBU sources."""

    def __init__(self, *, operations=None, readonly=None, command_reader=None,
                 command_counts_reader=None, settings_reader=None, validation_path=VALIDATION_PATH):
        if operations is None or readonly is None:
            if operations is None:
                legacy = _load_file('console._legacy_icbu_operations', LEGACY_OPERATIONS)
                operations = legacy.Operations()
            if readonly is None:
                readonly = _load_file('console._legacy_operations_readonly', LEGACY_READONLY)
        self.operations = operations
        self.readonly = readonly
        self.command_reader = command_reader or (lambda inbound_id: _read_commands(self.operations, inbound_id))
        self.command_counts_reader = command_counts_reader or (lambda: _read_command_counts(self.operations))
        self.settings_reader = settings_reader or (lambda: _read_configuration(self.operations))
        self.validation_path = Path(validation_path)
        self._configure_lock = threading.RLock()

    def _commands(self, inbound_id):
        return self.command_reader(inbound_id)

    def _detail(self, identifier):
        return self.readonly.draft_detail(identifier)

    def drafts(self, page):
        listing = self.readonly.draft_list(page)
        items = []
        for item in listing.get('items', []):
            identifier = item.get('id')
            detail = self._detail(identifier) if isinstance(identifier, str) else None
            account = item.get('account')
            conversation_id = detail.get('conversation_id') if isinstance(detail, dict) else None
            conversation_key = None
            if isinstance(account, str) and account and isinstance(conversation_id, str) and conversation_id:
                conversation_key = make_key('conversation', 'alibaba', account, conversation_id)
            commands = self._commands(identifier) if isinstance(identifier, str) else []
            latest = commands[0] if commands else None
            items.append({
                'id': identifier, 'account': account, 'buyer_name': item.get('buyer_name'),
                'country': item.get('country'), 'conversation_key': conversation_key,
                'state': item.get('state') if item.get('state') in SAFE_DRAFT_STATES else 'unknown', 'approved': bool(commands),
                'blocked': bool(latest and latest.get('state') == 'blocked'),
                'blocked_reason': _safe_block_reason(latest) if latest else None,
                'command_state': latest.get('state') if latest else None,
                'updated_at': _updated_at(item, commands),
            })
        raw_states = listing.get('states', {})
        states = {state: raw_states.get(state, 0) for state in SAFE_DRAFT_STATES
                  if isinstance(raw_states, dict) and type(raw_states.get(state, 0)) is int
                  and raw_states.get(state, 0) >= 0}
        return {
            'page': listing.get('page') if type(listing.get('page')) is int else page,
            'total': listing.get('total') if type(listing.get('total')) is int and listing.get('total') >= 0 else sum(states.values()),
            'has_next': listing.get('has_next') if type(listing.get('has_next')) is bool else False,
            'lane_ready': listing.get('lane_ready') if type(listing.get('lane_ready')) is bool else False,
            'states': states, 'items': items,
        }

    def draft(self, identifier):
        if not isinstance(identifier, str) or UUID.fullmatch(identifier) is None:
            raise Missing('未找到阿里草稿')
        item = self._detail(identifier)
        if item is None:
            raise Missing('未找到阿里草稿')
        commands = self._commands(identifier)
        account, conversation_id = item.get('account'), item.get('conversation_id')
        conversation_key = None
        if isinstance(account, str) and account and isinstance(conversation_id, str) and conversation_id:
            conversation_key = make_key('conversation', 'alibaba', account, conversation_id)
        current = item.get('draft') or ''
        latest = commands[0] if commands else None
        result = dict(item)
        if result.get('error_code'):
            result['error_code'] = '草稿生成失败，请核对执行状态'
        if result.get('state') not in SAFE_DRAFT_STATES:
            result['state'] = 'unknown'
        result.update({
            'draft_sha': digest(current), 'conversation_key': conversation_key,
            'approved': bool(commands), 'blocked': bool(latest and latest.get('state') == 'blocked'),
            'blocked_reason': _safe_block_reason(latest) if latest else None,
            'updated_at': _updated_at(item, commands),
            'commands': [_command_view(command) for command in commands],
        })
        return result

    def settings(self):
        value = self.settings_reader()
        mode, conversations = value.get('mode'), value.get('conversations')
        if (mode not in ('review', 'test_auto', 'auto') or not isinstance(conversations, list)
                or len(conversations) > 200):
            raise ValueError('settings_unavailable')
        if any(not isinstance(item, str) or TM_CONVERSATION.fullmatch(item) is None for item in conversations):
            raise ValueError('settings_unavailable')
        return {'mode': mode, 'conversations': conversations}

    def configure(self, mode, conversations):
        # The legacy method records its full settings object in mode_changed.
        # Preserve its settings write while keeping identifiers/configuration
        # values out of the event log.
        with self._configure_lock:
            namespace = getattr(self.operations, '__dict__', {})
            had_override = 'event' in namespace
            previous = namespace.get('event')
            original_event = getattr(self.operations, 'event', None)
            if callable(original_event) and hasattr(self.operations, '__dict__'):
                def safe_event(connection, kind, command=None, detail=None):
                    if kind == 'mode_changed':
                        detail = {'change': 'operations_settings_updated'}
                    return original_event(connection, kind, command, detail)
                self.operations.event = safe_event
            try:
                self.operations.configure(mode, conversations)
            finally:
                if callable(original_event) and hasattr(self.operations, '__dict__'):
                    if had_override:
                        self.operations.event = previous
                    else:
                        delattr(self.operations, 'event')
        return {'ok': True}

    def command(self, identifier, action, expected_sha, text):
        if not isinstance(identifier, str) or UUID.fullmatch(identifier) is None:
            raise Missing('未找到阿里草稿')
        if action not in ('fill', 'send'):
            raise Conflict('操作类型无效')
        item = self._detail(identifier)
        if item is None:
            raise Missing('未找到阿里草稿')
        if item.get('state') != 'draft_ready' or item.get('account') != 'Amy' or item.get('role') != 'buyer' or item.get('history') is not False:
            raise Conflict('草稿当前不可执行')
        draft_text = item.get('draft') or ''
        current_sha = digest(draft_text)
        if not isinstance(expected_sha, str) or not hmac.compare_digest(current_sha, expected_sha):
            raise Conflict('草稿内容已变化，请刷新后重新审核')
        if not isinstance(text, str):
            raise Conflict('执行文本无效')
        command_text = text.strip()
        if not 1 <= len(command_text) <= 6000 or '\x00' in command_text:
            raise Conflict('执行文本须为 1 至 6000 个字符')
        if not isinstance(item.get('conversation_id'), str) or TM_CONVERSATION.fullmatch(item['conversation_id']) is None:
            raise Conflict('会话标识未通过验证')
        # This is the sole call site for manual command enqueue. The legacy writer
        # repeats its inbound read and draft_sha comparison inside its transaction.
        try:
            command = self.operations.enqueue(identifier, action, expected_sha, text, retry_blocked=True)
        except ValueError as error:
            code = str(error)
            messages = {
                'draft_changed_refresh_required': '草稿内容已变化，请刷新后重新审核',
                'draft_not_ready_or_synthetic': '草稿当前不可执行',
                'draft_source_not_live_buyer': '草稿来源不符合实时买家会话要求',
                'conversation_unverified': '会话标识未通过验证',
                'invalid_command_action': '操作类型无效',
                'invalid_command_text': '执行文本须为 1 至 6000 个字符',
                'operation_schema_migration_required': '执行记录需要管理员处理，暂不能重试',
            }
            if code == 'draft_changed_refresh_required':
                raise Conflict(messages[code]) from None
            raise Conflict(messages.get(code, '阿里执行条件未通过，请刷新后核对')) from None
        if not isinstance(command, dict) or not isinstance(command.get('id'), str):
            raise RuntimeError('legacy_command_invalid')
        return {'ok': True, 'command': {'id': command['id'], 'state': command.get('state')}}

    def validation(self):
        reader = getattr(self.readonly, 'validation', None)
        if callable(reader):
            data = reader()
        else:
            if not self.validation_path.is_file():
                raise Missing('暂无合成草稿验收记录')
            if self.validation_path.stat().st_size > 1_000_000:
                raise ValueError('validation_unavailable')
            data = json.loads(self.validation_path.read_text())
        if data is None:
            raise Missing('暂无合成草稿验收记录')
        if not isinstance(data, dict) or data.get('synthetic') is not True or data.get('never_send') is not True:
            raise ValueError('validation_unavailable')
        result = data['result']
        metadata = data['metadata']
        item = {
            'id': 'synthetic-validation', 'account': 'Amy',
            'buyer_name': 'Phase 8 合成验收样例（未发送）', 'country': '',
            'state': 'draft_ready', 'created_at': _stamp(data['created_at']),
            'ready_at': _stamp(data['created_at']), 'provider': 'dashscope',
            'model': metadata['actual_model'], 'error_code': None,
            'conversation_id': '无真实收件人',
            'content': 'Do you have 380w moving head beam light? Need 16 pcs. Please quote packing and lead time.',
            'draft': result['draft'], 'sources': data['sources'],
            'review_notes': result['review_notes'], 'conversation_key': None,
            'approved': False, 'blocked': False, 'blocked_reason': None,
            'commands': [], 'draft_sha': digest(result['draft']),
        }
        return item

    def status(self, owner):
        overview = self.readonly.overview(owner)
        if not isinstance(overview, dict):
            raise ValueError('overview_unavailable')
        draft_result = overview.get('drafts') or {}
        draft_data = draft_result.get('data') if draft_result.get('ok') is True else None
        states = draft_data.get('states', {}) if isinstance(draft_data, dict) else {}
        safe_states = {key: states.get(key, 0) for key in SAFE_DRAFT_STATES
                       if type(states.get(key, 0)) is int and states.get(key, 0) >= 0}
        probe_result = overview.get('probe') or {}
        probe = probe_result.get('data') if probe_result.get('ok') is True else None
        safe_probe = None
        if isinstance(probe, dict):
            counters = probe.get('counters', {})
            safe_counters = {key: counters.get(key, 0) for key in SAFE_PROBE_COUNTERS
                             if type(counters.get(key, 0)) is int and counters.get(key, 0) >= 0}
            safe_probe = {
                'present': probe.get('present') is True,
                'freshness': probe.get('freshness') if probe.get('freshness') in ('online', 'stale', 'never_seen') else None,
                'state': probe.get('state') if probe.get('state') in ('starting', 'running', 'idle', 'paused') else None,
                'age_seconds': probe.get('age_seconds') if type(probe.get('age_seconds')) is int else None,
                'counters': safe_counters,
                'cycle_complete': probe.get('cycle_complete') if type(probe.get('cycle_complete')) is bool else None,
            }
        command_counts = self.command_counts_reader()
        raw_command_states = command_counts.get('states', {}) if isinstance(command_counts, dict) else {}
        safe_command_states = {state: raw_command_states.get(state, 0) for state in SAFE_COMMAND_STATES
                               if type(raw_command_states.get(state, 0)) is int and raw_command_states.get(state, 0) >= 0}
        executor = {
            'available': command_counts.get('available') if isinstance(command_counts, dict) and type(command_counts.get('available')) is bool else None,
            'total': command_counts.get('total', sum(safe_command_states.values())) if isinstance(command_counts, dict) else sum(safe_command_states.values()),
            'states': safe_command_states,
            'other': command_counts.get('other', 0) if isinstance(command_counts, dict) and type(command_counts.get('other', 0)) is int else 0,
            'clients_seen': command_counts.get('clients_seen', 0) if isinstance(command_counts, dict) and type(command_counts.get('clients_seen', 0)) is int else 0,
        }
        if type(executor['total']) is not int or executor['total'] < 0:
            executor['total'] = sum(executor['states'].values()) + executor['other']
        if executor['other'] < 0:
            executor['other'] = 0
        if executor['clients_seen'] < 0:
            executor['clients_seen'] = 0
        return {
            'observed_at': overview.get('observed_at'),
            'drafts': {'available': draft_result.get('ok') is True, 'states': safe_states,
                       'total': draft_data.get('total') if isinstance(draft_data, dict) and type(draft_data.get('total')) is int else sum(safe_states.values()),
                       'lane_ready': draft_data.get('lane_ready') if isinstance(draft_data, dict) and type(draft_data.get('lane_ready')) is bool else None},
            'executor': executor,
            'win10': safe_probe,
        }

