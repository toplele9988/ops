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

from starlette.applications import Starlette
from starlette.responses import PlainTextResponse

from console import (v3_app, v3_archive_api, v3_channels_api, v3_customers_api, v3_deals_api, v3_inbox_api,
                     v3_inspiration_api, v3_kb_api, v3_media_api, v3_outbox_api, v3_packing_api)
from console.channels.whatsapp import CONFIG_PATH

PORT = int(os.environ.get('REHEARSAL_PORT', '18902'))
REAL_ARCHIVE = Path(os.environ.get('REAL_ARCHIVE', '/var/lib/imgw/ops-app/archive.sqlite3'))
CSP = "default-src 'self'; img-src 'self' data:; script-src 'self'; style-src 'self'; frame-ancestors 'none'"


class Gate:
    def error_response(self, request, code, incident=None):
        return PlainTextResponse('err', status_code=code)


def archive_copy(tmp):
    target = Path(tmp) / 'archive.sqlite3'
    if REAL_ARCHIVE.exists():
        with closing(sqlite3.connect(REAL_ARCHIVE.resolve().as_uri() + '?mode=ro', uri=True)) as src, closing(sqlite3.connect(target)) as dst:
            src.backup(dst)
    return str(target)


def build_routes(tmp):
    gate = Gate()
    archive = archive_copy(tmp)
    routes = (v3_inspiration_api.build(gate) + v3_packing_api.build(gate, state_path=tmp + '/actions.sqlite3')
              + v3_inbox_api.build(gate, drafts_path=tmp + '/drafts.sqlite3') + v3_customers_api.build(gate)
              + v3_channels_api.build(gate) + v3_outbox_api.build(gate, outbox_path=tmp + '/outbox.sqlite3')
              + v3_media_api.build(gate) + v3_kb_api.build(gate) + v3_archive_api.build(gate, archive)
              + v3_deals_api.build(gate, archive))
    from console import v3_deal_workflow_api
    routes += v3_deal_workflow_api.build(gate, archive, runner=None)
    from console import v3_alibaba_open_api
    routes += v3_alibaba_open_api.build(gate, archive, credentials_path=tmp + '/alibaba-open-unconfigured.json')
    from console import v3_classify_api
    routes += v3_classify_api.build(gate, path=tmp + '/classify.sqlite3', archive_path=archive)
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
    return routes + v3_app.build(gate)


def main():
    tmp = tempfile.mkdtemp(prefix='imgw-rehearsal-')
    app = Starlette(routes=build_routes(tmp))

    async def asgi(scope, receive, send):
        if scope['type'] == 'http':
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
