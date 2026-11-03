"""分阶段准备知识库安装产物；只写 /tmp，不安装、不启动、不请求模型。"""
from __future__ import annotations

import argparse
from contextlib import closing, contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import subprocess
import tempfile
import time
from types import SimpleNamespace

import backlog as b

HERE = Path(__file__).absolute().parent
STAGES = ('snapshot', 'inventory', 'plan', 'render-config', 'verify', 'root-commands')
SOURCES = {
    'wechat': ('catalog.sqlite3', 'calls.sqlite3', 'state.sqlite3', 'usage.json', 'quota-review.json'),
    'alibaba': ('analysis.sqlite3', 'inbound.sqlite3', 'ledger.sqlite3', 'catalog.sqlite3',
                'source-freeze.json', 'usage.json'),
}
RELEASE = ('kb-two-unknown-manifest.json', 'kb-source-quarantine-v1.json',
           'kb-live-recovery-v1.json', 'kb-live-dual-v1.json')
STATE = '/var/lib/imgw/ops-app/kb-recovery-coordinator'


class PrepareError(ValueError):
    pass


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, '参数不完整或不支持，请运行 --help 查看用法\n')

    def format_help(self):
        return super().format_help().replace('usage:', '用法：', 1)


def need(ok, message):
    if not ok:
        raise PrepareError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def checked_path(path, *, directory=False, private=False):
    path = Path(path).absolute()
    need('..' not in path.parts and not any(p.is_symlink() for p in (path, *path.parents)),
         '路径含上级目录或符号链接，请改用完整普通路径')
    info = path.lstat()
    need(stat.S_ISDIR(info.st_mode) if directory else
         stat.S_ISREG(info.st_mode) and info.st_nlink == 1, '文件类型或链接数不符，请核对输入')
    if private:
        need(path.is_relative_to('/tmp') and info.st_uid == os.geteuid()
             and stat.S_IMODE(info.st_mode) == (0o700 if directory else 0o600),
             '私有产物必须在 /tmp，由当前用户持有，目录 0700、文件 0600')
    return path


def raw_file(path, *, private=True):
    return checked_path(path, private=private).read_bytes()


def read_json(path, *, private=True):
    # 沿用官方收据的严格 JSON 解码，拒绝重复键和 NaN。
    from official_deepseek import _json
    return _json(raw_file(path, private=private))


def write(path, raw):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def write_json(path, value):
    write(path, (b.canon(value) + '\n').encode())


def private_dir(parent, name):
    path = parent / name
    path.mkdir(mode=0o700)
    return path


def file_pins(root):
    result = {}
    for path in sorted(root.rglob('*')):
        checked_path(path, directory=path.is_dir(), private=True)
        if path.is_file() and path.name != 'stage.json':
            result[str(path.relative_to(root))] = sha(raw_file(path))
    return result


def wal_sample(path):
    result = {}
    for name, item in (('db', path), ('wal', Path(str(path) + '-wal'))):
        if item.exists() or item.is_symlink():
            info = checked_path(item).stat()
            result[name] = [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns]
        else:
            result[name] = None
    return result


def check_wal(samples):
    need(samples[0] == samples[1],
         '警告：两次采样发现数据库或 WAL 仍在变化；请停止全部旧写者，再新建快照')
    need(all(row['wal'] is None or row['wal'][2] <= 32 for row in samples[1].values()),
         '警告：WAL 仍含数据帧；本脚本不写生产 SHM，也不忽略 WAL。请总指挥确认停写和 checkpoint 后重做快照')


class Backend:
    """受审工具边界；演练通过注入实现替换余额与现场盘点。"""

    def inventory(self, ws, als, expected_wechat, expected_alibaba):
        import inventory_wechat
        import inventory_alibaba
        import provenance
        import estimate_usage
        wr = {}
        inventory_wechat.inventory(ws, wr)
        _, ar = inventory_alibaba.analyze(als)
        _, private = provenance.analyze(ws, als)
        holds = private['quarantine']['holds']
        wp, wc = estimate_usage.wechat_prompts(ws, holds, expected_wechat)
        ap, ac = estimate_usage.alibaba_prompts(als, ar, holds, expected_alibaba)
        return {'wechat_sources': wc['sources'], 'wechat_units': len(wp),
                'alibaba_sources': ac['sources'], 'alibaba_units': len(ap),
                'unknown_requests': len(private['unknown_checklist'])}, private

    def draft(self, ws, after):
        import draft_release
        return draft_release.draft(ws, after)

    def overlay(self):
        from verify_patch import prepare
        return prepare()

    def verify(self):
        import verify_coordinator
        return verify_coordinator.verify()

    def balance(self, currency, persist_receipt):
        from official_deepseek import OfficialDeepSeek
        return OfficialDeepSeek().read_balance(currency=currency, persist_receipt=persist_receipt)


