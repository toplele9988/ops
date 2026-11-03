"""首次审核的原授权入口；只在内存保留配置，不装配模型或生产写者。"""
from __future__ import annotations

import ast
from contextlib import contextmanager
import hashlib
import importlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
import time
from types import SimpleNamespace

import inventory_wechat
import prepare_install as p

HERE = Path(__file__).absolute().parent
WECHAT = Path('/opt/imgw/wechat')
ALIBABA = Path('/opt/imgw/scripts/icbu-conversation-analysis')
PROFILE = Path('/opt/imgw/wechat-kb-dual-live-v1/video_knowledge/kb_profile.py')
API_SHA = 'a52b7e93ed6ac577c25f78ee42c494915854b6ab052468e2e65148cd643e8367'
GRANT_SHA = 'af9a936b4709d84e43091f2f180d292de09c3835a6bff2cb2332cff86bd6e4c5'
READ_GRANT_SHA = '681c35e72a5a2421dc3d4d83c45497982d48e2874bc1a1b9b6b375c6c86b4713'
EMBEDDING_MODEL = 'BAAI/bge-m3@5617a9f61b028005a4858fdac845db406aefb181'
AUTH_BINDING_PATH = Path('/var/lib/imgw/whatsapp/messages.sqlite3')
EXPECTED_DENIALS = frozenset('business_history_' + suffix for suffix in (
    'receive_only_authority_required', 'current_binding_required', 'owner_scope_required',
    'current_grant_changed', 'marker_invalid', 'receipt_missing', 'source_changed',
    'body_changed', 'callback_missing', 'callback_hash_mismatch', 'callback_identity_mismatch',
    'callback_scope_mismatch', 'callback_timestamp_or_type_mismatch',
    'callback_message_id_mismatch'))
ALIBABA_DENIALS = frozenset({
    'test_event_excluded', 'not_archived_history', 'separate_pwa_source', 'account_not_registered'})


def need(ok, code):
    if not ok:
        raise RuntimeError(code)


def code(path, expected):
    path = Path(path)
    need(path.is_absolute() and '..' not in path.parts, 'AUTH_SOURCE_PATH')
    for item in (path, *path.parents):
        info = item.lstat()
        need(not stat.S_ISLNK(info.st_mode) and info.st_uid == 0
             and not info.st_mode & 0o022, 'AUTH_SOURCE_PATH')
    info = path.lstat()
    need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= 1048576,
         'AUTH_SOURCE_PATH')
    raw = path.read_bytes()
    need(hashlib.sha256(raw).hexdigest() == expected, 'AUTH_SOURCE_SHA')
    return raw


def original_function(raw, path, name, namespace):
    node = next(node for node in ast.parse(raw).body
                if isinstance(node, ast.FunctionDef) and node.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), namespace)
    return namespace[name]


