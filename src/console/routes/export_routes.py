'''Authenticated exports and asset flags.'''
from dataclasses import replace
from pathlib import Path
import json
import re
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect
from starlette.responses import Response, JSONResponse
from starlette.routing import Route
from console.cpq import export, leak_guard
from console.cpq.company import load_company_profile
from console.cpq.service import CPQError
from console.cpq.store import dump, stamp
from console.cpq.workdb import open_work
from console.security.media import MediaError, MediaService
from .cpq_routes import CPQAPI, error, pairs, bad_constant, finite_float

FORMATS = {'customer.pdf': ('application/pdf', '.pdf'), 'customer.html': ('text/html', '.html'), 'whatsapp.txt': ('text/plain', '.txt'), 'images.zip': ('application/zip', '-images.zip')}
CATEGORIES = {'supplier_name', 'factory_model', 'cny_marker', 'cost_value', 'internal_key', 'pdf_meta', 'inspection_error'}


class ExportAPI(CPQAPI):
    # Reuses freeze authorization and actor parsing, including console-owner.
    def __init__(self, settings):
        super().__init__(settings)
        self.media = MediaService(settings.media_root, settings.media_cache)

    def blocked(self, token, fmt, actor, hits):
        categories = sorted({h.category for h in hits})
        self.service.store.append_audit('LEAK_BLOCKED', actor=actor, quote_token=token, detail={'export_type': fmt, 'categories': categories, 'offsets': [{'category': h.category, 'offset': h.offset} for h in hits]})
        raise CPQError(422, 'LEAK_BLOCKED', {'categories': categories})

    def inspect(self, token, fmt, actor, scanner, *args):
        try:
            hits = list(scanner(*args))
            if any(h.category not in CATEGORIES or type(h.offset) is not int or h.offset < 0 for h in hits):
                raise ValueError('Invalid inspection result')
        except Exception:
            hits = [leak_guard.Hit('inspection_error', 0)]
        if hits:
            self.blocked(token, fmt, actor, hits)

    def resolver(self, logo=False):
        path = None
        if logo:
            profile_path = Path(self.quotes.company_profile_path)
            configured = load_company_profile(profile_path, config=self.service.config).values['logo_path']
            if configured:
                path = Path(configured).expanduser()
                if not path.is_absolute():
                    path = profile_path.parent / path
        return export.Resolver(self.service.repository, self.service.store, self.media, path)

    def document(self, token, fmt, actor):
        if not re.fullmatch(r'q_[0-9a-f]{32}', token):
            raise CPQError(404, 'QUOTE_NOT_FOUND')
        row = self.quotes.get_public_row(token)
        if row['status'] != 'final':
            raise CPQError(409, 'QUOTE_NOT_FINAL')
        try:
            dto, store, mids = export.load_customer_dto(row['customer_dto_json']), self.service.store, set()
            for sku in {line.sku for line in dto.lines}:
                rows = store.read('SELECT model_id FROM cpq_public_sku WHERE public_sku=?', (sku,))
                if len(rows) != 1:
                    raise ValueError('Inspection model unavailable')
                mids.add(rows[0]['model_id'])
            ctx = leak_guard.load_leak_context(store.db_path, store.work_db, tuple(sorted(mids)))
            costs = set(ctx.cost_values)
            for mid in mids:
                for packing in store.packing(mid).values():
                    if packing and packing.get('case_cost_cny') is not None:
                        costs.add(packing['case_cost_cny'])
            ctx = replace(ctx, cost_values=tuple(sorted(costs)))
        except Exception:
            self.blocked(token, fmt, actor, [leak_guard.Hit('inspection_error', 0)])
        mime, suffix = FORMATS[fmt]
        filename = dto.quote_no + suffix
        self.inspect(token, fmt, actor, leak_guard.scan, filename, ctx)
        if fmt == 'whatsapp.txt':
            body = export.render_whatsapp(dto)
            self.inspect(token, fmt, actor, leak_guard.scan, body, ctx)
            if re.search(r'<[^>]+>', body) or any(len(line) > 4000 for line in body.splitlines()):
                raise CPQError(422, 'EXPORT_FORMAT_INVALID')
        else:
            images = export.prepare_images(dto, self.resolver(fmt != 'images.zip'))
            if fmt == 'images.zip':
                body = export.build_images_zip(dto, images)
                self.inspect(token, fmt, actor, leak_guard.scan_zip_bytes, body, ctx)
            else:
                html = export.render_html(dto, images)
                # Pre-layout inspection also covers characters missing from PDF fonts.
                self.inspect(token, fmt, actor, leak_guard.scan, export.html_text(html), ctx)
                export.validate_html(html)
                body = export.render_pdf(html, export.PDF_BASE_URL, dto) if fmt == 'customer.pdf' else html
                if fmt == 'customer.pdf':
                    self.inspect(token, fmt, actor, leak_guard.scan_pdf_bytes, body, ctx)
        headers = {'Content-Disposition': f'attachment; filename="{filename}"', 'Cache-Control': 'private, no-store', 'Vary': 'Cookie, Authorization', 'X-Content-Type-Options': 'nosniff'}
        if fmt == 'customer.html':
            headers['Content-Security-Policy'] = "default-src 'none'; img-src data:; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
        return Response(body, media_type=mime, headers=headers)

    async def download(self, request):
        try:
            self.authorize(request, 'cpq.quote')
            return await run_in_threadpool(self.document, request.path_params['token'], request.url.path.rsplit('/', 1)[-1], self.actor(request))
        except CPQError as exc:
            return error(exc.status_code, exc.code, exc.details)
        except MediaError as exc:
            return error(exc.status_code, exc.code)
        except Exception:
            return error(503, 'EXPORT_UNAVAILABLE')

    async def flag_json(self, request):
        length = request.headers.get('content-length')
        if length is not None:
            if not length.isascii() or not length.isdecimal():
                raise CPQError(400, 'BAD_REQUEST')
            length = length.lstrip('0') or '0'
            if len(length) > 4 or int(length) > 8192:
                raise CPQError(413, 'REQUEST_TOO_LARGE')
        if request.headers.get('content-type', '').split(';', 1)[0].strip().lower() != 'application/json':
            raise CPQError(415, 'UNSUPPORTED_MEDIA_TYPE')
        raw = bytearray()
        async for chunk in request.stream():
            if len(raw) + len(chunk) > 8192:
                raise CPQError(413, 'REQUEST_TOO_LARGE')
            raw.extend(chunk)
        try:
            body = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_constant=bad_constant, parse_float=finite_float)
        except (ValueError, UnicodeError, RecursionError):
            raise CPQError(400, 'INVALID_JSON') from None
        if not isinstance(body, dict) or set(body) != {'customer_safe', 'reason'} or type(body['customer_safe']) is not bool or not isinstance(body['reason'], str) or len(body['reason']) > 200:
            raise CPQError(400, 'BAD_REQUEST')
        return body

    def set_flag(self, uid, body, actor):
        asset = self.service.repository.asset_by_uid(uid)
        if asset is None:
            raise CPQError(404, 'NOT_FOUND')
        store = self.service.store
        store.initialize()
        connection = open_work(store.work_db)
        try:
            with connection:
                connection.execute('BEGIN IMMEDIATE')
                connection.execute('INSERT INTO cpq_asset_flag(asset_id,customer_safe,reason,updated_by,updated_at) VALUES (?,?,?,?,?) ON CONFLICT(asset_id) DO UPDATE SET customer_safe=excluded.customer_safe,reason=excluded.reason,updated_by=excluded.updated_by,updated_at=excluded.updated_at', (asset.asset_id, int(body['customer_safe']), body['reason'], actor, stamp()))
                connection.execute('INSERT INTO cpq_audit_log(at,actor,action,quote_token,detail_json) VALUES (?,?,?,?,?)', (stamp(), actor, 'ASSET_FLAG', None, dump({'asset_uid': uid, 'customer_safe': body['customer_safe']})))
        finally:
            connection.close()
        return {'asset_uid': uid, 'customer_safe': body['customer_safe']}

    async def flag(self, request):
        try:
            self.authorize(request, 'cpq.admin')
            actor = self.actor(request)
            body = await self.flag_json(request)
            return JSONResponse(await run_in_threadpool(self.set_flag, request.path_params['asset_uid'], body, actor), headers={'Cache-Control': 'private, no-store'})
        except CPQError as exc:
            return error(exc.status_code, exc.code, exc.details)
        except ClientDisconnect:
            return error(400, 'BAD_REQUEST')
        except Exception:
            return error(503, 'ASSET_FLAG_UNAVAILABLE')


def build_export_routes(settings):
    api = ExportAPI(settings)
    return [Route('/quotes/{token}/' + fmt, api.download, methods=['GET'], name='cpq_export_' + fmt.replace('.', '_')) for fmt in FORMATS] + [Route('/api/cpq/assets/{asset_uid}/flag', api.flag, methods=['POST'], name='cpq_asset_flag')]
