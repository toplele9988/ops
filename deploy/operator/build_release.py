#!/usr/bin/env python3
"""Rebuild the public source-only package from the observed production baseline.

Run only during review. Installer never refreshes or bypasses a hash mismatch.
"""
import difflib
from pathlib import Path
import hashlib
import shutil

HERE=Path(__file__).resolve().parent
REPO=HERE.parents[1]
BACKEND=Path('/opt/imgw/wechat')
WEB=Path('/srv/imgw-dev/ops/web')
SHARED={'console/v3.py':BACKEND/'console/v3.py','console/app.py':BACKEND/'console/app.py',
        'web/src/App.tsx':WEB/'src/App.tsx','web/src/nav.ts':WEB/'src/nav.ts'}


def additions(name,text):
    if name=='console/v3.py':
        anchor='    routes += v3_roles_api.build(app_module)\n'
        assert text.count(anchor)==1
        return text.replace(anchor,anchor+'    from .operator import host as operator_host\n    routes += operator_host.build(app_module)\n')
    if name=='console/app.py':
        anchor='from .password_candidate.runtime import build as build_password_console\n'
        assert text.count(anchor)==1
        return text.replace(anchor,'from .operator.host import attach_lifecycle as attach_operator_lifecycle\nattach_operator_lifecycle(application, sys.modules[__name__])\n'+anchor)
    if name=='web/src/App.tsx':
        anchor='import { RolesPage } from "./pages/Roles";\n'
        assert text.count(anchor)==1
        text=text.replace(anchor,anchor+'import { Operator } from "./pages/Operator";\n')
        anchor='              <Route path="roles" element={<RolesPage />} />\n'
        assert text.count(anchor)==1
        return text.replace(anchor,anchor+'              <Route path="operator" element={<Operator />} />\n')
    if name=='web/src/nav.ts':
        anchor=next(line for line in text.splitlines(keepends=True) if '{ key: "roles",' in line)
        text=text.replace(anchor,anchor+'  { key: "operator", label: "AI 运营员", path: "/operator", kind: "live", area: "front", group: "日常工作", icon: Bot },\n')
        anchor='"search", "roles",'
        assert text.count(anchor)==1
        return text.replace(anchor,anchor+'\n  "operator",')
    raise ValueError(name)


def main():
    baseline,candidate,patch=[],[],[]
    for name,path in SHARED.items():
        before=path.read_text();after=additions(name,before)
        baseline.append(hashlib.sha256(before.encode()).hexdigest()+'  '+name+'\n')
        candidate.append(hashlib.sha256(after.encode()).hexdigest()+'  '+name+'\n')
        patch.extend(difflib.unified_diff(before.splitlines(keepends=True),after.splitlines(keepends=True),fromfile='a/'+name,tofile='b/'+name,n=2))
    payload=HERE/'payload'
    if payload.exists():shutil.rmtree(payload)
    files={}
    for path in (REPO/'src/console/operator').glob('*.py'):files['console/operator/'+path.name]=path
    files['console/v3_operator_api.py']=REPO/'src/console/v3_operator_api.py'
    files['web/src/pages/Operator.tsx']=REPO/'web/src/pages/Operator.tsx'
    for path in (REPO/'web/src/pages/operator').glob('*'):
        if path.is_file():files['web/src/pages/operator/'+path.name]=path
    manifest=[]
    for name,path in sorted(files.items()):
        observed=WEB/name[4:] if name.startswith('web/') else BACKEND/name
        if observed.exists():
            before,after=observed.read_text(),path.read_text()
            baseline.append(hashlib.sha256(before.encode()).hexdigest()+'  '+name+'\n')
            candidate.append(hashlib.sha256(after.encode()).hexdigest()+'  '+name+'\n')
            patch.extend(difflib.unified_diff(before.splitlines(keepends=True),after.splitlines(keepends=True),fromfile='a/'+name,tofile='b/'+name,n=2))
            continue
        target=payload/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(path,target)
        manifest.append(hashlib.sha256(path.read_bytes()).hexdigest()+'  '+name+'\n')
    for name,lines in [('baseline.sha256',baseline),('candidate.sha256',candidate),('production.patch',patch)]:
        (HERE/name).write_text(''.join(lines))
    (HERE/'payload.sha256').write_text(''.join(manifest))
    # The package touches none of these paths; both complete audited states are
    # accepted, without combining pieces from different prerequisite versions.
    for name in ('baseline','candidate'):
        shutil.copyfile(REPO/'deploy/wechat-private'/(name+'.sha256'),HERE/('wechat-private-'+name+'.sha256'))
    assert not set(SHARED)&{line.split('  ',1)[1].strip() for line in (HERE/'wechat-private-candidate.sha256').read_text().splitlines()}
    print('release built:',len(baseline),'patched files,',len(manifest),'new files; no overlap with wechat-private candidate')


if __name__=='__main__':main()
