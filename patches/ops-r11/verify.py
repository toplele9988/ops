"""Strict patch rehearsal against real source baselines; all mutations in private_out."""
from pathlib import Path
import ast
import hashlib
import json
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
BUNDLE = Path(__file__).resolve().parent
OUT = ROOT / 'private_out/r11'
STAGE = OUT / 'production-stage'
assert not STAGE.exists(), 'use a fresh stage directory'
STAGE.mkdir()
# Copy installed code, never production data or configuration.
shutil.copytree('/opt/imgw/wechat/console', STAGE / 'console',
    ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.bak*', '*.old-*', '*.log', '*.db*', '*.sqlite*'))

def check(manifest):
    for line in (BUNDLE / manifest).read_text().splitlines():
        expected, name = line.split()
        assert hashlib.sha256((STAGE / name).read_bytes()).hexdigest() == expected, name


def patch(*args):
    p = subprocess.run(['patch', '--batch', '--fuzz=0', '-p1', '-d', str(STAGE), '-i', str(BUNDLE / 'production.patch'), *args], capture_output=True, text=True)
    print(p.stdout, end='')
    assert p.returncode == 0, p.stderr

check('baseline.sha256')
for name in (BUNDLE / 'absent.list').read_text().splitlines():
    assert not (STAGE / name).exists()
patch('--dry-run'); patch(); check('candidate.sha256')
for line in (BUNDLE / 'candidate.sha256').read_text().splitlines():
    ast.parse((STAGE / line.split()[1]).read_text())
patch('-R', '--dry-run'); patch('-R'); check('baseline.sha256')
for name in (BUNDLE / 'absent.list').read_text().splitlines():
    assert not (STAGE / name).exists()
# Leave the candidate installed in the disposable source tree for route and data rehearsal.
patch(); check('candidate.sha256')
# Verify the rehearsal patch independently against its original workspace baseline.
rehearsal = OUT / 'rehearsal-patch-stage/scripts'
rehearsal.mkdir(parents=True)
source = BUNDLE / 'rehearsal.baseline.py'
shutil.copyfile(source, rehearsal / 'rehearsal_server.py')
for args in [('--dry-run',), (), ('-R', '--dry-run'), ('-R',)]:
    p = subprocess.run(['patch', '--batch', '--fuzz=0', '-p1', '-d', str(rehearsal.parent), '-i', str(BUNDLE / 'rehearsal.patch'), *args], capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr
assert (rehearsal / 'rehearsal_server.py').read_bytes() == source.read_bytes()
print('PASS: production/rehearsal forward dry-run, apply, reverse dry-run, reverse, SHA and syntax')
