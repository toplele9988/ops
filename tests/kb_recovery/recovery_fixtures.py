"""隔离导入真实源码补丁；仅虚构证据和假 runner 可以进入演练。"""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import shutil
import sqlite3
import sys
from types import ModuleType, SimpleNamespace
import uuid

ROOT = Path(__file__).resolve().parents[2]


def load_modules(tmp_path):
    spec = importlib.util.spec_from_file_location('kb_patch_verify', ROOT / 'deploy/kb/verify_patch.py')
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)
    prepared = verifier.prepare()
    code = tmp_path / ('code-' + uuid.uuid4().hex)
    shutil.move(str(prepared), code)
    if '/opt/imgw/wechat' not in sys.path:
        sys.path.append('/opt/imgw/wechat')
    package_name = '_kb_recovery_' + uuid.uuid4().hex
    package = ModuleType(package_name)
    package.__path__ = [str(code / 'video_knowledge'), '/opt/imgw/wechat/video_knowledge']
    sys.modules[package_name] = package
    modules = SimpleNamespace(code=code)
    for name in ('kb_unknown_isolation', 'quota_isolation', 'kb_source_gate', 'kb_model_runtime',
                 'kb_extract', 'kb_profile', 'kb_live'):
        spec = importlib.util.spec_from_file_location(package_name + '.' + name, code / 'video_knowledge' / (name + '.py'))
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        setattr(package, name, module)
        spec.loader.exec_module(module)
        setattr(modules, name, module)
    return modules


def live_fixture(tmp_path, monkeypatch):
    from test_isolation import synthetic_holds
    modules = load_modules(tmp_path)
    frozen = synthetic_holds(modules, tmp_path)
    runtime = modules.kb_model_runtime
    frozen.db.row_factory = sqlite3.Row
    manifest = modules.kb_unknown_isolation.capture(frozen.db, frozen.ledger, frozen.matrix)
    frozen.db.close()
    held_rows = sqlite3.connect(frozen.path).execute('SELECT * FROM calls ORDER BY cache_key').fetchall()
    modules.quota_isolation.install(frozen.matrix, manifest)
    frozen.matrix.models = tuple(frozen.ledger['models']) + ('fictional-ready-2',)
    frozen.matrix.select_model_readonly = lambda prompt, schema, **kw: {
        'model': (None if kw.get('preferred_model') == 'fictional-exhausted'
                  else kw.get('preferred_model') or 'fictional-ready'), 'reservation_tokens': 8000}
    requests = []
    facts = []

    def complete(prompt, schema, model):
        requests.append((runtime.sha(prompt), model))
        return {'content': json.dumps({'facts': facts}), 'metadata': {
            'requested_model': model, 'actual_model': model,
            'usage': {'prompt_tokens': 10, 'completion_tokens': 2, 'total_tokens': 12}}}

    policy = {'root': str(frozen.path.parent), 'model_codes': frozen.matrix.models,
              'schema': 'wechat-kb-model-runtime-v1', 'calls_per_day': 1000000,
              'tokens_per_day': 1000000000}
    matrix = runtime.Matrix(policy, frozen.matrix, complete=complete, now=lambda: 10000,
                            isolation_enabled=True, manifest=manifest)
    quarantine = synthetic_quarantine(modules, manifest)
    quarantine_path = tmp_path / 'fictional-source-quarantine.json'
    quarantine_path.write_text(json.dumps(quarantine))
    quarantine_path.chmod(0o600)
    original_reader = runtime.restricted
    def fixture_reader(path, *, owner, **kwargs):
        if path == quarantine_path:
            assert owner == 0
            return original_reader(path, owner=__import__('os').geteuid(), **kwargs)
        return original_reader(path, owner=owner, **kwargs)
    monkeypatch.setattr(runtime, 'restricted', fixture_reader)
    monkeypatch.setattr(runtime, 'SOURCE_QUARANTINE', quarantine_path)
    matrix.source_quarantine_sha256 = runtime.sha(quarantine_path.read_bytes())
    matrix.quarantine_preflight = matrix.quarantine_preflight_live
    recovery_dir = tmp_path / 'recovery-state'
    recovery_dir.mkdir(mode=0o700)
    monkeypatch.setattr(modules.kb_live, 'RECOVERY_DB', recovery_dir / 'recovery.sqlite3')
    db = sqlite3.connect(tmp_path / 'fictional-catalog.sqlite3')
    db.row_factory = sqlite3.Row
    modules.kb_live.initialize_live(db)
    evidence = []
    monkeypatch.setattr(modules.kb_live, 'kb_context', lambda *a: {'_supplier_context': {}, '_learning': {}})
    monkeypatch.setattr(modules.kb_live, 'kb_authorized', lambda db, ctx: list(evidence))
    monkeypatch.setattr(modules.kb_extract, 'kb_authorized', lambda db, ctx: list(evidence))
    monkeypatch.setattr(modules.kb_profile, 'flag_scope', lambda: 'all')
    monkeypatch.setattr(modules.kb_profile, 'rebuild', lambda *a, **kw: {})
    history = sys.modules[modules.kb_live.__package__ + '.history_backfill']
    classifier = __import__(modules.kb_live.__package__ + '.channel_classifier', fromlist=['SUP'])
    def joined(db, evidence_id):
        row = next(row for row in evidence if row['evidence_id'] == evidence_id)
        return {'channel_role': classifier.SUP, 'channel_id_sha256': runtime.sha(row['chatroom_id'])}
    monkeypatch.setattr(history, '_joined_evidence', joined)
    limiter = modules.kb_live.UnitLimiter(64, clock=lambda: 10000)

    def add(number, at, admitted=None, room='fictional-room', text=None):
        evidence.append({'evidence_id': 'fictional-evidence-' + str(number), 'chatroom_id': room,
                         'body_text': text or '虚构灯具参数说明' + str(number), 'price_kind': 'supplier_offer',
                         'source_created_at': at, 'received_at': at,
                         'created_at': at if admitted is None else admitted,
                         'source_kind': 'message', 'source_sha256': runtime.sha(str(number)),
                         'classification_id': 'fictional-classification',
                         'owner': 'fictional-owner', 'source_scope': 'fictional-scope'})

    def tick(now=10000):
        return modules.kb_live.tick(db, None, modules.kb_live.LiveConfig(None, 0), {}, now,
                                   matrix=matrix, limiter=limiter, recovery_after=1000)

    return SimpleNamespace(modules=modules, frozen=frozen, db=db, matrix=matrix,
                           requests=requests, facts=facts, add=add, evidence=evidence, tick=tick,
                           held_rows=held_rows, limiter=limiter, manifest=manifest)


def synthetic_quarantine(modules, manifest):
    sha = modules.kb_model_runtime.sha
    holds = [{'channel': 'wechat', 'request_sha256': row['checkpoint_sha256'],
              'prompt_sha256': row['prompt_sha256'],
              'source_keys': [sha('fictional-held-source-' + str(i))],
              'source_sha256': [sha('fictional-held-original-' + str(i))],
              'content_sha256': [sha('虚构未知资料' + str(i))]}
             for i, row in enumerate(manifest['holds'], 1)]
    holds.append({'channel': 'alibaba', 'request_sha256': sha('fictional-ali-request'),
                  'prompt_sha256': sha('fictional-ali-prompt'),
                  'source_keys': [sha('fictional-ali-source')],
                  'source_sha256': [sha('fictional-ali-original')],
                  'content_sha256': [sha('虚构阿里未知资料')]})
    return {'schema': 'kb-source-quarantine-v1', 'holds': holds}
