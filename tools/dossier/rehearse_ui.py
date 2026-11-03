#!/usr/bin/env python3
"""Private, isolated UI rehearsal. Production code and sources stay read only.

prepare writes ONLY under the explicit private_out directory. serve accepts GET
only, binds loopback, requires a random local token, and never imports console.app.
All access logs are disabled. It is not a production authentication mechanism.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys

sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[2]


def private_root(path):
    path = path.resolve()
    if 'private_out' not in path.parts:
        raise ValueError('rehearsal_output_must_be_private')
    return path


def prepare(args):
    from import_candidate import import_candidate
    from prepare import readonly
    root = private_root(args.output)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    candidate_hash = hashlib.sha256(args.candidate.read_bytes()).hexdigest()
    result = import_candidate(args.candidate, root / 'dossiers.sqlite3', True)
    replay = import_candidate(args.candidate, root / 'dossiers.sqlite3', True)
    assert not any(replay['inserted'].values())
    (root / 'dossiers.sqlite3').chmod(0o600)
    roots = set()
    with readonly(args.candidate) as db:
        for external, ref in db.execute("SELECT external_key,private_ref FROM source WHERE kind='nas_file'"):
            if ref and ref.startswith('/') and ref.endswith('/' + external):
                roots.add(ref[:-len(external)-1])
    if len(roots) != 1:
        raise ValueError('nas_root_requires_explicit_review')
    config = dict(nas_root=roots.pop(), wechat_db='/var/lib/imgw/wechat/api_state.sqlite3',
                  blob_root='/var/lib/imgw/wechat/owner_workspace/media_archive', media_key='/var/lib/imgw/ops-app/media.key')
    (root / 'runtime.json').write_text(json.dumps(config))
    (root / 'rehearsal-token').write_text(secrets.token_urlsafe(32))
    (root / 'prepare-result.json').write_text(json.dumps(dict(imported=result['counts'], replay_inserted=replay['inserted'], candidate_sha256=candidate_hash)))
    assert candidate_hash == hashlib.sha256(args.candidate.read_bytes()).hexdigest()
    print('PRIVATE_PREVIEW_READY; candidate unchanged; replay inserted zero')


def serve(args):
    root = private_root(args.output)
    sys.path.insert(0, str(REPO / 'src'))
    import console
    console.__path__.append('/opt/imgw/wechat/console')
    from console.dossiers import Dossiers
    from console.dossiers.sources import Sources
    from console.v3_dossiers_api import build
    from starlette.applications import Starlette
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import PlainTextResponse, JSONResponse
    from starlette.routing import Route, Mount
    from starlette.staticfiles import StaticFiles
    from types import SimpleNamespace
    import uvicorn
    token = (root / 'rehearsal-token').read_text()
    service = Dossiers(root / 'dossiers.sqlite3', resolver=Sources(config_path=root / 'runtime.json'))
    gate = SimpleNamespace(error_response=lambda r,c: PlainTextResponse('blocked',c))
    async def summary(request):
        return JSONResponse(dict(ready=False,waiting_total=0,draft_new=0),headers={'Cache-Control':'no-store'})
    async def index(request):
        from starlette.responses import Response
        return Response((args.dist / 'index.html').read_bytes(), media_type='text/html')
    routes = build(gate,service=service)
    routes += [Route('/api/v1/inbox/summary', summary), Mount('/app/assets',StaticFiles(directory=args.dist / 'assets')),Route('/app/{path:path}',index)]
    app = Starlette(routes=routes)
    async def protect(request, call_next):
        if request.method != 'GET':
            return PlainTextResponse('read only',405)
        if not secrets.compare_digest(request.headers.get('x-dossier-rehearsal',''),token):
            return PlainTextResponse('blocked',403)
        request.state.password_principal=SimpleNamespace(username='offline-readonly')
        request.state.session=SimpleNamespace(authenticated=True,csrf='')
        request.state.password_recheck=lambda:True
        response=await call_next(request)
        response.headers['Cache-Control']='no-store'
        response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'"
        return response
    app.add_middleware(BaseHTTPMiddleware,dispatch=protect)
    print('DOSSIER_READONLY_READY',flush=True)
    uvicorn.run(app,host='127.0.0.1',port=args.port,access_log=False,log_level='critical')


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=('prepare','serve'))
    parser.add_argument('--candidate',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--dist',type=Path,default=REPO / 'web/dist')
    parser.add_argument('--port',type=int,default=18936)
    args=parser.parse_args()
    os.umask(0o077)
    (prepare if args.action=='prepare' else serve)(args)
