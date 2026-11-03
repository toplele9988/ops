"""显式 dry-run-all 的虚构输入；网络与真实余额入口均不可使用。"""
import base64
from copy import deepcopy
from contextlib import contextmanager
import json
from pathlib import Path
import socket
import sqlite3
import tempfile
import time

import prepare_install as p
from official_deepseek import OfficialDeepSeek
from recovery_fixtures import load_modules, synthetic_quarantine
from test_isolation import synthetic_holds
from test_backlog import entry


def private_json(path, value):
    p.write_json(path, value)
    return path


def sources_fixture(root):
    modules = load_modules(root)
    frozen = synthetic_holds(modules, root)
    isolation = modules.kb_unknown_isolation.capture(frozen.db, frozen.ledger, frozen.matrix)
    frozen.db.close()
    quarantine = synthetic_quarantine(modules, isolation)
    wechat = frozen.path.parent
    private_json(wechat / 'usage.json', frozen.ledger)
    private_json(wechat / 'quota-review.json', {
        'quota_baseline': frozen.matrix.quota_baseline, 'legacy_quarantine': []})
    alibaba = p.private_dir(root, 'alibaba')
    for directory, names in ((wechat, ('catalog', 'state')),
                             (alibaba, ('analysis', 'inbound', 'ledger', 'catalog'))):
        for name in names:
            path = directory / (name + '.sqlite3')
            p.write(path, b'')
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE fictional_fixture(value TEXT)')
                db.execute('INSERT INTO fictional_fixture VALUES(?)', ('虚构技术资料，仅供隔离演练',))
    private_json(alibaba / 'source-freeze.json', {'schema': 'fictional-freeze'})
    private_json(alibaba / 'usage.json', {'models': {}})
    sources = {channel: {name: str(directory / name) for name in names}
               for channel, directory, names in (
                   ('wechat', wechat, p.SOURCES['wechat']),
                   ('alibaba', alibaba, p.SOURCES['alibaba']))}
    return sources, quarantine


def values_fixture(now=None):
    now = now or time.time()
    return {
        'currency': 'CNY', 'request_tokens': 15000, 'max_age_seconds': 3600,
        'rates': {'deepseek-v4-pro-0813': 0, 'deepseek-v4.1-flash': 0,
                  'deepseek-v4-pro': 8000000, 'deepseek-flash': 3000000},
        'free_balances': {model: {'tokens': 100000, 'unknown_reserved_tokens': 1000,
            'other_reserved_tokens': 2000, 'observed_at': now - 1, 'expires_at': now + 7200,
            'free_only_verified': True}
            for model in ('deepseek-v4-pro-0813', 'deepseek-v4.1-flash')},
    }


class DryBackend(p.Backend):
    """只替换盘点现场输入和余额传输，补丁、草案与 SHA 验证沿用原代码。"""

    def __init__(self, quarantine):
        self.quarantine = deepcopy(quarantine)
        self.inventory_calls = 0
        self.balance_calls = 0
        self.bad_count = False

    def inventory(self, ws, als, expected_wechat, expected_alibaba):
        self.inventory_calls += 1
        summary = {'wechat_sources': 1 + int(self.bad_count), 'wechat_units': 1,
                   'alibaba_sources': 1, 'alibaba_units': 1, 'unknown_requests': 4}
        return summary, {'quarantine': deepcopy(self.quarantine)}

    def balance(self, currency, persist_receipt):
        def fake(packet):
            if packet != {'operation': 'balance', 'timeout_seconds': 15}:
                raise AssertionError('演练只允许一次假余额查询')
            self.balance_calls += 1
            body = {'is_available': True, 'balance_infos': [{'currency': currency,
                'total_balance': '123.00', 'granted_balance': '3.00', 'topped_up_balance': '120.00'}]}
            return {'http_status': 200, 'body_base64': base64.b64encode(
                json.dumps(body).encode()).decode(), 'retry_after_seconds': None}
        return OfficialDeepSeek(fake).read_balance(currency=currency, persist_receipt=persist_receipt)


