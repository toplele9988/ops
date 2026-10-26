"""Temporary Node-only fixtures, with common Node network APIs blocked.

This is an API-level guard, not an operating-system network sandbox.
"""
import json, os, pathlib, shutil, subprocess, tempfile

source=pathlib.Path(__file__).resolve().parent
with tempfile.TemporaryDirectory(prefix='probe-synthetic-test-') as directory:
    root=pathlib.Path(directory)
    for file in source.glob('*.js'):shutil.copyfile(file,root/file.name)
    hook=root/'block-network.cjs'
    hook.write_text("const net=require('node:net');net.Socket.prototype.connect=function(){throw new Error('network disabled')};for(const m of ['node:http','node:https','node:tls','node:dns']){const x=require(m);for(const k of ['request','get','connect','lookup','resolve'])if(typeof x[k]==='function')x[k]=()=>{throw new Error('network disabled')};}\n")
    env={'PATH':os.environ.get('PATH',''),'TMPDIR':directory,'NODE_OPTIONS':'--require='+str(hook)}
    failed=False
    for name in ('test-entry-status.js','test-human-pause.js','test-human-node.js','test-pwa-node.js'):
        proc=subprocess.run(['node',name],cwd=root,env=env,capture_output=True,text=True,timeout=15)
        print(json.dumps({'test':name,'exit':proc.returncode,'stdout':proc.stdout,'stderr':proc.stderr},ensure_ascii=False))
        failed |= proc.returncode!=0
    if failed:raise SystemExit(1)
