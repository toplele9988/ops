"""生产适配入口只接虚构来源与假 runner，不加载生产授权或凭据。"""
from contextlib import contextmanager
import importlib.util
import json
from pathlib import Path
import sqlite3
import shutil
from types import SimpleNamespace
import sys

import pytest

from recovery_fixtures import live_fixture

HERE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HERE / 'deploy/kb'))
import production_adapter as adapter


@pytest.fixture
def wechat(tmp_path, monkeypatch):
    f = live_fixture(tmp_path, monkeypatch)
    f.frozen.ledger['models']['fictional-ready-2'] = dict(f.frozen.ledger['models']['fictional-ready'])
    @contextmanager
    def context(*, writable=False):
        yield f.db, None, {'_supplier_context': {}, '_learning': {}}
    value = adapter.WechatAdapter(f.modules.kb_live, f.matrix, context,
        f.modules.kb_model_runtime.json.loads((tmp_path / 'fictional-source-quarantine.json').read_text()),
        models=('fictional-ready', 'fictional-ready-2'), clock=lambda: 10000, settle_seconds=0)
    return f, value


def test_wechat_each_callback_requests_exactly_one_reader(wechat):
    f, value = wechat
    f.add(1, 5000)
    item, = value.discover_live(1000)
    assert item['source_id'] == 'KL:fictional-evidence-1'
    assert value.read_source(item) == item
    plan = value.plan(item)
    replies = [value.request(item, reader['model'], reader['reservation']) for reader in plan]
    assert len(f.requests) == 2
    assert len({model for prompt, model in f.requests}) == 2
    value.apply_local(item, replies)
    assert value.discover_live(1000) == []
    assert len(f.requests) == 2


def test_source_alias_cannot_bypass_original_quarantine(wechat):
    f, value = wechat
    f.add(1, 5000)
    f.evidence[0]['source_sha256'] = value.quarantine['holds'][0]['source_sha256'][0]
    # 命中原未知隔离的新增来源被排除，不进入候选，也不请求模型。
    assert value.discover_live(1000) == []
    assert not f.requests


def test_completed_facts_still_rebuild_after_local_restart(wechat, monkeypatch):
    f, value = wechat
    f.add(1, 5000)
    item, = value.discover_live(1000)
    replies = [value.request(item, r['model'], r['reservation']) for r in value.plan(item)]
    rebuilds = []
    monkeypatch.setattr(f.modules.kb_profile, 'rebuild', lambda *a, **kw: rebuilds.append(kw['matrix']) or {})
    value.apply_local(item, replies)
    value.apply_local(item, replies)
    assert rebuilds == [None, None]
    assert len(f.requests) == 2


def test_frozen_reader_never_requests(wechat):
    f, value = wechat
    value.models = ('fictional-model-1', 'fictional-ready')
    with pytest.raises(ValueError, match='冻结'):
        value.preflight()
    assert not f.requests


def test_one_reader_cannot_become_complete(wechat):
    f, value = wechat
    f.add(1, 5000)
    item, = value.discover_live(1000)
    reply = value.request(item, 'fictional-ready', 65536)
    with pytest.raises(ValueError, match='两名'):
        value.apply_local(item, [reply])
    assert not f.db.execute('SELECT 1 FROM kb_segment').fetchone()