@contextmanager
def load_originals():
    baseline = json.loads((HERE / 'coordinator-baseline.json').read_text())
    pins = baseline['production_source_sha256']
    names = {
        'history': ('video_knowledge.history_backfill', WECHAT / 'video_knowledge/history_backfill.py'),
        'business': ('video_knowledge.business_history', WECHAT / 'video_knowledge/business_history.py'),
        'classifier': ('video_knowledge.channel_classifier', WECHAT / 'video_knowledge/channel_classifier.py'),
        'policy': ('video_knowledge.assistant_policy', WECHAT / 'video_knowledge/assistant_policy.py'),
        'grant': ('video_knowledge.whatsapp_grant', WECHAT / 'video_knowledge/whatsapp_grant.py'),
        'collection': ('collection_policy', WECHAT / 'collection_policy.py'),
        'api': ('api_groupbot', WECHAT / 'api_groupbot.py'),
        'core': ('analysis_core', ALIBABA / 'analysis_core.py'),
        'runtime': ('analysis_runtime', ALIBABA / 'analysis_runtime.py'),
        'freeze': ('source_freeze', ALIBABA / 'source_freeze.py'),
    }
    checked = {path: pins[str(path)] for _, path in names.values() if str(path) in pins}
    for name in ('business_access', 'cpq_batch', 'cpq_sync', 'cpq_windows', 'kb_normalize'):
        path = WECHAT / ('video_knowledge/' + name + '.py')
        checked[path] = pins[str(path)]
    for name in ('collection_policy.py', 'group_lifecycle.py', 'retrieval/local_cli.py'):
        path = WECHAT / name
        checked[path] = pins[str(path)]
    checked[WECHAT / 'api_groupbot.py'] = API_SHA
    checked[WECHAT / 'video_knowledge/whatsapp_grant.py'] = GRANT_SHA
    checked[WECHAT / 'retrieval/read_grant_provider.py'] = READ_GRANT_SHA
    checked[inventory_wechat.EXTRACT] = inventory_wechat.EXTRACT_SHA
    checked[Path('/opt/imgw/scripts/icbu_history.py')] = pins['/opt/imgw/scripts/icbu_history.py']
    checked[PROFILE] = baseline['installed_overlay_sha256'][str(PROFILE)]
    for path, pin in checked.items():
        code(path, pin)
    old_path = list(sys.path)
    sys.path[:0] = [str(WECHAT), '/opt/imgw/scripts', str(ALIBABA)]
    try:
        modules = {}
        for name, (module_name, path) in names.items():
            module = importlib.import_module(module_name)
            need(Path(module.__file__).absolute() == path, 'AUTH_IMPORT_PATH')
            modules[name] = module
        history = modules['runtime'].history_module()
        need(Path(history.__file__).absolute() == Path('/opt/imgw/scripts/icbu_history.py'),
             'AUTH_IMPORT_PATH')
        modules['alibaba_history'] = history
        modules['flag_scope'] = original_function(code(PROFILE, checked[PROFILE]), PROFILE,
            'flag_scope', {'Path': Path, 'stat': stat, 'json': json,
                           'FLAG': Path('/etc/imgw/kb-build.json')})
        algorithms = inventory_wechat.algorithms()
        raw = code(inventory_wechat.EXTRACT, inventory_wechat.EXTRACT_SHA)
        original_function(raw, inventory_wechat.EXTRACT, 'scope_chatrooms', algorithms)
        modules['extract'] = SimpleNamespace(**algorithms)
        modules['pins'] = checked
        yield SimpleNamespace(**modules)
    finally:
        sys.path[:] = old_path


@contextmanager
def sticky_exceptions(functions):
    """原入口会吞部分异常；只容许源码中明确的正常授权拒绝。"""
    watched = {function.__code__ for function in functions}
    failed = []
    previous = sys.gettrace()

    def trace(frame, event, arg):
        if event == 'exception' and frame.f_code in watched:
            error = arg[1]
            if (not isinstance(error, (StopIteration, GeneratorExit))
                    and not (type(error) is ValueError and len(error.args) == 1
                             and isinstance(error.args[0], str)
                             and error.args[0] in EXPECTED_DENIALS)):
                failed.append(True)
        return trace

    sys.settrace(trace)
    try:
        yield
        need(not failed, 'AUTH_WECHAT_EXCEPTION')
    finally:
        sys.settrace(previous)


def watched_functions(modules):
    names = {
        'history': ('eligible', 'kb_context', 'kb_authorized', 'learning_fact_allowed'),
        'business': ('_registration', '_original', '_schema', 'verified_evidence'),
        'classifier': ('current_registration', 'registration', 'safe_body'),
        'policy': ('authority', 'read_policy'),
        'api': ('load_config', '_validate_config'),
        'grant': ('owner_pairs', '_owner_pairs'),
        'collection': ('public_selection', 'group_view'),
    }
    return [function for module, attributes in names.items()
            for name in attributes
            for function in [getattr(getattr(modules, module, None), name, None)]
            if hasattr(function, '__code__')]


def binding_metadata(path):
    need(not any(item.is_symlink() for item in (path, *path.parents)), 'AUTH_BINDING_PATH')
    result = {}
    for suffix in ('', '-wal', '-shm', '-journal'):
        item = Path(str(path) + suffix)
        if not item.exists() and not item.is_symlink():
            need(bool(suffix), 'AUTH_BINDING_MISSING')
            result[suffix] = None
            continue
        info = item.lstat()
        need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, 'AUTH_BINDING_PATH')
        need(suffix != '-journal' and (suffix != '-wal' or info.st_size <= 32),
             'AUTH_BINDING_NOT_QUIESCENT')
        result[suffix] = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
                          info.st_ctime_ns, info.st_uid, info.st_mode, info.st_nlink)
    return result


