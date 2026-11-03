"""Local rehearsal of the whole console with REAL read-only data and a FAKE login.

Never for production. It binds 127.0.0.1 only, refuses to start without IMGW_REHEARSAL=1, and every store it writes
(drafts, outbox, actions, archive) is a throwaway copy, so clicking around cannot change real business data.
It serves the web app with the same strict Content-Security-Policy the console enforces, so styling and script
problems show up here exactly as they would live. Used by scripts/e2e_smoke.sh.
"""
import os
import shutil
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

if os.environ.get('IMGW_REHEARSAL') != '1':
    sys.exit('refusing to start: set IMGW_REHEARSAL=1 (this server fakes authentication)')

# The public checkout deliberately omits optional media/legacy packages.
# Use installed read-only code only for those gaps; candidate modules stay first.
import console
optional_console = Path('/opt/imgw/wechat/console')
if optional_console.is_dir():
    console.__path__.append(str(optional_console))

from starlette.applications import Starlette
from starlette.responses import PlainTextResponse

from console import (v3_app, v3_archive_api, v3_channels_api, v3_customers_api, v3_deals_api, v3_inbox_api,
                     v3_inspiration_api, v3_kb_api, v3_media_api, v3_outbox_api, v3_packing_api)
from console.channels.whatsapp import CONFIG_PATH

PORT = int(os.environ.get('REHEARSAL_PORT', '18902'))
if PORT == 8791:
    sys.exit('refusing production port')
REAL_ARCHIVE = Path(os.environ.get('REAL_ARCHIVE', '/var/lib/imgw/ops-app/archive.sqlite3'))
CSP = "default-src 'self'; img-src 'self' data:; script-src 'self'; style-src 'self'; frame-ancestors 'none'"


class Gate:
    def error_response(self, request, code, incident=None):
        return PlainTextResponse('err', status_code=code)


def snapshot_copy(source, target):
    """Copy a stable main/WAL pair without letting SQLite create live sidecars.

    Read-only WAL databases can still need a writable shm file when it is absent.
    Copy files first and open SQLite only in the private disposable directory.
    A changed source fingerprint rejects the attempt; no immutable live read.
    """
    source, target = Path(source), Path(target)
    if not source.exists():
        return str(target)
    files = [source, Path(str(source) + '-wal')]
    def stamp():
        return [(p.stat().st_size, p.stat().st_mtime_ns, p.stat().st_ino) if p.exists() else None for p in files]
    for _ in range(5):
        before = stamp()
        for original, suffix in zip(files, ('', '-wal')):
            dest = Path(str(target) + suffix)
            if dest.exists():
                dest.unlink()
            if original.exists():
                shutil.copyfile(original, dest)
                dest.chmod(0o600)
        if before == stamp():
            with closing(sqlite3.connect(target)) as db:
                if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                    raise RuntimeError('source snapshot validation failed')
            return str(target)
    raise RuntimeError('source changed during snapshot; retry rehearsal later')


def archive_copy(tmp):
    return snapshot_copy(REAL_ARCHIVE, Path(tmp) / 'archive.sqlite3')