def core_module():
    spec = importlib.util.spec_from_file_location('_synthetic_ali_core', adapter.ALI_CORE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fictional_group(body='虚构灯具的历史要求'):
    core = core_module()
    record = core.normalize_record({'inbound_id': 'fictional_source', 'source_sha256': adapter.sha('fictional_source'),
        'account': 'fictional_account', 'conversation_id': 'fictional_conversation', 'role': 'buyer',
        'body': body, 'sent_at': '2026-10-31T00:00:00+00:00',
        'received_at': '2026-10-31T00:01:00+00:00', 'scope': 'fictional_scope'})
    group = {'key': core.group_key('fictional_owner', record['account'], record['conversation_id']),
             'owner': 'fictional_owner', 'account': record['account'], 'conversation_id': record['conversation_id'],
             'source_version': core.record_version([record]), 'records': [record]}
    return core, group, record


def test_alibaba_single_source_stable_when_parent_conversation_grows():
    core, group, record = fictional_group()
    first = adapter.single_alibaba_unit(core, group, record)
    group['records'].append(dict(record, inbound_id='fictional_other', source_sha256=adapter.sha('other')))
    group['source_version'] = core.record_version(group['records'])
    second = adapter.single_alibaba_unit(core, group, record)
    assert first == second
    assert first['window']['segments'][0]['char_end'] == len(record['body'])


def test_alibaba_multiple_windows_are_excluded():
    core, group, record = fictional_group('虚构' * 6000)
    with pytest.raises(ValueError, match='多个单元'):
        adapter.single_alibaba_unit(core, group, record)


def test_long_document_is_not_discovered_as_twelve_complete_chunks(wechat):
    f, value = wechat
    f.add(1, 5000, text='\n'.join('虚构规格' * 100 for _ in range(80)))
    assert value.discover_live(1000) == []
    assert f.requests == []


def test_adapter_hub_preserves_channel_stop_reason():
    class Blocked:
        def check_channel(self, item):
            raise adapter.ChannelBlocked('虚构原熔断')
    hub = adapter.AdapterHub({'alibaba': Blocked()}, {'fictional': True})
    assert hub.channel_status()['alibaba']['state'] == 'blocked'


def test_scope_restriction_reaches_original_supplier_context():
    original = {'channels': {'fictional_allowed': '甲', 'fictional_excluded': '乙'},
                '_supplier_context': {'channels': {'fictional_allowed': '甲', 'fictional_excluded': '乙'},
                                      'owner': 'fictional_owner', 'scopes': ['fictional_scope']},
                '_learning': {'fictional_learning': {'registered_business_role': 'learning'}}}
    narrowed = adapter.restrict_context(original, {'fictional_allowed'})
    assert narrowed['channels'] == narrowed['_supplier_context']['channels'] == {'fictional_allowed': '甲'}
    assert narrowed['_learning'] == {}
    assert original['_supplier_context']['channels'] == {'fictional_allowed': '甲', 'fictional_excluded': '乙'}


def test_reservation_must_cover_real_provider_payload(wechat):
    f, value = wechat
    f.add(1, 5000)
    item, = value.discover_live(1000)
    value.request_tokens = 4096
    with pytest.raises(ValueError, match='保守预留'):
        value.plan(item)
    assert not f.requests


def test_wechat_platform_alias_with_new_id_and_body_is_still_quarantined(wechat):
    f, value = wechat
    f.add(1, 5000)
    f.evidence[0]['message_id'] = 'fictional_same_platform_message'
    f.db.execute('CREATE TABLE channel_business_evidence('
        'evidence_id TEXT,body_text TEXT,source_sha256 TEXT,owner TEXT,source_scope TEXT,message_id TEXT)')
    f.db.execute('INSERT INTO channel_business_evidence VALUES(?,?,?,?,?,?)',
        ('fictional_old_unknown_id', '虚构原未知正文', value.quarantine['holds'][0]['source_sha256'][0],
         f.evidence[0]['owner'], f.evidence[0]['source_scope'], f.evidence[0]['message_id']))
    f.db.commit()
    assert value.discover_live(1000) == []
    assert f.requests == []


def test_authorized_plan_review_is_private_and_does_not_request(wechat, tmp_path):
    from history_fence import HistoryFence
    from backlog import make_manifest
    from plan_review import write_review
    f, value = wechat
    snapshot = f.matrix.root / 'fictional-approved-calls.sqlite3'
    shutil.copyfile(f.matrix.root / 'calls.sqlite3', snapshot)
    snapshot.chmod(0o600)
    value.history_fence = HistoryFence(snapshot, adapter.sha(snapshot.read_bytes()))
    f.add(1, 5000)
    hub = adapter.AdapterHub({'wechat': value}, value.quarantine)
    seed = {'after': 10000, 'snapshots': {'wechat_calls': adapter.sha(snapshot.read_bytes())}}
    summary = write_review(hub, seed, value.quarantine)
    output = Path(summary['私有目录'])
    try:
        assert output.stat().st_mode & 0o777 == 0o700
        assert (output / 'review.json').stat().st_mode & 0o777 == 0o600
        review = json.loads((output / 'review.json').read_text())
        manifest = make_manifest(review, value.quarantine)
        assert len(manifest['items']) == 1
        assert manifest['items'][0]['source_id'] == 'KL:fictional-evidence-1'
        assert summary['模型请求'] == 0 and f.requests == []
    finally:
        shutil.rmtree(output)


@pytest.fixture
def alibaba(tmp_path, monkeypatch):
    for fixed in (str(adapter.ALI_CORE.parent), '/opt/imgw/wechat'):
        if fixed not in sys.path:
            sys.path.append(fixed)
    import analysis_runtime
    import analysis_quota_pool
    from knowledge_engine.bge_engine import EmbeddingResult
    from supplier_knowledge.catalog import Catalog
    from test_backlog import quarantine
    core, group, record = fictional_group()
    record['message_id'] = 'fictional_platform_message'
    group['source_version'] = core.record_version(group['records'])
    private = tmp_path / 'fictional_ali'
    private.mkdir(mode=0o700)
    catalog_path = private / 'catalog.sqlite3'
    Catalog(catalog_path).close()
    analysis_path = private / 'analysis.sqlite3'
    with sqlite3.connect(analysis_path) as db:
        db.executescript('''CREATE TABLE provider_circuits(scope TEXT,model TEXT,state TEXT);
            CREATE TABLE analyses(version TEXT,group_json TEXT);
            CREATE TABLE windows(version TEXT,window_index INTEGER,state TEXT,plan_json TEXT);
            CREATE TABLE model_attempts(version TEXT,window_index INTEGER,attempt INTEGER,status TEXT);
            CREATE TABLE routing_audit(payload_json TEXT,event_sha256 TEXT);
            CREATE TABLE execution_attempts(version TEXT,window_index INTEGER,attempt INTEGER,prompt_sha256 TEXT);''')
    analysis_path.chmod(0o600)
    engine = SimpleNamespace(model_version='fictional-embedding',
        encode=lambda texts: [EmbeddingResult((1.0,) + (0.0,) * 1023, {}, 'fictional-embedding') for text in texts])
    calls = []
    def completion(prompt, schema, **kwargs):
        calls.append(kwargs)
        return reply()
    def reply():
        unit = adapter.single_alibaba_unit(core, group, record)
        return {'content': json.dumps({'schema_version': 3, 'claims': [
            {'category': 'requirement', 'topic': '虚构历史需求', 'statement': '买家曾提出虚构灯具要求。',
             'stance': 'request', 'evidence_ids': [unit['window']['evidence_spans'][0]['span_id']]}], 'gaps': []}),
            'metadata': {'provider': 'dashscope', 'requested_model': adapter.ALIBABA_MODEL,
                'actual_model': adapter.ALIBABA_MODEL, 'json_mode': 'json_object',
                'usage': {'prompt_tokens': 10, 'completion_tokens': 10, 'total_tokens': 20},
                'requested_output_tokens': 6000, 'reservation_tokens': 65536,
                'thinking_disabled_requested': True}}
    frozen_checks = []
    def frozen(frozen, key, version):
        frozen_checks.append((key, version))
        assert key == group['key'] and version == group['source_version']
    value = adapter.AlibabaAdapter(core, analysis_runtime, analysis_quota_pool,
        SimpleNamespace(select_model_readonly=lambda *a, **kw: {'model': adapter.ALIBABA_MODEL, 'reservation_tokens': 8000},
                        completion=completion), lambda: {group['key']: group},
        {'owner': group['owner'], 'binding_id': 'fictional_binding', 'authority_sha': adapter.sha('fictional_authority'),
         'catalog_path': str(catalog_path)}, lambda: None,
        SimpleNamespace(open_catalog=lambda path: Catalog(path)), engine, quarantine(),
        receipts_path=private / 'receipts.sqlite3', analysis_path=analysis_path,
        frozen=frozenset(), require_new_source=frozen)
    monkeypatch.setattr(value, 'preflight', lambda: value.quarantine)
    unit = adapter.single_alibaba_unit(core, group, record)
    return SimpleNamespace(value=value, item=value._entry(unit, record), group=group, record=record,
                           reply=reply, calls=calls, catalog_path=catalog_path, analysis_path=analysis_path,
                           frozen_checks=frozen_checks, private=private)


def test_alibaba_one_physical_request_and_idempotent_catalog_publication(alibaba):
    f = alibaba
    reply = f.value.request(f.item, adapter.ALIBABA_MODEL, 65536)
    assert len(f.calls) == 1
    f.value.apply_local(f.item, [reply])
    f.value.apply_local(f.item, [reply])
    assert len(f.calls) == 1
    with sqlite3.connect(f.catalog_path) as db:
        db.row_factory = sqlite3.Row
        asset, = db.execute('SELECT * FROM multimedia_assets').fetchall()
        document = json.loads(asset['extraction_snapshot_json'])
        assert asset['logical_asset_id'].startswith('icbu-analysis-recovery-source:')
        assert document['analysis_scope'] == 'single_source_recovery_not_complete_conversation'
        assert document['source_message_count'] == 1 and document['auto_send'] is False
        assert db.execute('SELECT count(*) FROM knowledge_chunks').fetchone()[0] == 1
    assert f.frozen_checks


def test_alibaba_profile_circuit_does_not_get_cleared(alibaba):
    f = alibaba
    with sqlite3.connect(f.analysis_path) as db:
        db.execute("INSERT INTO provider_circuits VALUES('profile','existing-qwen-credential-profile','open')")
    with pytest.raises(adapter.ChannelBlocked):
        f.value.plan(f.item)
    assert f.calls == []
    with sqlite3.connect(f.analysis_path) as db:
        assert db.execute('SELECT state FROM provider_circuits').fetchall() == [('open',)]


def test_alibaba_prior_platform_alias_never_requests(alibaba):
    f = alibaba
    old = dict(f.record, inbound_id='fictional_old_alias', source_sha256=adapter.sha('fictional_old_payload'))
    group = dict(f.group, records=[old])
    with sqlite3.connect(f.analysis_path) as db:
        db.execute('INSERT INTO analyses VALUES(?,?)', ('fictional_old_version', adapter.canon(group)))
        db.execute('INSERT INTO windows VALUES(?,?,?,?)', ('fictional_old_version', 0, 'validated',
                   adapter.canon({'segments': [{'inbound_id': old['inbound_id'], 'source_sha256': old['source_sha256']}]})))
        db.execute('INSERT INTO model_attempts VALUES(?,?,?,?)', ('fictional_old_version', 0, 1, 'validated'))
    with pytest.raises(ValueError, match='旧请求'):
        f.value.request(f.item, adapter.ALIBABA_MODEL, 65536)
    assert f.calls == []


def test_alibaba_catalog_commit_then_restart_writes_only_missing_receipt(alibaba):
    f = alibaba
    receipt_dir = f.private / 'receipts'
    receipt_dir.mkdir(mode=0o755)
    receipt_dir.chmod(0o755)
    f.value.receipts_path = receipt_dir / 'receipts.sqlite3'
    with pytest.raises(ValueError, match='0700'):
        f.value.apply_local(f.item, [f.reply()])
    receipt_dir.chmod(0o700)
    f.value.apply_local(f.item, [f.reply()])
    with sqlite3.connect(f.catalog_path) as db:
        assert db.execute('SELECT count(*) FROM multimedia_assets').fetchone()[0] == 1
        assert db.execute('SELECT count(*) FROM knowledge_chunks').fetchone()[0] == 1
    assert f.calls == []


def test_wechat_batch_source_check_matches_single_checks(wechat):
    f, value = wechat
    f.add(1, 5000)
    f.add(2, 5001)
    items = value.discover_live(1000)
    assert len(items) >= 1
    assert value.read_sources(items) == [value.read_source(item) for item in items]
    changed = dict(items[0], source_sha256='0' * 64)
    with pytest.raises(Exception):
        value.read_sources([changed])


def test_wechat_prepared_cache_is_opt_in_and_expires(wechat):
    f, value = wechat
    f.add(1, 5000)
    item, = value.discover_live(1000)
    calls = []
    real = value._units
    value._units = lambda *a, **k: calls.append(1) or real(*a, **k)
    value.read_source(item)
    value.read_source(item)
    assert len(calls) == 2  # 默认不缓存，每次现场重建
    value.prepared_ttl = 30
    calls.clear()
    value.read_source(item)
    value.read_source(item)
    assert len(calls) == 1  # 开启后短时复用
    value.clock = lambda: 10000 + 31
    value.read_source(item)
    assert len(calls) == 2  # 过期后重建