class Session:
    def __init__(self, path, backend=None):
        self.root = checked_path(path, directory=True, private=True)
        self.backend = backend or Backend()

    @classmethod
    def create(cls, backend=None):
        return cls(Path(tempfile.mkdtemp(prefix='kb-prepare-install-', dir='/tmp')), backend)

    @contextmanager
    def locked(self):
        path = self.root / '.prepare.lock'
        if path.exists() or path.is_symlink():
            checked_path(path, private=True)
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise PrepareError('另一准备进程仍持有锁，请等它结束后重跑当前阶段') from None
            yield
        finally:
            os.close(fd)

    def marker(self, stage):
        path = self.root / stage
        checked_path(path, directory=True, private=True)
        marker = read_json(path / 'stage.json')
        need(marker['outputs'] == file_pins(path), '阶段产物摘要已变化，请保留目录并新建准备会话')
        return marker

    def run(self, stage, **options):
        need(stage in STAGES, '阶段名称不正确，请查看 --help')
        with self.locked():
            dependencies = {}
            for earlier in STAGES[:STAGES.index(stage)]:
                need((self.root / earlier).exists(), '前序阶段尚未通过，请按 snapshot、inventory、plan、render-config、verify 顺序执行')
                dependencies[earlier] = b.digest(self.marker(earlier))
            inputs = deepcopy(options)
            for key in ('values_path', 'review_path'):
                if inputs.get(key):
                    path = inputs[key]
                    inputs[key] = {'path': str(path), 'sha256': sha(raw_file(path))}
            inputs = json.loads(json.dumps(inputs, default=str))
            binding = b.digest({'options': inputs, 'dependencies': dependencies})
            final = self.root / stage
            if final.exists():
                marker = self.marker(stage)
                need(marker['input_sha256'] == binding, '阶段输入已变化；请新建准备会话，不能覆盖已冻结产物')
                if stage in ('render-config', 'verify', 'root-commands'):
                    self._bindings()
                return {'状态': '已核对，无需重做', '阶段': stage, '私有目录': str(self.root)}
            output = Path(tempfile.mkdtemp(prefix='.' + stage + '-', dir=self.root))
            try:
                getattr(self, '_' + stage.replace('-', '_'))(output, **options)
                write_json(output / 'stage.json', {'input_sha256': binding, 'completed_at': time.time(),
                           'outputs': file_pins(output)})
                output.rename(final)
            except BaseException:
                # 失败产物不带通过标记；保留余额只读收据等私有证据，不自动重试。
                output.rename(output.with_name('failed-' + output.name.lstrip('.')))
                raise
        return {'状态': '通过', '阶段': stage, '私有目录': str(self.root)}

    def _snapshot(self, output, *, sources, writers_stopped=False, sample_seconds=1):
        need(writers_stopped is True, '尚未确认停写；请总指挥停止全部旧写者和 timer 后添加 --writers-stopped')
        need(type(sample_seconds) in (int, float) and 0 <= sample_seconds <= 10,
             '采样间隔应在 0 到 10 秒之间')
        need(set(sources) == set(SOURCES) and all(set(sources[k]) == set(v) for k, v in SOURCES.items()),
             '输入清单不完整，请按手册填写两渠道的数据库和额度审阅文件')
        paths = {channel + '/' + name: checked_path(path)
                 for channel, items in sources.items() for name, path in items.items()}
        dbs = {name: path for name, path in paths.items() if name.endswith('.sqlite3')}
        first = {name: wal_sample(path) for name, path in dbs.items()}
        json_pins = {name: sha(raw_file(path, private=False))
                     for name, path in paths.items() if not name.endswith('.sqlite3')}
        time.sleep(sample_seconds)
        second = {name: wal_sample(path) for name, path in dbs.items()}
        check_wal([first, second])
        records = {}
        for channel in SOURCES:
            target = private_dir(output, channel)
            for name in SOURCES[channel]:
                key = channel + '/' + name
                source, dest = paths[key], target / name
                started = time.time()
                if name.endswith('.sqlite3'):
                    write(dest, b'')
                    # 非空 WAL 已拒绝。immutable 使 SQLite 不创建或改写生产 SHM。
                    with closing(sqlite3.connect(source.as_uri() + '?mode=ro&immutable=1', uri=True)) as src, \
                            closing(sqlite3.connect(dest)) as dst:
                        src.execute('PRAGMA query_only=ON')
                        src.backup(dst)
                        dst.execute('PRAGMA journal_mode=DELETE')
                        need(dst.execute('PRAGMA quick_check').fetchall() == [('ok',)],
                             'SQLite 副本完整性检查失败，请重新准备快照')
                else:
                    raw = raw_file(source, private=False)
                    need(sha(raw) == json_pins[key], '额度文件在备份期间变化，请重新停写并准备快照')
                    write(dest, raw)
                records[key] = {'sha256': sha(raw_file(dest)), 'started_at': started, 'finished_at': time.time()}
        last = {name: wal_sample(path) for name, path in dbs.items()}
        check_wal([second, last])
        need(all(sha(raw_file(paths[name], private=False)) == pin for name, pin in json_pins.items()),
             '额度文件在备份期间变化，请重新准备快照')
        write_json(output / 'snapshots.json', {'schema': 'kb-install-snapshots-v1',
                   'files': records, 'wal_samples': [first, second, last], 'atomic_across_databases': False})

    def _inventory(self, output, *, expected_wechat=1600, expected_alibaba=106, expected_wechat_units=1603):
        need(all(type(v) is int and v >= 0 for v in (expected_wechat, expected_alibaba, expected_wechat_units)),
             '估算期望数量必须为非负整数')
        summary, private = self.backend.inventory(self.root / 'snapshot/wechat', self.root / 'snapshot/alibaba',
                                                  expected_wechat, expected_alibaba)
        expected = {'wechat_sources': expected_wechat, 'wechat_units': expected_wechat_units,
                    'alibaba_sources': expected_alibaba, 'alibaba_units': expected_alibaba}
        need(all(summary.get(key) == value for key, value in expected.items()),
             '盘点数量与 R5-D 估算或人工复核值不同；请审查差异后新建会话，不沿用旧估算')
        b.verify_quarantine(private['quarantine'])
        write_json(output / 'summary.json', {'counts': summary, 'expected': expected, 'tolerance': 0})
        write_json(output / 'provenance.json', private)
        write_json(output / 'quarantine.json', private['quarantine'])

    def _plan(self, output, *, after, review_path=None, review_sha256=None, bootstrap_empty=False):
        need(type(after) is int and 0 < after <= time.time(), '固定边界必须是人工核准的过去 UTC 秒数')
        quarantine = read_json(self.root / 'inventory/quarantine.json')
        snapshots = {name.replace('/', '_').replace('.sqlite3', '').replace('.json', ''): row['sha256']
                     for name, row in read_json(self.root / 'snapshot/snapshots.json')['files'].items()}
        need(bool(review_path) != bool(bootstrap_empty), '请选择原授权入口的受审审核稿，或显式 --bootstrap-empty 生成首次空清单')
        if bootstrap_empty:
            review = {'schema': 'kb-backlog-review-v1', 'after': after, 'snapshots': snapshots, 'entries': []}
        else:
            need(b.is_sha(review_sha256) and sha(raw_file(review_path)) == review_sha256,
                 '审核稿文件 SHA 不匹配，请人工核对原授权入口的输出')
            review = read_json(review_path)
            need(review['after'] == after and review['snapshots'] == snapshots,
                 '审核稿未绑定本次快照和固定边界，请通过原授权入口重新生成')
        import plan_review
        adapter = SimpleNamespace(plan_review=lambda boundary, pins: (deepcopy(review), 0))
        report = plan_review.write_review(adapter, {'after': after, 'snapshots': snapshots}, quarantine)
        reviewed = checked_path(report['私有目录'], directory=True, private=True)
        try:
            write(output / 'review.json', raw_file(reviewed / 'review.json'))
        finally:
            shutil.rmtree(reviewed)
        manifest = b.make_manifest(review, quarantine)
        b.validate_manifest(manifest, b.digest(manifest), quarantine)
        write_json(output / 'manifest.json', manifest)
        write_json(output / 'quarantine.json', quarantine)
        draft_input = private_dir(output, 'draft-input')
        for name in ('calls.sqlite3', 'usage.json', 'quota-review.json'):
            write(draft_input / name, raw_file(self.root / 'snapshot/wechat' / name))
        write_json(draft_input / 'source-quarantine.json', quarantine)
        draft = checked_path(self.backend.draft(draft_input, after), directory=True, private=True)
        release = private_dir(output, 'release')
        try:
            for name in RELEASE:
                write(release / name, raw_file(draft / name))
        finally:
            shutil.rmtree(draft)
        write_json(output / 'summary.json', {'manifest_sha256': b.digest(manifest),
                   'quarantine_sha256': b.digest(quarantine), 'items': len(manifest['items']),
                   'excluded': len(manifest['excluded']), 'bootstrap_empty': bootstrap_empty})

    def _render_config(self, output, *, values_path):
        values = read_json(values_path)
        need(set(values) == {'currency', 'request_tokens', 'rates', 'free_balances', 'max_age_seconds'},
             '现场值不完整；请填写币种、保守单次预留、各路由费率、免费余额与有效时限')
        need(values['currency'] in ('CNY', 'USD') and type(values['request_tokens']) is int
             and values['request_tokens'] > 0 and type(values['max_age_seconds']) is int
             and 0 < values['max_age_seconds'] <= 86400, '币种、保守单次预留或有效时限不正确，请现场核实')
        config = read_json(HERE / 'coordinator-config.example.json', private=False)
        baseline_raw = raw_file(HERE / 'coordinator-baseline.json', private=False)
        baseline = json.loads(baseline_raw)
        models = {model for readers in config['routes'].values() for route in readers for model in route}
        need(set(values['rates']) == models and all(type(v) is int and v >= 0 for v in values['rates'].values()),
             '必须逐一填写四个路由模型的最坏费率，单位为账户币种微元/百万 tokens')
        free = {m for m in models if config['physical']['models'][m]['bucket'].startswith('dashscope_')}
        need(set(values['free_balances']) == free, '必须分别提供两个百炼型号的免费余额观察值')
        now = time.time()
        for model in sorted(models):
            config['physical']['models'][model]['cost_micros_per_1m_tokens'] = values['rates'][model]
            if model not in free:
                need(values['rates'][model] > 0, '官方模型的最坏费率必须大于零，请核对币种与当前最高输入/输出费率')
        for model in sorted(free):
            row = values['free_balances'][model]
            need(set(row) == {'tokens', 'unknown_reserved_tokens', 'other_reserved_tokens', 'observed_at',
                             'expires_at', 'free_only_verified'}, '免费余额缺少观察时间、过期日、预留或云端用尽即停核验')
            need(all(type(row[k]) is int and row[k] >= 0 for k in
                     ('tokens', 'unknown_reserved_tokens', 'other_reserved_tokens'))
                 and type(row['free_only_verified']) is bool, '免费余额和预留应为非负整数，免费开关应显式核验')
            self._fresh(row, now, values['max_age_seconds'])
            remaining = row['tokens'] - row['unknown_reserved_tokens'] - row['other_reserved_tokens']
            need(remaining >= 0, '免费观察余额小于既有预留，请核对原账本，不释放未知预留')
            bucket = config['physical']['buckets'][config['physical']['models'][model]['bucket']]
            bucket.update(verified=row['free_only_verified'], max_tokens=remaining)
        receipts = []
        def persist_receipt(receipt):
            need(not receipts, '余额入口返回多份收据，请保留现场证据并核对，不能自动重试')
            write_json(output / 'balance-receipt.json', receipt)
            receipts.append(deepcopy(receipt))
        try:
            balance = self.backend.balance(values['currency'], persist_receipt)
        except subprocess.SubprocessError:
            raise PrepareError('官方余额查询进程超时或未完成；请保留失败目录核对现场状态，不自动重试') from None
        now = time.time()
        need(len(receipts) == 1 and balance.get('receipt') == receipts[0]
             and balance.get('currency') == values['currency'] and balance.get('available') is True
             and type(balance.get('total_micros')) is int and balance['total_micros'] >= 0,
             '官方余额查询未通过；已停止，请通过既有受审入口核对余额，不猜测或自动重试')
        self._fresh({'observed_at': balance['observed_at'], 'expires_at': now + values['max_age_seconds']},
                    now, values['max_age_seconds'])
        config['official_deepseek']['currency'] = values['currency']
        config['physical']['buckets']['deepseek_official'].update(verified=True, max_cost_micros=balance['total_micros'])
        # 物理闸门要求请求数上限非空；按各账户 token、金额上限与单次保守预留取不会比其更宽的上界。
        reservation = values['request_tokens']
        for model in sorted(free):
            bucket = config['physical']['buckets'][config['physical']['models'][model]['bucket']]
            bucket['max_requests'] = bucket['max_tokens'] // reservation
        official = [(reservation * values['rates'][m] + 999999) // 1000000 for m in sorted(models - free)]
        config['physical']['buckets']['deepseek_official']['max_requests'] = balance['total_micros'] // min(official)
        config['release_sha256'] = sha(baseline_raw)
        config['manifest_sha256'] = b.digest(read_json(self.root / 'plan/manifest.json'))
        config['quarantine_sha256'] = b.digest(read_json(self.root / 'plan/quarantine.json'))
        config['adapter'].update(loader_sha256=baseline['installed_overlay_sha256'][
            '/opt/imgw/wechat-kb-dual-live-v1/live_loader.py'], request_tokens=values['request_tokens'],
            approved_calls_sha256=sha(raw_file(self.root / 'snapshot/wechat/calls.sqlite3')))
        from coordinator import validate_config
        from physical_gate import validate_config as validate_physical
        validate_config(config)
        validate_physical(config['physical'])
        write_json(output / 'config.json', config)
        write_json(output / 'observations.json', {'free_balances': values['free_balances'],
                   'official_observed_at': balance['observed_at'], 'max_age_seconds': values['max_age_seconds']})
        write_json(output / 'summary.json', {'config_sha256': sha(raw_file(output / 'config.json'))})

    @staticmethod
    def _fresh(row, now, max_age):
        need(all(type(row[k]) in (int, float) for k in ('observed_at', 'expires_at'))
             and 0 < row['observed_at'] <= now < row['expires_at']
             and now - row['observed_at'] <= max_age, '余额观察值已过期或时间不正确，请重新观察并新建准备会话')

    def _bindings(self):
        config = read_json(self.root / 'render-config/config.json')
        baseline_raw = raw_file(HERE / 'coordinator-baseline.json', private=False)
        baseline = json.loads(baseline_raw)
        manifest = read_json(self.root / 'plan/manifest.json')
        quarantine = read_json(self.root / 'plan/quarantine.json')
        b.validate_manifest(manifest, config['manifest_sha256'], quarantine)
        need(config['quarantine_sha256'] == b.digest(quarantine)
             and config['release_sha256'] == sha(baseline_raw)
             and config['adapter']['loader_sha256'] == baseline['installed_overlay_sha256'][
                 '/opt/imgw/wechat-kb-dual-live-v1/live_loader.py']
             and config['adapter']['approved_calls_sha256'] == sha(raw_file(self.root / 'snapshot/wechat/calls.sqlite3')),
             '发布、loader、原调用账本或隔离清单 SHA 绑定不匹配，请停止安装')
        need(config['manifest_path'] == '/etc/imgw/kb-backlog-manifest-v1.json'
             and config['quarantine_path'] == '/etc/imgw/kb-source-quarantine-v1.json'
             and config['adapter']['approved_calls_path'] == STATE + '/approved-calls.sqlite3',
             '安装目标与受审配置不同，请停止安装')
        observations = read_json(self.root / 'render-config/observations.json')
        now, age = time.time(), observations['max_age_seconds']
        for row in observations['free_balances'].values():
            self._fresh(row, now, age)
        self._fresh({'observed_at': observations['official_observed_at'],
                     'expires_at': observations['official_observed_at'] + age}, now, age)
        release = self.root / 'plan/release'
        boundary = read_json(release / 'kb-live-recovery-v1.json')
        dual = read_json(release / 'kb-live-dual-v1.json')
        need(boundary['after'] == manifest['after']
             and boundary['isolation_sha256'] == sha(raw_file(release / 'kb-two-unknown-manifest.json'))
             and dual['recovery_boundary_sha256'] == sha(raw_file(release / 'kb-live-recovery-v1.json'))
             and dual['source_quarantine_sha256'] == sha(raw_file(release / 'kb-source-quarantine-v1.json'))
             and b.digest(read_json(release / 'kb-source-quarantine-v1.json')) == b.digest(quarantine),
             '原隔离、固定边界或 loader 发布清单绑定不一致，请重新核对')
        return config, baseline

    def _verify(self, output):
        config, baseline = self._bindings()
        self.backend.verify()
        overlay = checked_path(self.backend.overlay(), directory=True, private=True)
        dest = private_dir(output, 'overlay')
        try:
            for target, pin in baseline['installed_overlay_sha256'].items():
                relative = Path(target).relative_to(baseline['overlay_base'])
                raw = raw_file(overlay / relative, private=False)
                need(sha(raw) == pin, '补丁候选摘要不符，请重新核对源码基线')
                (dest / relative).parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                write(dest / relative, raw)
        finally:
            shutil.rmtree(overlay)
        dual = read_json(self.root / 'plan/release/kb-live-dual-v1.json')
        need(dual['modules'] == {p.stem: sha(raw_file(p)) for p in sorted((dest / 'video_knowledge').glob('*.py'))},
             '原 loader 模块清单不符，请重新核对发布摘要')
        runtime = private_dir(output, 'runtime')
        for name, pin in baseline['runtime_sha256'].items():
            raw = raw_file(HERE / b_path(name), private=False)
            need(sha(raw) == pin, '运行文件摘要变化，请重新审查发布基线')
            write(runtime / name, raw)
        write(runtime / 'coordinator-baseline.json', raw_file(HERE / 'coordinator-baseline.json', private=False))
        unit = raw_file(runtime / 'imgw-kb-recovery-coordinator.service').replace(
            b'REPLACE_WITH_REVIEWED_CONFIG_SHA256', sha(raw_file(self.root / 'render-config/config.json')).encode())
        write(output / 'coordinator.service', unit)
        write_json(output / 'summary.json', {'config_sha256': sha(raw_file(self.root / 'render-config/config.json')),
                   'runtime_files': len(baseline['runtime_sha256']), 'overlay_files': len(baseline['installed_overlay_sha256'])})

    def _root_commands(self, output):
        config, baseline = self._bindings()
        files = []
        def add(source, target, owner='root', mode='0640'):
            files.append({'source': str(source), 'target': target, 'owner': owner,
                          'mode': mode, 'sha256': sha(raw_file(source))})
        for name in (*baseline['runtime_sha256'], 'coordinator-baseline.json'):
            add(self.root / 'verify/runtime' / name, '/opt/imgw/kb-recovery-coordinator/' + name,
                mode='0755' if name == 'start-coordinator.sh' else '0644')
        for target in baseline['installed_overlay_sha256']:
            add(self.root / 'verify/overlay' / Path(target).relative_to(baseline['overlay_base']), target, mode='0644')
        for name in RELEASE:
            add(self.root / 'plan/release' / name, '/etc/imgw/' + name)
        add(self.root / 'plan/manifest.json', config['manifest_path'])
        add(self.root / 'render-config/config.json', '/etc/imgw/kb-recovery-coordinator.json')
        add(self.root / 'snapshot/wechat/calls.sqlite3', config['adapter']['approved_calls_path'], 'imgw', '0600')
        add(self.root / 'verify/coordinator.service', '/etc/systemd/system/imgw-kb-recovery-coordinator.service', mode='0644')
        from prepare_install_root import generate_commands
        observations = read_json(self.root / 'render-config/observations.json')
        age = observations['max_age_seconds']
        valid_until = min([observations['official_observed_at'] + age] + [
            min(row['observed_at'] + age, row['expires_at'])
            for row in observations['free_balances'].values()])
        generate_commands(files, output, sha(raw_file(self.root / 'render-config/config.json')),
                          valid_until=valid_until)
        write_json(output / 'files.json', files)


def b_path(name):
    from verify_patch import safe_name
    return safe_name(name)


def main(argv=None):
    parser = Parser(description=__doc__, add_help=False, usage='%(prog)s 阶段 [参数]')
    parser._positionals.title = '阶段'
    parser._optionals.title = '可选参数'
    parser.add_argument('-h', '--help', action='help', help='显示用法后退出')
    parser.add_argument('stage', choices=(*STAGES, 'dry-run-all'), help='按顺序执行的准备阶段，或独立虚构演练')
    parser.add_argument('--session', type=Path, help='上一阶段打印的 /tmp 私有目录')
    parser.add_argument('--sources', type=Path, help='0700 私有目录内的 0600 来源路径 JSON')
    parser.add_argument('--writers-stopped', action='store_true', help='总指挥已确认全部旧写者和 timer 停止')
    parser.add_argument('--sample-seconds', type=float, default=1, help='WAL 两次采样的间隔秒数，默认 1')
    parser.add_argument('--expected-wechat', type=int, default=1600, help='经审阅的微信候选来源数，默认 1600')
    parser.add_argument('--expected-wechat-units', type=int, default=1603, help='经审阅的微信候选单元数，默认 1603')
    parser.add_argument('--expected-alibaba', type=int, default=106, help='经审阅的阿里候选来源数，默认 106')
    parser.add_argument('--after', type=int, help='人工核准且与原边界相同的 UTC Unix 秒')
    parser.add_argument('--review', type=Path, help='原授权入口生成并经人工审阅的审核稿')
    parser.add_argument('--review-sha256', help='人工独立核对的原审核稿文件字节 SHA-256')
    parser.add_argument('--bootstrap-empty', action='store_true', help='仅生成零条积压的首次装配清单，不能代替授权核验')
    parser.add_argument('--values', type=Path, help='现场币种、费率、免费余额与保守预留 JSON')
    args = parser.parse_args(argv)
    if args.stage == 'dry-run-all':
        return dry_run_all()
    need(args.session is not None or args.stage == 'snapshot', '请使用 --session 指定上一阶段的私有目录')
    session = Session(args.session) if args.session else Session.create()
    # 先打印新目录；失败后仍可定位会话，但不打印异常的原文或输入值。
    print(b.canon({'私有目录': str(session.root), '阶段': args.stage, '状态': '开始核对'}), flush=True)
    options = {}
    if args.stage == 'snapshot':
        need(args.sources is not None, '缺少 --sources，请按手册提供路径清单')
        options = dict(sources=read_json(args.sources), writers_stopped=args.writers_stopped, sample_seconds=args.sample_seconds)
    elif args.stage == 'inventory':
        options = dict(expected_wechat=args.expected_wechat, expected_wechat_units=args.expected_wechat_units,
                       expected_alibaba=args.expected_alibaba)
    elif args.stage == 'plan':
        options = dict(after=args.after, review_path=args.review, review_sha256=args.review_sha256,
                       bootstrap_empty=args.bootstrap_empty)
    elif args.stage == 'render-config':
        need(args.values is not None, '缺少 --values，请现场核实全部必填值后再渲染')
        options = dict(values_path=args.values)
    print(b.canon(session.run(args.stage, **options)))
    return 0


def dry_run_all():
    # 仅在显式演练时导入测试夹具；运行安装版不需要 tests，也不回退到真实实现。
    import sys
    tests = HERE.parents[1] / 'tests/kb_recovery'
    need(tests.is_dir(), '演练夹具不存在，请在开发快照运行 dry-run-all')
    sys.path.insert(0, str(tests))
    from prepare_install_fixtures import run_all
    result = run_all()
    print(b.canon(result))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except PrepareError as exc:
        raise SystemExit(str(exc)) from None
    except (OSError, ValueError, TypeError, KeyError, RuntimeError, sqlite3.Error, subprocess.SubprocessError):
        raise SystemExit('准备已停止；请核对当前阶段的私有输入、源码基线和现场观察值。未安装、未启动、未请求模型') from None
