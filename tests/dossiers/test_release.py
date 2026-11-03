"""Install/replay/rollback in disposable code roots, never a production target."""
import json
import hashlib
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import sys

import pytest

REPO=Path(__file__).resolve().parents[2]
BUNDLE=REPO/'deploy/dossiers'


@pytest.mark.parametrize('kind',['original','production'])
def test_install_replay_and_rollback(env,kind):
    root=env.root/'code';(root/'console/ops_queue').mkdir(parents=True)
    for name in ('console/v3.py','console/ops_queue/service.py'):
        shutil.copyfile(Path('/opt/imgw/wechat')/name,root/name)
    # Reconstruct either exact companion baseline without depending on whether
    # the independently managed private package has since been installed live.
    queue=root/'console/ops_queue/service.py'
    want=dict(line.split('  ',1)[::-1] for line in (BUNDLE/(kind+'.baseline.sha256')).read_text().splitlines())['console/ops_queue/service.py']
    if hashlib.sha256(queue.read_bytes()).hexdigest()!=want:
        lines=(REPO/'deploy/wechat-private/production.patch').read_text().splitlines(True)
        start=lines.index('--- a/console/ops_queue/service.py\n')
        end=next((i for i in range(start+1,len(lines)) if lines[i].startswith('--- a/')),len(lines))
        patch=env.root/'companion.patch';patch.write_text(''.join(lines[start:end]))
        p=subprocess.run(['patch','--batch','--fuzz=0',*(['-R'] if kind=='original' else []),'-p1','-i',str(patch)],cwd=root,capture_output=True)
        assert p.returncode==0,p.stderr.decode()
    assert hashlib.sha256(queue.read_bytes()).hexdigest()==want
    baseline={name:(root/name).read_bytes() for name in ('console/v3.py','console/ops_queue/service.py')}
    state=env.root/'state';backup=env.root/'backup'
    user=pwd.getpwuid(os.getuid()).pw_name
    # Disposable bundle and fictional static assets keep pytest independent of npm.
    bundle=env.root/'bundle';shutil.copytree(BUNDLE,bundle)
    web=env.root/'build';web.mkdir();(web/'index.html').write_text('fictional new interface')
    (bundle/'dist.sha256').write_text(hashlib.sha256((web/'index.html').read_bytes()).hexdigest()+'  index.html\n')
    webtarget=env.root/'dist';webtarget.mkdir();(webtarget/'index.html').write_text('fictional old interface')
    args=[sys.executable,'-B',str(bundle/'release.py'),'install','--root',str(root),'--state',str(state),'--candidate',str(env.candidate),'--backup',str(backup),'--nas-root',str(env.nas),'--wechat-db',str(env.wx),'--blob-root',str(env.root/'blobs'),'--media-key',str(env.key),'--owner',user,'--python',sys.executable,'--web-dist',str(web),'--web-target',str(webtarget)]
    p=subprocess.run(args,capture_output=True)
    assert p.returncode==0,p.stderr.decode()
    result=json.loads(p.stdout)
    assert result['variant']==kind and result['restarted'] is False
    assert result['frontend_published'] and (webtarget/'index.html').read_text()=='fictional new interface'
    assert (backup/'routes.json').exists()
    for name in ('dossiers.sqlite3','drafts.sqlite3','runtime.json'):
        assert (state/name).stat().st_mode & 0o777==0o600
        assert (state/name).stat().st_uid==os.getuid()
    args[args.index(str(backup))]=str(env.root/'backup-replay')
    p=subprocess.run(args,capture_output=True)
    assert p.returncode==0,p.stderr.decode()
    assert not any(json.loads(p.stdout)['imported'].values())
    # Restore original install backup: retain dossiers and drafts, remove only new code.
    p=subprocess.run([sys.executable,'-B',str(bundle/'release.py'),'rollback','--backup',str(backup)],capture_output=True)
    assert p.returncode==0,p.stderr.decode()
    for name,raw in baseline.items():assert (root/name).read_bytes()==raw
    assert not (root/'console/v3_dossiers_api.py').exists()
    assert (state/'drafts.sqlite3').exists() and (state/'dossiers.sqlite3').exists()
    assert (webtarget/'index.html').read_text()=='fictional old interface'


def test_baseline_drift_fails_before_writes(env):
    root=env.root/'drift';(root/'console/ops_queue').mkdir(parents=True)
    for name in ('console/v3.py','console/ops_queue/service.py'):
        shutil.copyfile(Path('/opt/imgw/wechat')/name,root/name)
    (root/'console/v3.py').write_text('fictional drift')
    dest=env.root/'dest'
    p=subprocess.run([sys.executable,'-B',str(BUNDLE/'prepare.py'),'--source-root',str(root),'--destination',str(dest)],capture_output=True)
    assert p.returncode!=0 and not dest.exists()
