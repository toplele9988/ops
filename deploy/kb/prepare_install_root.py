"""只生成待 root 审阅执行的首装与回滚清单，不执行安装或服务操作。"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import shlex
import stat

HERE = Path(__file__).resolve().parent
RUNTIME = '/opt/imgw/kb-recovery-coordinator/'
STATE = '/var/lib/imgw/ops-app/kb-recovery-coordinator'
CONFIG = '/etc/imgw/kb-recovery-coordinator.json'
UNIT = '/etc/systemd/system/imgw-kb-recovery-coordinator.service'


def need(ok, message='安装命令输入不完整，请核对私有产物和安装目标'):
    if not ok:
        raise ValueError(message)


def allowed_targets():
    baseline = json.loads((HERE / 'coordinator-baseline.json').read_text())
    result = {RUNTIME + name for name in baseline['runtime_sha256']}
    result.add(RUNTIME + 'coordinator-baseline.json')
    result.update(baseline['installed_overlay_sha256'])
    result.update('/etc/imgw/' + name for name in (
        'kb-two-unknown-manifest.json', 'kb-source-quarantine-v1.json',
        'kb-live-recovery-v1.json', 'kb-live-dual-v1.json',
        'kb-backlog-manifest-v1.json', 'kb-recovery-coordinator.json'))
    return result | {UNIT, STATE + '/approved-calls.sqlite3'}


# 所有实际动作都留在生成文件中；源路径通过 shell 引号作为数据传入。
ROOT_PROGRAM = r'''
import hashlib
import json
import os
from pathlib import Path
import pwd
import stat
import subprocess
import sys
import time

spec = json.loads(sys.argv[1])
state = Path('/var/lib/imgw/ops-app/kb-recovery-coordinator')
backup = Path(spec['backup'])
service = 'imgw-kb-recovery-coordinator.service'
old_units = ('imgw-kb-live.service', 'icbu-conversation-analysis.service',
             'icbu-conversation-analysis.timer')

def need(ok, message):
    if not ok:
        raise ValueError(message)

def chain(path):
    path = Path(path)
    private_state = path == state or state in path.parents
    need(path.is_absolute() and '..' not in path.parts, '路径不安全，停止安装或回滚')
    for parent in reversed((path, *path.parents)):
        if parent.exists() or parent.is_symlink():
            need(not parent.is_symlink(), '路径含符号链接，停止安装或回滚')
            info = parent.lstat()
            if parent == path and stat.S_ISREG(info.st_mode):
                need(info.st_nlink == 1, '目标文件存在硬链接，停止安装或回滚')
            else:
                need(stat.S_ISDIR(info.st_mode), '父路径不是目录，停止安装或回滚')
            if not private_state:
                need(info.st_uid == 0 and not info.st_mode & 0o022,
                     '目标父路径权限不安全，交总指挥核对')

def source_bytes(item):
    path = Path(item['source'])
    need(not any(p.is_symlink() for p in (path, *path.parents)),
         '私有产物路径含符号链接，请重新准备')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1,
             '私有产物不是单链接普通文件，请重新准备')
        raw = stream.read()
    need(hashlib.sha256(raw).hexdigest() == item['sha256'],
         '私有产物摘要变化，停止并重新审阅')
    return raw

def write_private(path, raw):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())

def inactive(unit):
    result = subprocess.run(['systemctl', 'show', '--property=ActiveState', '--value', unit],
                            capture_output=True, text=True, check=False)
    need(result.returncode == 0 and result.stdout.strip() in ('inactive', 'failed'),
         '旧写者或协调器仍活动或状态未知；请总指挥先停写，再重新执行')

def install():
    need(spec['valid_until'] is not None and time.time() <= spec['valid_until'],
         '余额观察已过期或缺少有效期；请重新查询余额并生成安装清单')
    for unit in (*old_units, service):
        inactive(unit)
    for path, expected in spec['production_source_sha256'].items():
        chain(path)
        need(hashlib.sha256(Path(path).read_bytes()).hexdigest() == expected,
             '生产参考源码摘要变化，禁止覆盖；请重新审查基线')
    for path, expected in spec['overlay_before'].items():
        chain(path)
        if not Path(path).exists() and path in spec['overlay_allow_absent']:
            continue
        need(hashlib.sha256(Path(path).read_bytes()).hexdigest() in expected,
             '原 overlay 不是受审基线或候选，禁止覆盖；请重新审查')
    for item in spec['files']:
        chain(item['target'])
        source_bytes(item)
    chain(state)
    if state.exists():
        need(not any(state.iterdir()), '状态目录已有内容，禁止覆盖旧账本；需先审计策略迁移')
    chain(backup)
    need(not backup.exists(), '安装备份目录已存在；请先核对上次结果或执行回滚')
    backup.mkdir(mode=0o700)
    records = []
    for index, item in enumerate(spec['files']):
        path = Path(item['target'])
        record = {'target': str(path), 'existed': path.exists(),
                  'ledger': str(path) == str(state / 'approved-calls.sqlite3'),
                  'installed_sha256': item['sha256']}
        if path.exists():
            info = path.stat()
            need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1,
                 '已有目标不是单链接普通文件，停止安装')
            raw = path.read_bytes()
            name = str(index) + '.backup'
            write_private(backup / name, raw)
            record.update(name=name, sha256=hashlib.sha256(raw).hexdigest(),
                          uid=info.st_uid, gid=info.st_gid, mode=stat.S_IMODE(info.st_mode),
                          atime_ns=info.st_atime_ns, mtime_ns=info.st_mtime_ns)
        records.append(record)
    raw = json.dumps(records, ensure_ascii=False, sort_keys=True).encode()
    write_private(backup / 'files.json', raw)
    write_private(backup / 'files.sha256', hashlib.sha256(raw).hexdigest().encode())
    account = pwd.getpwnam('imgw')
    for item in spec['files']:
        path = Path(item['target'])
        missing = []
        parent = path.parent
        while not parent.exists():
            missing.append(parent)
            parent = parent.parent
        for parent in reversed(missing):
            parent.mkdir(mode=0o700 if parent == state else 0o755)
        if state == path.parent:
            os.chown(state, account.pw_uid, account.pw_gid)
            os.chmod(state, 0o700)
        raw = source_bytes(item)
        temp = path.parent / ('.kb-prepare-' + str(os.getpid()))
        write_private(temp, raw)
        try:
            uid = account.pw_uid if item['owner'] == 'imgw' else 0
            gid = account.pw_gid if item['owner'] == 'imgw' or str(path).startswith('/etc/imgw/') else 0
            os.chown(temp, uid, gid)
            os.chmod(temp, int(item['mode'], 8))
            os.replace(temp, path)
        finally:
            if temp.exists():
                temp.unlink()
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
    subprocess.run(['/opt/imgw/venv/bin/python',
                    '/opt/imgw/kb-recovery-coordinator/verify_coordinator.py', '--installed'],
                   check=True, env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
    write_private(backup / 'installed', spec['config_sha'].encode())
    print('安装文件和摘要核对通过；服务未启动。首次只读盘点由总指挥执行。')

def preserve_state():
    evidence = backup / ('rollback-evidence-' + str(time.time_ns()))
    evidence.mkdir(mode=0o700)
    # 这里只保全当前文件字节；停止后 WAL 仍可能存在，不能将此目录回灌业务库。
    if state.exists():
        for index, path in enumerate(sorted(state.iterdir())):
            need(not path.is_symlink(), '状态目录含符号链接，请人工保全后再回滚')
            info = path.stat()
            need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1,
                 '状态目录含非普通文件，请人工保全后再回滚')
            write_private(evidence / (str(index) + '.evidence'), path.read_bytes())
        write_private(evidence / 'names.json',
                      json.dumps([p.name for p in sorted(state.iterdir())], ensure_ascii=False).encode())

def rollback():
    subprocess.run(['systemctl', 'stop', service], check=True)
    inactive(service)
    with open('/dev/tty', 'w') as prompt, open('/dev/tty', 'r') as terminal:
        prompt.write('此脚本只保全新状态目录。请先另行保全原 calls、usage、Catalog、'
                     '阿里库与收据，并核对已无在途请求；确认后输入“无在途请求且原库已保全”：')
        prompt.flush()
        need(terminal.readline().strip() == '无在途请求且原库已保全',
             '尚未确认在途请求和原库保全，已停协调器，请保全账本后继续')
    for unit in old_units:
        inactive(unit)
    chain(backup)
    raw = (backup / 'files.json').read_bytes()
    need(hashlib.sha256(raw).hexdigest() == (backup / 'files.sha256').read_text(),
         '备份清单摘要不符，停止回滚并人工核对')
    records = json.loads(raw)
    need({r['target'] for r in records} == {r['target'] for r in spec['files']},
         '备份目标不符，停止回滚')
    for record in records:
        chain(record['target'])
        if record['existed']:
            path = backup / record['name']
            need(not path.is_symlink() and path.stat().st_nlink == 1
                 and hashlib.sha256(path.read_bytes()).hexdigest() == record['sha256'],
                 '备份文件摘要不符，停止回滚')
    preserve_state()
    for record in records:
        if record['ledger']:
            continue
        path = Path(record['target'])
        if record['existed']:
            temp = path.parent / ('.kb-rollback-' + str(os.getpid()))
            write_private(temp, (backup / record['name']).read_bytes())
            try:
                os.chown(temp, record['uid'], record['gid'])
                os.chmod(temp, record['mode'])
                os.utime(temp, ns=(record['atime_ns'], record['mtime_ns']))
                os.replace(temp, path)
            finally:
                if temp.exists():
                    temp.unlink()
        elif path.exists():
            need(hashlib.sha256(path.read_bytes()).hexdigest() == record['installed_sha256'],
                 '首次新文件已被修改，停止删除并人工核对')
            path.unlink()
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
    print('原代码和配置已恢复，状态证据已保留；未回灌账本，旧写者和 timer 继续停用。')

try:
    need(os.geteuid() == 0, '此清单只能由总指挥审阅后以 root 执行')
    if spec['action'] == 'install':
        install()
    else:
        rollback()
except ValueError as error:
    raise SystemExit(str(error)) from None
except (OSError, KeyError, TypeError, EOFError, subprocess.SubprocessError):
    raise SystemExit('文件操作已停止；请核对停写状态、路径权限、SHA 和私有备份，再决定下一步') from None
'''


def generate_commands(files: list[dict], directory: Path, config_sha: str,
                      valid_until: float | None = None) -> None:
    directory = Path(directory)
    need(directory.is_absolute() and '..' not in directory.parts
         and directory.is_relative_to('/tmp')
         and not any(p.is_symlink() for p in (directory, *directory.parents)),
         '命令目录必须为 /tmp 中无链接的私有目录')
    info = directory.stat()
    need(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid()
         and stat.S_IMODE(info.st_mode) == 0o700, '命令目录必须由当前用户持有且权限为 0700')
    need(re.fullmatch(r'[0-9a-f]{64}', config_sha or ''))
    need(valid_until is None or type(valid_until) in (float, int)
         and math.isfinite(valid_until) and valid_until > 0,
         '余额观察有效期无效，请重新核对')
    files = [dict(item, source=str(item['source'])) for item in files]
    targets, allowed = set(), allowed_targets()
    for item in files:
        need(set(item) == {'source', 'target', 'sha256', 'owner', 'mode'})
        source, target = Path(item['source']), item['target']
        need(source.is_absolute() and '..' not in source.parts
             and source.is_relative_to('/tmp')
             and not any(p.is_symlink() for p in (source, *source.parents)))
        info = source.stat()
        need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
             and info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o600,
             '安装来源必须为当前用户持有的 0600 私有单链接文件')
        parent_info = source.parent.stat()
        need(parent_info.st_uid == os.geteuid() and stat.S_IMODE(parent_info.st_mode) == 0o700,
             '安装来源目录必须由当前用户持有且权限为 0700')
        need(re.fullmatch(r'[0-9a-f]{64}', item['sha256'] or '')
             and hashlib.sha256(source.read_bytes()).hexdigest() == item['sha256'],
             '安装来源摘要变化，请重新准备产物')
        need(target in allowed and target not in targets, '安装目标不在固定清单或重复，请重新核对')
        targets.add(target)
        ledger = target == STATE + '/approved-calls.sqlite3'
        expected_mode = ('0600' if ledger else '0640' if target.startswith('/etc/imgw/')
                         else '0755' if target == RUNTIME + 'start-coordinator.sh' else '0644')
        need(item['owner'] == ('imgw' if ledger else 'root') and item['mode'] == expected_mode,
             '安装权限不符合受审契约，请重新核对')
    need(any(f['target'] == CONFIG and f['sha256'] == config_sha for f in files),
         '缺少与配置 SHA 绑定的运行配置，请先渲染并核对')
    backup = '/var/backups/imgw-kb-prepare-' + directory.name
    release = json.loads((HERE / 'coordinator-baseline.json').read_text())
    overlay = json.loads((HERE / 'baseline.json').read_text())
    before = {str(Path(overlay['base']) / name): [pin]
              for name, pin in overlay['sha256'].items()}
    allow_absent = []
    for path, pin in release['installed_overlay_sha256'].items():
        if path not in before:
            allow_absent.append(path)
            before[path] = []
        before[path].append(pin)
    for action, name in (('install', 'install-root.sh'), ('rollback', 'rollback-root.sh')):
        payload = json.dumps({'files': files, 'backup': backup, 'config_sha': config_sha,
                              'action': action, 'valid_until': valid_until,
                              'production_source_sha256': release['production_source_sha256'],
                              'overlay_before': before, 'overlay_allow_absent': allow_absent},
                             ensure_ascii=False, sort_keys=True)
        raw = ('#!/bin/sh\n# 待总指挥审阅后由 root 执行；不启动服务，不调用模型。\n'
               'set -eu\numask 077\n/opt/imgw/venv/bin/python - '
               + shlex.quote(payload) + " <<'KB_PREPARE_ROOT_PY'\n"
               + ROOT_PROGRAM + '\nKB_PREPARE_ROOT_PY\n').encode()
        path = directory / name
        if path.exists():
            info = path.lstat()
            need(not path.is_symlink() and info.st_nlink == 1 and info.st_uid == os.geteuid()
                 and stat.S_IMODE(info.st_mode) == 0o600
                 and path.read_bytes() == raw, '已有命令清单变化，请新建准备目录')
            continue
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
