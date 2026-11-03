"""安装前编排只使用虚构副本与注入的只读盘点、余额入口。"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys

import pytest

from recovery_fixtures import ROOT, load_modules, synthetic_quarantine
from test_isolation import synthetic_holds

HERE = ROOT / 'deploy/kb'
if str(HERE) not in sys.path:
    sys.path.append(str(HERE))
import prepare_install as p
from prepare_install_fixtures import DryBackend, review_fixture, values_fixture


def file_sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_private(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False))
    path.chmod(0o600)
    return path


@pytest.fixture
def fictional_sources(tmp_path):
    modules = load_modules(tmp_path)
    frozen = synthetic_holds(modules, tmp_path)
    manifest = modules.kb_unknown_isolation.capture(frozen.db, frozen.ledger, frozen.matrix)
    frozen.db.close()
    quarantine = synthetic_quarantine(modules, manifest)
    wechat = frozen.path.parent
    write_private(wechat / 'usage.json', frozen.ledger)
    write_private(wechat / 'quota-review.json', {
        'quota_baseline': frozen.matrix.quota_baseline, 'legacy_quarantine': []})
    alibaba = tmp_path / 'fictional-alibaba'
    alibaba.mkdir(mode=0o700)
    for directory, names in ((wechat, ('catalog', 'state')),
                             (alibaba, ('analysis', 'inbound', 'ledger', 'catalog'))):
        for name in names:
            path = directory / (name + '.sqlite3')
            path.touch(mode=0o600)
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE fixture(value TEXT)')
                db.execute('INSERT INTO fixture VALUES(?)', ('虚构测试内容，不代表真实客户资料',))
    write_private(alibaba / 'source-freeze.json', {'schema': 'fictional-freeze'})
    write_private(alibaba / 'usage.json', {'models': {}})
    sources = {
        'wechat': {name: str(wechat / name) for name in (
            'catalog.sqlite3', 'calls.sqlite3', 'state.sqlite3', 'usage.json', 'quota-review.json')},
        'alibaba': {name: str(alibaba / name) for name in (
            'analysis.sqlite3', 'inbound.sqlite3', 'ledger.sqlite3', 'catalog.sqlite3',
            'source-freeze.json', 'usage.json')},
    }
    return sources, quarantine


class FakeBackend(p.Backend):
    def __init__(self, quarantine):
        self.quarantine = quarantine
        self.inventory_calls = 0
        self.balance_calls = 0
        self.counts = {'wechat_sources': 1600, 'wechat_units': 1603,
                       'alibaba_sources': 106, 'alibaba_units': 106}

    def inventory(self, ws, als, expected_wechat, expected_alibaba):
        self.inventory_calls += 1
        return deepcopy(self.counts), {'quarantine': deepcopy(self.quarantine)}

    def balance(self, currency, persist_receipt):
        raise AssertionError('未配置现场值时不应查询余额')


def snap(fictional_sources):
    sources, quarantine = fictional_sources
    session = p.Session.create()
    backend = FakeBackend(quarantine)
    session = p.Session(session.root, backend=backend)
    session.run('snapshot', sources=sources, writers_stopped=True, sample_seconds=0)
    return session, backend


def inventory(session):
    return session.run('inventory', expected_wechat=1600,
                       expected_alibaba=106, expected_wechat_units=1603)


def test_snapshot_reads_sqlite_backup_without_mutating_source(fictional_sources):
    sources, _ = fictional_sources
    before = {path: Path(path).read_bytes() for files in sources.values() for path in files.values()}
    session, _ = snap(fictional_sources)
    assert session.root.is_relative_to('/tmp')
    assert before == {path: Path(path).read_bytes() for path in before}
    backups = list((session.root / 'snapshot').rglob('*.sqlite3'))
    assert len(backups) == 7
    assert not list((session.root / 'snapshot').rglob('*-wal'))
    assert not list((session.root / 'snapshot').rglob('*-shm'))
    for path in (session.root, *session.root.rglob('*')):
        assert stat.S_IMODE(path.stat().st_mode) == (0o700 if path.is_dir() else 0o600)


def test_snapshot_requires_stopped_writer_acknowledgment(fictional_sources):
    sources, _ = fictional_sources
    session = p.Session.create()
    with pytest.raises((ValueError, RuntimeError)):
        session.run('snapshot', sources=sources, writers_stopped=False, sample_seconds=0)
    with pytest.raises((ValueError, RuntimeError)):
        inventory(session)


def test_nonempty_wal_stops_before_immutable_backup(fictional_sources):
    sources, _ = fictional_sources
    wal = Path(sources['wechat']['catalog.sqlite3'] + '-wal')
    wal.write_bytes(b'fictional WAL data frame' * 10)
    wal.chmod(0o600)
    session = p.Session.create()
    with pytest.raises((ValueError, RuntimeError)):
        session.run('snapshot', sources=sources, writers_stopped=True, sample_seconds=0)
    assert wal.read_bytes() == b'fictional WAL data frame' * 10
    with pytest.raises((ValueError, RuntimeError)):
        inventory(session)


def test_wal_change_between_two_samples_stops_backup(fictional_sources, monkeypatch):
    sources, _ = fictional_sources
    wal = Path(sources['wechat']['catalog.sqlite3'] + '-wal')
    def writer_during_wait(_seconds):
        wal.write_bytes(b'fictional still active writer' * 10)
        wal.chmod(0o600)
    monkeypatch.setattr(p.time, 'sleep', writer_during_wait)
    session = p.Session.create()
    with pytest.raises(p.PrepareError, match='两次采样'):
        session.run('snapshot', sources=sources, writers_stopped=True, sample_seconds=0)
    assert not (session.root / 'snapshot').exists()


@pytest.mark.parametrize('kind', ['symlink', 'hardlink'])
def test_snapshot_rejects_linked_sources(fictional_sources, tmp_path, kind):
    sources, _ = fictional_sources
    original = Path(sources['wechat']['state.sqlite3'])
    link = tmp_path / 'fictional-linked.sqlite3'
    if kind == 'symlink':
        link.symlink_to(original)
    else:
        os.link(original, link)
    sources['wechat']['state.sqlite3'] = str(link)
    session = p.Session.create()
    with pytest.raises((ValueError, RuntimeError)):
        session.run('snapshot', sources=sources, writers_stopped=True, sample_seconds=0)


def test_snapshot_rejects_parent_component_before_reading(fictional_sources):
    sources, _ = fictional_sources
    path = Path(sources['wechat']['catalog.sqlite3'])
    sources['wechat']['catalog.sqlite3'] = str(path.parent / '..' / path.parent.name / path.name)
    with pytest.raises((ValueError, RuntimeError)):
        p.Session.create().run('snapshot', sources=sources,
                               writers_stopped=True, sample_seconds=0)


def test_snapshot_requires_complete_named_source_set(fictional_sources):
    sources, _ = fictional_sources
    del sources['alibaba']['ledger.sqlite3']
    with pytest.raises((ValueError, RuntimeError)):
        p.Session.create().run('snapshot', sources=sources,
                               writers_stopped=True, sample_seconds=0)


def test_session_rejects_linked_or_public_directory(tmp_path):
    public = tmp_path / 'fictional-public-session'
    public.mkdir(mode=0o755)
    public.chmod(0o755)
    with pytest.raises((ValueError, RuntimeError)):
        p.Session(public)
    session = p.Session.create()
    link = tmp_path / 'fictional-session-link'
    link.symlink_to(session.root, target_is_directory=True)
    with pytest.raises((ValueError, RuntimeError)):
        p.Session(link)


def test_snapshot_is_idempotent(fictional_sources):
    session, _ = snap(fictional_sources)
    before = {path: path.read_bytes() for path in (session.root / 'snapshot').rglob('*') if path.is_file()}
    sources, _ = fictional_sources
    session.run('snapshot', sources=sources, writers_stopped=True, sample_seconds=0)
    assert before == {path: path.read_bytes() for path in before}


def test_inventory_runs_once_and_is_idempotent(fictional_sources):
    session, backend = snap(fictional_sources)
    inventory(session)
    inventory(session)
    assert backend.inventory_calls == 1


@pytest.mark.parametrize('field', ['wechat_sources', 'wechat_units', 'alibaba_sources'])
def test_inventory_count_change_stops_later_stages(fictional_sources, field):
    session, backend = snap(fictional_sources)
    backend.counts[field] += 10
    with pytest.raises((ValueError, RuntimeError)):
        inventory(session)
    with pytest.raises((ValueError, RuntimeError)):
        session.run('plan', after=1000, bootstrap_empty=True)


def test_snapshot_hash_tampering_blocks_inventory(fictional_sources):
    session, backend = snap(fictional_sources)
    target = next((session.root / 'snapshot').rglob('catalog.sqlite3'))
    with target.open('ab') as stream:
        stream.write(b'fictional tampering')
    with pytest.raises((ValueError, RuntimeError)):
        inventory(session)
    assert backend.inventory_calls == 0


def test_plan_rejects_unbound_review_bytes(fictional_sources, tmp_path):
    session, _ = snap(fictional_sources)
    inventory(session)
    review = write_private(tmp_path / 'fictional-review.json', {})
    with pytest.raises((ValueError, RuntimeError)):
        session.run('plan', after=1000, review_path=review, review_sha256='a' * 64)


def test_plan_requires_authorized_review_or_explicit_empty_bootstrap(fictional_sources):
    session, _ = snap(fictional_sources)
    inventory(session)
    with pytest.raises((ValueError, RuntimeError)):
        session.run('plan', after=1000)


@pytest.mark.parametrize('field', ['snapshots', 'after'])
def test_review_valid_sha_but_wrong_snapshot_or_boundary_is_rejected(fictional_sources, tmp_path, field):
    session, _ = snap(fictional_sources)
    inventory(session)
    review = review_fixture(session)
    if field == 'snapshots':
        review['snapshots']['wechat_catalog'] = 'f' * 64
    else:
        review['after'] += 1
    path = write_private(tmp_path / 'fictional-wrong-review.json', review)
    with pytest.raises(p.PrepareError, match='未绑定'):
        session.run('plan', after=1000, review_path=path, review_sha256=file_sha(path))
    assert not (session.root / 'plan').exists()


def test_imported_nonempty_review_freezes_two_channels(fictional_sources, tmp_path):
    session, _ = snap(fictional_sources)
    inventory(session)
    review = review_fixture(session)
    path = write_private(tmp_path / 'fictional-reviewed-plan.json', review)
    session.run('plan', after=1000, review_path=path, review_sha256=file_sha(path))
    manifest = p.read_json(session.root / 'plan/manifest.json')
    assert len(manifest['items']) == 2
    assert {item['channel'] for item in manifest['items']} == {'wechat', 'alibaba'}
    assert manifest['review_sha256'] == p.b.digest(review)


@pytest.mark.parametrize('stage', ['inventory', 'plan', 'render-config', 'verify', 'root-commands'])
def test_stages_refuse_missing_predecessors(stage):
    session = p.Session.create()
    with pytest.raises((ValueError, RuntimeError)):
        session.run(stage)


def test_render_refuses_missing_values_before_balance(fictional_sources, tmp_path):
    session, backend = snap(fictional_sources)
    inventory(session)
    session.run('plan', after=1000, bootstrap_empty=True)
    values = write_private(tmp_path / 'fictional-values.json', {})
    with pytest.raises((ValueError, RuntimeError)):
        session.run('render-config', values_path=values)
    assert backend.balance_calls == 0


def planned_session(fictional_sources):
    sources, quarantine = fictional_sources
    backend = DryBackend(quarantine)
    session = p.Session.create(backend)
    session.run('snapshot', sources=sources, writers_stopped=True, sample_seconds=0)
    session.run('inventory', expected_wechat=1, expected_wechat_units=1, expected_alibaba=1)
    session.run('plan', after=1000, bootstrap_empty=True)
    return session, backend


def render(session, tmp_path, values=None):
    path = write_private(tmp_path / 'fictional-render-values.json', values or values_fixture())
    session.run('render-config', values_path=path)
    return path


def test_render_binds_raw_and_canonical_shas_and_preserves_unknown_reserves(fictional_sources, tmp_path):
    session, backend = planned_session(fictional_sources)
    path = render(session, tmp_path)
    config = p.read_json(session.root / 'render-config/config.json')
    manifest = p.read_json(session.root / 'plan/manifest.json')
    quarantine = p.read_json(session.root / 'plan/quarantine.json')
    baseline = p.read_json(HERE / 'coordinator-baseline.json', private=False)
    assert config['manifest_sha256'] == p.b.digest(manifest)
    assert config['manifest_sha256'] != file_sha(session.root / 'plan/manifest.json')
    assert config['quarantine_sha256'] == p.b.digest(quarantine)
    assert config['release_sha256'] == file_sha(HERE / 'coordinator-baseline.json')
    assert config['adapter']['approved_calls_sha256'] == file_sha(session.root / 'snapshot/wechat/calls.sqlite3')
    assert config['adapter']['loader_sha256'] == baseline['installed_overlay_sha256'][
        '/opt/imgw/wechat-kb-dual-live-v1/live_loader.py']
    assert p.read_json(session.root / 'render-config/summary.json')['config_sha256'] == file_sha(
        session.root / 'render-config/config.json')
    assert config['physical']['buckets']['deepseek_official']['max_cost_micros'] == 123000000
    assert config['physical']['buckets']['dashscope_free_pro']['max_tokens'] == 97000
    assert config['physical']['buckets']['dashscope_free_flash']['max_tokens'] == 97000
    # 已核验的账户必须有非空的请求数上限，否则物理闸门会把整个账户判为未核实而停止。
    from physical_gate import PhysicalGate
    for name in ('dashscope_free_pro', 'dashscope_free_flash', 'deepseek_official'):
        bucket = config['physical']['buckets'][name]
        assert type(bucket['max_requests']) is int and bucket['max_requests'] > 0
    session.run('render-config', values_path=path)
    assert backend.balance_calls == 1


@pytest.mark.parametrize('missing', ['currency', 'request_tokens', 'rates', 'free_balances', 'max_age_seconds'])
def test_missing_site_value_stops_before_official_query(fictional_sources, tmp_path, missing):
    session, backend = planned_session(fictional_sources)
    values = values_fixture()
    del values[missing]
    with pytest.raises(p.PrepareError):
        render(session, tmp_path, values)
    assert backend.balance_calls == 0
    assert not (session.root / 'render-config').exists()


@pytest.mark.parametrize('field', ['observed_at', 'expires_at', 'unknown_reserved_tokens'])
def test_missing_free_observation_evidence_stops_before_query(fictional_sources, tmp_path, field):
    session, backend = planned_session(fictional_sources)
    values = values_fixture()
    del values['free_balances']['deepseek-v4-pro-0813'][field]
    with pytest.raises(p.PrepareError):
        render(session, tmp_path, values)
    assert backend.balance_calls == 0


def test_expired_observation_stops_before_query(fictional_sources, tmp_path):
    session, backend = planned_session(fictional_sources)
    values = values_fixture()
    row = values['free_balances']['deepseek-v4-pro-0813']
    row['expires_at'] = row['observed_at']
    with pytest.raises(p.PrepareError):
        render(session, tmp_path, values)
    assert backend.balance_calls == 0


def test_insufficient_free_balance_does_not_release_unknown_reserve(fictional_sources, tmp_path):
    session, backend = planned_session(fictional_sources)
    values = values_fixture()
    values['free_balances']['deepseek-v4-pro-0813']['tokens'] = 999
    with pytest.raises(p.PrepareError):
        render(session, tmp_path, values)
    assert backend.balance_calls == 0


def test_changed_values_cannot_replace_frozen_render_or_query_again(fictional_sources, tmp_path):
    session, backend = planned_session(fictional_sources)
    path = render(session, tmp_path)
    config = (session.root / 'render-config/config.json').read_bytes()
    values = p.read_json(path)
    values['request_tokens'] += 1
    write_private(path, values)
    with pytest.raises(p.PrepareError):
        session.run('render-config', values_path=path)
    assert backend.balance_calls == 1
    assert (session.root / 'render-config/config.json').read_bytes() == config


def test_expired_balance_blocks_verify_and_later_root_commands(fictional_sources, tmp_path, monkeypatch):
    session, _ = planned_session(fictional_sources)
    render(session, tmp_path)
    real_now = p.time.time()
    monkeypatch.setattr(p.time, 'time', lambda: real_now + 7200)
    with pytest.raises(p.PrepareError):
        session.run('verify')
    with pytest.raises(p.PrepareError):
        session.run('root-commands')


def test_tampered_render_blocks_verify(fictional_sources, tmp_path):
    session, _ = planned_session(fictional_sources)
    render(session, tmp_path)
    config = session.root / 'render-config/config.json'
    with config.open('ab') as stream:
        stream.write(b' ')
    with pytest.raises(p.PrepareError):
        session.run('verify')
    assert not (session.root / 'verify').exists()


def test_invalid_official_balance_retains_receipt_and_stops(fictional_sources, tmp_path):
    import base64
    from official_deepseek import OfficialDeepSeek
    session, backend = planned_session(fictional_sources)
    def invalid_balance(currency, persist_receipt):
        def fake(packet):
            assert packet['operation'] == 'balance'
            backend.balance_calls += 1
            return {'http_status': 200, 'body_base64': base64.b64encode(
                json.dumps({'is_available': True, 'balance_infos': []}).encode()).decode(),
                'retry_after_seconds': None}
        return OfficialDeepSeek(fake).read_balance(currency=currency, persist_receipt=persist_receipt)
    backend.balance = invalid_balance
    with pytest.raises(ValueError):
        render(session, tmp_path)
    assert backend.balance_calls == 1
    assert not (session.root / 'render-config').exists()
    failed = list(session.root.glob('failed-render-config-*'))
    assert len(failed) == 1
    receipt = p.read_json(failed[0] / 'balance-receipt.json')
    assert receipt['operation'] == 'balance' and receipt['http_status'] == 200
    assert not (failed[0] / 'stage.json').exists()
    with pytest.raises(p.PrepareError):
        session.run('verify')


def test_dry_run_all_repeats_six_stages_with_no_real_balance(monkeypatch):
    from prepare_install_fixtures import run_all
    def rejected(*args, **kwargs):
        raise AssertionError('不允许回退到真实余额查询')
    monkeypatch.setattr(p.Backend, 'balance', rejected)
    result = run_all()
    assert result['阶段数'] == result['重复通过阶段数'] == 6
    assert result['拒绝检查数'] == 4
    assert result['冻结虚构单元数'] == 2
    assert result['假余额查询数'] == result['假盘点调用数'] == 1
    assert result['真实网络请求数'] == result['模型请求数'] == 0
    session = p.Session(result['私有目录'])
    assert (session.root / 'root-commands/install-root.sh').is_file()
    assert (session.root / 'root-commands/rollback-root.sh').is_file()


def test_balance_timeout_stops_with_chinese_message(fictional_sources, tmp_path, monkeypatch):
    import subprocess
    session, backend = planned_session(fictional_sources)
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired('fictional-readonly-balance', 15)
    monkeypatch.setattr(backend, 'balance', timeout)
    with pytest.raises(p.PrepareError, match='余额查询进程超时或未完成'):
        render(session, tmp_path)
    assert not (session.root / 'render-config').exists()
    assert list(session.root.glob('failed-render-config-*'))
    with pytest.raises(p.PrepareError):
        session.run('verify')
