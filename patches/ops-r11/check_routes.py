from pathlib import Path
from types import SimpleNamespace
import socket, sqlite3, json
import argparse
parser = argparse.ArgumentParser(description='Build routes with network and live SQLite disabled')
parser.add_argument('source_root', type=Path)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
stage = args.source_root.resolve()
import sys
sys.path.insert(0,str(stage))
sys.path.append('/opt/imgw/wechat')
def no_network(*a, **k): raise AssertionError('route validation forbids network')
socket.socket.connect = no_network
socket.create_connection = no_network
original = sqlite3.connect
def connect(path, *a, **kw):
    if str(path) != ':memory:' and str(stage.parent) not in str(path):
        raise AssertionError('route validation forbids production SQLite')
    return original(path,*a,**kw)
sqlite3.connect = connect
from console import v3
from starlette.responses import PlainTextResponse
gate = SimpleNamespace(error_response=lambda r,c,incident=None: PlainTextResponse('error',c))
routes = v3.build(gate)
seen = {}
for r in routes:
    for method in r.methods or ():
        key = (r.path,method)
        assert key not in seen, (key, seen.get(key),r.name)
        seen[key] = r.name
for path, method in [('/api/v1/ops/queue','GET'),('/api/v1/ops/drafts','GET'),('/api/v1/parties-ops','POST'),('/api/v1/inbox/conversations/{key}/ops/action','POST'),('/api/v1/deals/{id}/drafts','POST'),('/api/v1/quotes/{id}/drafts','POST')]: assert (path,method) in seen, path
args.output.write_text(json.dumps([dict(path=r.path,methods=sorted(r.methods or ()),name=r.name) for r in routes],indent=2))
print('PASS',len(routes),'routes,',len(seen),'unique path/method pairs; required r11 endpoints present')
