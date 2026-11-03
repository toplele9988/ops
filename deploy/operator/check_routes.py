#!/usr/bin/env python3
"""Build the real v3 route table with every network/SQLite call forbidden."""
import argparse
import ast
import asyncio
import importlib.util
import json
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
from types import SimpleNamespace

p=argparse.ArgumentParser()
p.add_argument('candidate',type=Path)
p.add_argument('--fallback',type=Path,default=Path('/opt/imgw/wechat'))
p.add_argument('--web-root',type=Path)
p.add_argument('--output',type=Path)
a=p.parse_args()
root,fallback=a.candidate.resolve(),a.fallback.resolve()
def denied(*args,**kwargs):raise AssertionError('route validation forbids external I/O')
socket.socket.connect=denied
socket.create_connection=denied
sqlite3.connect=denied
subprocess.run=denied
sys.path[:0]=[str(root),str(fallback)]
spec=importlib.util.spec_from_file_location('console',fallback/'console/__init__.py',submodule_search_locations=[str(root/'console'),str(fallback/'console')])
module=importlib.util.module_from_spec(spec);sys.modules['console']=module;spec.loader.exec_module(module)
from console import v3
routes=v3.build(SimpleNamespace())
from console.operator.host import attach_lifecycle
from starlette.applications import Starlette
calls=[]
application=Starlette()
attach_lifecycle(application,SimpleNamespace(operator_worker=SimpleNamespace(start=lambda:calls.append('start'),stop=lambda:calls.append('stop'))))
async def lifecycle_check():
    async with application.router.lifespan_context(application):
        assert calls==['start']
asyncio.run(lifecycle_check())
assert calls==['start','stop']
seen=set()
for r in routes:
    for method in r.methods or ():
        key=(r.path,method)
        assert key not in seen, ('duplicate route',key)
        seen.add(key)
expected=[('/api/v1/operator','GET')]+[('/api/v1/operator/'+x,'POST') for x in ['settings','profiles','actions/{id}/approve','actions/{id}/edit','actions/{id}/reject']]
assert all(x in seen for x in expected)
for path in ['/api/v1/ops/queue','/api/v1/ops/drafts','/api/v1/roles']:
    assert (path,'GET') in seen,path
app=(root/'console/app.py').read_text()
ast.parse(app)
assert 'attach_operator_lifecycle(application, sys.modules[__name__])' in app
web_root=a.web_root if a.web_root else root/'web'
web=(web_root/'src/App.tsx').read_text();nav=(web_root/'src/nav.ts').read_text()
assert web.count('<Route path="operator" element={<Operator />} />')==1
assert nav.count('key: "operator"')==1
assert '"operator",' in nav
result=dict(routes=len(routes),pairs=len(seen),operator_endpoints=len(expected),lifespan=True,network_calls=0,sqlite_calls=0)
if a.output:a.output.write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result))