@contextmanager
def authorization_connections():
    """原可选 WA owner 授权只读 binding；拒绝忽略活跃 WAL 或改写 SHM。"""
    original = sqlite3.connect
    observed = []

    def connect(database, *args, **kwargs):
        binding = str(database) == 'file:' + str(AUTH_BINDING_PATH) + '?mode=ro'
        if binding:
            need(not args and kwargs.get('uri') is True
                 and set(kwargs) <= {'uri', 'timeout'}, 'AUTH_BINDING_CONNECTION')
            pin = binding_metadata(AUTH_BINDING_PATH)
            observed.append(pin)
            database = 'file:' + str(AUTH_BINDING_PATH) + '?mode=ro&immutable=1'
        connection = original(database, *args, **kwargs)
        if binding:
            # 原函数只查这四列；消息、正文和其他表不能经授权入口被读取。
            def authorize(action, table, column, database, trigger):
                allowed = action == sqlite3.SQLITE_SELECT or (
                    action == sqlite3.SQLITE_READ and table == 'binding'
                    and column in {'session', 'account_id', 'owner', 'scope'})
                return sqlite3.SQLITE_OK if allowed else sqlite3.SQLITE_DENY
            try:
                connection.set_authorizer(authorize)
            except BaseException:
                connection.close()
                raise
        return connection

    sqlite3.connect = connect
    try:
        yield
        for pin in observed:
            need(binding_metadata(AUTH_BINDING_PATH) == pin, 'AUTH_BINDING_CHANGED')
    finally:
        sqlite3.connect = original