def build_routes(tmp, *, copy_sources=False):
    gate = Gate()
    archive = archive_copy(tmp) if copy_sources or 'REAL_ARCHIVE' in os.environ else str(Path(tmp) / 'archive.sqlite3')
    from console.channels import build_inbox
    from console.inbox.store import INBOUND_PATH, WECHAT_PATH
    inbound = snapshot_copy(INBOUND_PATH, Path(tmp) / 'inbound.sqlite3') if copy_sources else str(Path(tmp) / 'disabled-inbound.sqlite3')
    wechat = snapshot_copy(WECHAT_PATH, Path(tmp) / 'wechat.sqlite3') if copy_sources else str(Path(tmp) / 'disabled-wechat.sqlite3')
    inbox = build_inbox(inbound, wechat_path=wechat, whatsapp_config_path=Path(tmp) / 'disabled-whatsapp.json')
    from console.inbox.migrate import migrate
    from console.customers.operations import Parties
    migrate(archive, tmp + '/outbox.sqlite3', tmp + '/drafts.sqlite3')
    Parties(archive, inbox=inbox).initialize()
    routes = (v3_inspiration_api.build(gate) + v3_packing_api.build(gate, state_path=tmp + '/actions.sqlite3')
              + v3_inbox_api.build(gate, inbox=inbox, drafts_path=tmp + '/drafts.sqlite3', archive_path=archive, outbox_path=tmp + '/outbox.sqlite3', whatsapp_config_path=Path(tmp) / 'disabled-whatsapp.json', classify_path=tmp + '/classify.sqlite3', runner=lambda _: (_ for _ in ()).throw(RuntimeError('AI disabled in rehearsal'))) + v3_customers_api.build(gate, inbox=inbox)
              + v3_channels_api.build(gate, inbox=inbox) + v3_outbox_api.build(gate, inbox=inbox, outbox_path=tmp + '/outbox.sqlite3', config_path=tmp + '/disabled-whatsapp.json')
              + v3_media_api.build(gate) + v3_kb_api.build(gate) + v3_archive_api.build(gate, archive)
              + v3_deals_api.build(gate, archive, inbox=inbox))
    routes += v3_customers_api.build_parties(gate, archive, inbox=inbox)
    from console import v3_ops_drafts_api, v3_ops_queue_api
    routes += v3_ops_drafts_api.build(gate, archive, tmp + '/outbox.sqlite3')
    from console.ops_queue.service import OpsQueue
    from console.overview import Overview
    from console.pipeline import PipelineHealth
    routes += v3_ops_queue_api.build(gate, service=OpsQueue(paths={'archive': archive, 'inbound': inbound, 'wechat': wechat}, overview=Overview(), pipeline=PipelineHealth()))
    from console import v3_deal_workflow_api
    routes += v3_deal_workflow_api.build(gate, archive, inbox=inbox, runner=None)
    from console import v3_alibaba_open_api
    routes += v3_alibaba_open_api.build(gate, archive, credentials_path=tmp + '/alibaba-open-unconfigured.json')
    from console import v3_classify_api
    from console.directory import WechatDirectory
    from console.lang import Lang
    directory = WechatDirectory(cache_path=Path(tmp) / 'directory.sqlite3')
    routes += v3_classify_api.build(gate, path=tmp + '/classify.sqlite3', archive_path=archive, inbox=inbox, directory=directory, lang=Lang(Path(tmp) / 'lang.sqlite3', inbox=inbox, runner=None))
    from console import v3_overview_api
    routes += v3_overview_api.build(gate, paths={'inbound': tmp + '/overview-inbound.sqlite3'})
    for name in ('v3_quotes_api', 'v3_roles_api', 'v3_whatsapp_login_api', 'v3_directory_api', 'v3_audit_api', 'v3_extract_api', 'v3_fulfil_api', 'v3_icbu_api', 'v3_listing_api', 'v3_lang_api', 'v3_pipeline_api'):
        # Modules added by later batches join the rehearsal automatically.
        try:
            module = __import__('console.' + name, fromlist=['build'])
        except ImportError:
            continue
        if name == 'v3_directory_api':
            # Fake authentication must not trigger remote contact requests.
            routes += module.build(gate, cache_path=tmp + '/directory.sqlite3', allow_refresh=False)
            continue
        if name == 'v3_audit_api':
            # Only role output is written, to the disposable archive copy.
            # Explicit local sources retain production reads without WAHA I/O.
            from console.audit.service import WHATSAPP_PATH
            from console.channels.probe import PROBE_PATH
            from console.directory.sources import CONFIG_PATH as WX_CONFIG, DISCOVERY_PATH, STATE_PATH
            from console.inbox.store import INBOUND_PATH
            routes += module.build(gate, archive, inbound_path=INBOUND_PATH, probe_path=PROBE_PATH,
                wechat_path=STATE_PATH, discovery_path=DISCOVERY_PATH, wechat_config_path=WX_CONFIG,
                whatsapp_path=WHATSAPP_PATH, whatsapp_config_path=CONFIG_PATH)
            continue
        if name == 'v3_icbu_api':
            # Rehearsal uses only the legacy read-only projections and may never
            # change settings or enqueue commands against the live ICBU lane.
            routes += module.build(gate, allow_writes=False, owner_resolver=lambda: 'rehearsal')
            continue
        if name == 'v3_listing_api':
            # Rehearsal drafts stay in the copied archive and use rules only.
            routes += module.build(gate, archive, runner=None)
            continue
        if name == 'v3_roles_api':
            routes += module.build(gate, archive, inbox=inbox, runner=None)
            continue
        if name == 'v3_pipeline_api':
            # Health is read-only over the real stores; it takes no archive.
            routes += module.build(gate)
            continue
        if name == 'v3_lang_api':
            # Language settings and translation cache stay in the disposable directory.
            routes += module.build(gate, tmp + '/lang.sqlite3')
            continue
        # Fake authentication must never authorize changes to the real WAHA session.
        options = ({'allow_writes': False, 'whatsapp_config_path': CONFIG_PATH}
                   if name == 'v3_whatsapp_login_api' else {})
        routes += module.build(gate, archive, **options)
    from starlette.routing import Route
    from starlette.responses import JSONResponse
    async def rehearsal_marker(request):
        return JSONResponse({'rehearsal': True, 'dry_run_writes': True})
    routes += [Route('/__rehearsal__', rehearsal_marker, methods=['GET'])]
    return routes + v3_app.build(gate, Path(__file__).resolve().parents[1] / 'web/dist')


def main():
    private = Path(__file__).resolve().parents[1] / 'private_out/rehearsal'
    private.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = tempfile.mkdtemp(prefix='imgw-rehearsal-', dir=private)
    app = Starlette(routes=build_routes(tmp, copy_sources=True))

    async def asgi(scope, receive, send):
        if scope['type'] == 'http':
            if scope.get('method') not in ('GET', 'HEAD'):
                import re
                path = scope.get('path', '')
                allowed = re.fullmatch(r'/api/v1/(classify/scan|parties-ops(?:/\d+/(?:update|link|merge|undo|draft|review|verify|deal|term|term-status))?|inbox/conversations/[^/]+/ops/(?:action|supplier-draft)|deals(?:/\d+/(?:update|participants|conversations|items|tasks|events|quotes|payments|drafts|fulfilment/[^/]+/update))?|deal-tasks/\d+/status|payments/\d+/confirm|quotes/\d+/(?:update|terms|lines/\d+/update|freight-options/choose|negotiations|drafts))', path)
                if not allowed:
                    return await PlainTextResponse('rehearsal write disabled', status_code=403)(scope, receive, send)
            scope.setdefault('state', {})
            scope['state'].update(password_principal=SimpleNamespace(username='rehearsal'), password_recheck=lambda: True,
                                  session=SimpleNamespace(authenticated=True), csrf='rehearsal')

        async def secured(message):
            if message['type'] == 'http.response.start':
                message['headers'] = [h for h in message['headers'] if h[0].lower() != b'content-security-policy'] \
                    + [(b'content-security-policy', CSP.encode())]
            await send(message)
        await app(scope, receive, secured)

    import uvicorn
    print('REHEARSAL_READY', PORT, tmp, flush=True)
    try:
        uvicorn.run(asgi, host='127.0.0.1', port=PORT, log_level='warning')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    main()
