import hashlib
import json
import sqlite3
import time

import pytest

from console.audit.common import finding, read_db, sample_id
from console.audit.service import Audit
from console.archive.store import Store
from console.roles.service import Roles


NOW = 1893456000


def make_checks():
    def result(key, level, count):
        return lambda ctx: finding(key, '虚构检查', level, count, '虚构说明', '系统后台 → 数据源维护')
    def fail(ctx):
        raise RuntimeError('Cookie=fictional-cookie bare-fictional-secret /tmp/private/customer.txt')
    return {'wechat': [('wechat.ok', '正常项', result('wechat.ok', 'ok', 0)),
                       ('wechat.error', '失败项', fail)],
            'alibaba': [('alibaba.high', '高项', result('alibaba.high', 'high', 12))],
            'whatsapp': [('whatsapp.low', '低项', result('whatsapp.low', 'low', 2))],
            'archive': [('archive.optional', '可选项', lambda ctx: None)]}


def test_failure_isolation_sorting_summary_and_filtered_scope(tmp_path):
    audit = Audit(tmp_path / 'archive.db', clock=lambda: NOW, checks=make_checks())
    result = audit.scan()
    assert [r['level'] for r in result['items']] == ['high', 'medium', 'low', 'ok']
    assert result['summary'] == '2 个需要处理；计划中 1 项'
    assert result['attention_count'] == 2 and result['planned_count'] == 1
    assert result['plan']['whatsapp'] == 'not_started'
    assert result['total'] == 4 and result['failed_count'] == 1
    assert result['skipped'] == ['archive.optional']
    assert '读取失败：' in result['items'][1]['meaning']
    assert 'fictional-secret' not in json.dumps(result) and 'Cookie' not in json.dumps(result)
    assert audit.scan('alibaba')['total'] == 1
    assert not list(tmp_path.iterdir())


def test_independent_query_timeouts_keep_later_checks_running(tmp_path):
    path = tmp_path / 'data.db'
    sqlite3.connect(path).close()
    def slow(ctx):
        with read_db(path) as db:
            db.execute('WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n WHERE x<100000000) SELECT sum(x) FROM n').fetchone()
    checks = make_checks()
    checks['wechat'] = [('wechat.slow', '慢项', slow)] + checks['wechat'][:1]
    service = Audit(tmp_path / 'archive.db', checks=checks)
    start = time.monotonic()
    result = service.scan()
    assert time.monotonic() - start < 3
    assert next(i for i in result['items'] if i['key'] == 'wechat.slow')['read_failed']
    assert next(i for i in result['items'] if i['key'] == 'wechat.ok')['level'] == 'ok'


@pytest.mark.parametrize('wal', [False, True])
def test_read_only_hash_files_and_query_only(tmp_path, wal):
    path = tmp_path / 'source.db'
    writer = sqlite3.connect(path)
    if wal:
        writer.execute('PRAGMA journal_mode=WAL')
    writer.execute('CREATE TABLE data (n INTEGER)')
    writer.execute('INSERT INTO data VALUES (7)')
    writer.commit()
    if not wal:
        writer.close()
    def files():
        # SQLite's read-only WAL reader updates transient SHM read marks, not
        # source rows or the WAL. Source data hashes must remain identical.
        return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.iterdir()
                if not p.name.endswith('-shm')}
    before = files()
    with read_db(path) as db:
        assert db.execute('SELECT sum(n) FROM data').fetchone()[0] == 7
        with pytest.raises(sqlite3.OperationalError):
            db.execute('DELETE FROM data')
    assert files() == before
    if wal:
        writer.close()


def test_closed_wal_read_does_not_create_sidecars(tmp_path):
    path = tmp_path / 'closed.db'
    with sqlite3.connect(path) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE data (n INTEGER)')
    db.close()
    before = set(tmp_path.iterdir())
    with read_db(path) as reader:
        assert reader.execute('SELECT count(*) FROM data').fetchone()[0] == 0
    assert set(tmp_path.iterdir()) == before


def test_missing_source_never_created_and_sources_are_isolated(tmp_path):
    service = Audit(tmp_path / 'archive.db', checks=make_checks())
    assert all(path.parent == tmp_path for path in service.paths.values())
    with pytest.raises(FileNotFoundError):
        with read_db(tmp_path / 'missing.db'):
            pass
    assert not list(tmp_path.iterdir())


def test_run_is_fresh_rules_only_and_visible_in_roles(tmp_path):
    path = tmp_path / 'archive.db'
    audit = Audit(path, checks=make_checks(), clock=lambda: NOW)
    first, second = audit.run({}, 'fictional-owner'), audit.run({}, 'fictional-owner')
    assert first['run']['id'] != second['run']['id']
    assert second['run']['source'] == 'rules' and second['run']['model'] is None
    assert second['run']['role'] == 'audit' and second['run']['status'] == 'fallback'
    roles = Roles(path, runner=lambda *a: pytest.fail('must never invoke AI'), clock=lambda: NOW)
    item = next(r for r in roles.read('roles')['items'] if r['key'] == 'audit')
    assert item['name'] == '数据体检员' and item['model'] == '纯规则' and item['level'] == 'L0'
    assert item['runs_24h'] == 2
    assert len(roles.read('runs', {'role': 'audit'})['items']) == 2
    with read_db(path) as db:
        assert db.execute('SELECT count(*) FROM role_ai_reservations').fetchone()[0] == 0


def test_sample_limit_and_opaque_identifier():
    result = finding('test.sample', '标题', 'medium', 9, '说明', '系统后台',
                     [{'ref': sample_id('fictional-person-' + str(n))} for n in range(9)])
    assert len(result['samples']) == 5
    assert 'fictional-person' not in json.dumps(result)


def test_remediation_preserves_only_known_frontend_routes():
    result = finding('test.route', '标题', 'ok', 0, '说明',
                     '到 /products 和 /inbox 查看； /private/fictional-secret 不应回传')
    assert '/products' in result['remedy'] and '/inbox' in result['remedy']
    assert 'fictional-secret' not in result['remedy']


@pytest.mark.parametrize('alias', ['same', 'symlink', 'hardlink'])
def test_run_store_cannot_alias_channel_sources(tmp_path, alias):
    path = tmp_path / 'channel.db'
    sqlite3.connect(path).close()
    target = tmp_path / 'archive.db'
    if alias == 'same':
        target = path
    elif alias == 'symlink':
        target.symlink_to(path)
    else:
        target.hardlink_to(path)
    with pytest.raises(ValueError, match='独立'):
        Audit(target, inbound_path=path, checks=make_checks())


def test_default_missing_sources_are_independent_findings(tmp_path):
    service = Audit(tmp_path / 'archive.db')
    start = time.monotonic()
    result = service.scan()
    assert time.monotonic() - start < 3
    assert {i['channel'] for i in result['items']} == set(service.checks)
    assert result['failed_count'] > 0
    assert not list(tmp_path.iterdir())