class OriginalAuthorization:
    def __init__(self, modules, ws, als, after):
        self.modules, self.ws, self.als, self.after = modules, ws, als, after
        self.core, self.extract = modules.core, modules.extract
        self.config, self.policy = self.configuration()
        self.configuration_sha = p.b.digest([self.config, self.policy])
        self.scope = self.flag_scope()
        with sticky_exceptions(watched_functions(modules)):
            self.auth, _, self.policy_sha = modules.runtime.authority(modules.alibaba_history)
            need(isinstance(self.auth, dict), 'AUTH_ALIBABA_RESULT')
            modules.runtime.boundary(modules.alibaba_history, self.auth, self.policy_sha)
        frozen = p.read_json(Path(als) / 'source-freeze.json')
        self.frozen = modules.freeze.checked(frozen, modules.freeze.sha(frozen))
        self.now = time.time()

    def configuration(self):
        # 原 load_config 会修正权限；预检后才调用，权限不符直接停。
        path = Path(self.modules.history.CONFIG)
        need(not any(item.is_symlink() for item in (path, *path.parents)), 'AUTH_CONFIG_PATH')
        info = path.stat()
        need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
             and stat.S_IMODE(info.st_mode) == 0o600, 'AUTH_CONFIG_MODE')
        with sticky_exceptions(watched_functions(self.modules)):
            config = self.modules.api.load_config(path)
            policy = self.modules.policy.read_policy()
        need(type(config) is dict and type(policy) is dict, 'AUTH_CONFIG_RESULT')
        return config, policy

    def flag_scope(self):
        scope = self.modules.flag_scope()
        need(scope in ('pilot2', 'all'), 'AUTH_SCOPE_UNAVAILABLE')
        return scope

    def wechat_rows(self, db, state):
        m = self.modules
        with sticky_exceptions(watched_functions(m)):
            ctx = m.history.kb_context(state, self.config, self.policy, self.now)
            need(type(ctx) is dict and type(ctx.get('_learning')) is dict
                 and type(ctx.get('_supplier_context')) is dict, 'AUTH_WECHAT_RESULT')
            rows = [dict(row) for row in m.history.kb_authorized(db, ctx)]
        need(len({row['evidence_id'] for row in rows}) == len(rows), 'AUTH_WECHAT_RESULT')
        # 原 pilot2/all 按当前来源数量选群，不能拿旧盘点的群替代。
        namespace = m.extract.scope_chatrooms.__globals__
        namespace['kb_authorized'] = lambda database, context: iter(rows)
        allowed = set(m.extract.scope_chatrooms(db, ctx, self.scope))
        supplier = dict(ctx['_supplier_context'])
        supplier['channels'] = {room: name for room, name in supplier['channels'].items() if room in allowed}
        ctx = dict(ctx, channels=supplier['channels'], _supplier_context=supplier,
                   _learning={room: reg for room, reg in ctx['_learning'].items() if room in allowed})
        return m.extract, ctx, [row for row in rows if row['chatroom_id'] in allowed]

    def alibaba_record(self, raw_row):
        history = self.modules.alibaba_history
        try:
            record = history.source_record(raw_row, self.auth)
        except history.Rejected as error:
            if (len(error.args) == 1 and isinstance(error.args[0], str)
                    and error.args[0] in ALIBABA_DENIALS):
                return None
            raise RuntimeError('AUTH_ALIBABA_EXCEPTION') from None
        need(type(record) is dict, 'AUTH_ALIBABA_RESULT')
        return record

    def alibaba_groups(self, inbound_db, ledger_db):
        records, all_ids = [], set()
        for row in inbound_db.execute('SELECT id,payload,fingerprint,state,created_at FROM inbound ORDER BY created_at,id'):
            need(row['id'] not in all_ids, 'AUTH_ALIBABA_RESULT')
            all_ids.add(row['id'])
            record = self.alibaba_record(row)
            if record is not None:
                records.append(record)
            need(len(all_ids) <= self.modules.runtime.MAX_SOURCE_ROWS, 'AUTH_ALIBABA_BUDGET')
        admitted = {row['inbound_id'] for row in records}
        ledger = [dict(row) for row in ledger_db.execute('SELECT * FROM history_index')]
        need(len(ledger) <= self.modules.runtime.MAX_SOURCE_ROWS, 'AUTH_ALIBABA_BUDGET')
        indexed = [row['inbound_id'] for row in ledger if row['status'] == 'indexed']
        need(len(indexed) == len(set(indexed)) and set(indexed) <= all_ids, 'AUTH_ALIBABA_RECONCILE')
        # 已正常撤权的原来源不构成账本损坏；其余来源仍走原完整精确对账。
        groups, _ = self.core.collect_groups(records,
            [row for row in ledger if row['inbound_id'] in admitted], owner=self.auth['owner'],
            parser=self.modules.alibaba_history.PARSER, embedding_model=EMBEDDING_MODEL)
        result = {}
        for key, group in groups.items():
            try:
                self.modules.freeze.require_new_source(self.frozen, key, group['source_version'])
            except ValueError as error:
                if error.args == ('old_source_version_model_calls_frozen',):
                    continue
                raise RuntimeError('AUTH_ALIBABA_FREEZE') from None
            result[key] = group
        return result

    def recheck(self):
        need(p.b.digest(list(self.configuration())) == self.configuration_sha,
             'AUTH_CONFIGURATION_CHANGED')
        need(self.flag_scope() == self.scope, 'AUTH_SCOPE_CHANGED')
        with sticky_exceptions(watched_functions(self.modules)):
            self.modules.runtime.boundary(self.modules.alibaba_history, self.auth, self.policy_sha)
        for path, pin in self.modules.pins.items():
            code(path, pin)


@contextmanager
def original_authorization(ws, als, after):
    sys.dont_write_bytecode = True
    try:
        with load_originals() as modules, authorization_connections():
            original = OriginalAuthorization(modules, ws, als, after)
            yield original
            original.recheck()
    except RuntimeError as error:
        if len(error.args) == 1 and isinstance(error.args[0], str) and error.args[0].startswith('AUTH_'):
            raise
        raise RuntimeError('AUTH_RUNTIME_FAILED') from None
    except Exception:
        raise RuntimeError('AUTH_RUNTIME_FAILED') from None