def review_fixture(session, *, after=1000):
    files = p.read_json(session.root / 'snapshot/snapshots.json')['files']
    pins = {name.replace('/', '_').replace('.sqlite3', '').replace('.json', ''): row['sha256']
            for name, row in files.items()}
    wechat, alibaba = entry(91), entry(92)
    alibaba['channel'] = 'alibaba'
    return {'schema': 'kb-backlog-review-v1', 'after': after, 'snapshots': pins,
            'entries': [wechat, alibaba]}


@contextmanager
def no_network():
    saved = socket.socket.connect, socket.socket.connect_ex, socket.create_connection
    def blocked(*args, **kwargs):
        raise AssertionError('演练禁止访问真实网络')
    socket.socket.connect = socket.socket.connect_ex = socket.create_connection = blocked
    try:
        yield
    finally:
        socket.socket.connect, socket.socket.connect_ex, socket.create_connection = saved


def run_all():
    with no_network():
        root = Path(tempfile.mkdtemp(prefix='kb-prepare-fictional-', dir='/tmp'))
        sources, quarantine = sources_fixture(root)
        backend = DryBackend(quarantine)
        session = p.Session.create(backend)
        values = private_json(root / 'values.json', values_fixture())
        snapshot_options = {'sources': sources, 'writers_stopped': True, 'sample_seconds': 0}
        session.run('snapshot', **snapshot_options)
        review = private_json(root / 'review.json', review_fixture(session))
        options = {
            'snapshot': snapshot_options,
            'inventory': {'expected_wechat': 1, 'expected_wechat_units': 1, 'expected_alibaba': 1},
            'plan': {'after': 1000, 'review_path': review, 'review_sha256': p.sha(p.raw_file(review))},
            'render-config': {'values_path': values},
            'verify': {}, 'root-commands': {},
        }
        for stage in p.STAGES:
            session.run(stage, **options[stage])
        if len(p.read_json(session.root / 'plan/manifest.json')['items']) != 2:
            raise AssertionError('两渠道受审单元未进入冻结清单')
        before = {stage: p.b.digest(session.marker(stage)) for stage in p.STAGES}
        for stage in p.STAGES:
            session.run(stage, **options[stage])
        if before != {stage: p.b.digest(session.marker(stage)) for stage in p.STAGES}:
            raise AssertionError('重复编排改变了已冻结产物')
        if backend.inventory_calls != 1 or backend.balance_calls != 1:
            raise AssertionError('重复编排重复调用了盘点或余额入口')
        failed = 0
        missing = p.Session.create(backend)
        try:
            missing.run('verify')
        except p.PrepareError:
            failed += 1
        bad = DryBackend(quarantine)
        bad.bad_count = True
        rejected = p.Session.create(bad)
        rejected.run('snapshot', **options['snapshot'])
        try:
            rejected.run('inventory', **options['inventory'])
        except p.PrepareError:
            failed += 1
        try:
            rejected.run('plan', **options['plan'])
        except p.PrepareError:
            failed += 1
        unknown_session = p.Session.create(DryBackend(quarantine))
        unknown_session.run('snapshot', **snapshot_options)
        unknown_session.run('inventory', **options['inventory'])
        unknown_review = review_fixture(unknown_session)
        unknown_review['entries'][0]['content_sha256'] = quarantine['holds'][0]['content_sha256'][0]
        unknown_path = private_json(root / 'unknown-review.json', unknown_review)
        try:
            unknown_session.run('plan', after=1000, review_path=unknown_path,
                                review_sha256=p.sha(p.raw_file(unknown_path)))
        except ValueError:
            failed += 1
        if failed != 4 or (rejected.root / 'plan').exists() or (unknown_session.root / 'plan').exists():
            raise AssertionError('失败阶段之后仍继续执行')
        return {'状态': '虚构编排通过；未安装、未启动、未请求模型', '私有目录': str(session.root),
                '阶段数': len(p.STAGES), '重复通过阶段数': len(p.STAGES),
                '冻结虚构单元数': 2,
                '拒绝检查数': failed, '假余额查询数': backend.balance_calls,
                '假盘点调用数': backend.inventory_calls,
                'config_sha256': p.sha(p.raw_file(session.root / 'render-config/config.json')),
                '真实网络请求数': 0, '模型请求数': 0}
