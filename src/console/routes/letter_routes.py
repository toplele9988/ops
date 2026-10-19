"""Authenticated generation and retrieval of customer letter drafts."""
from dataclasses import replace
import re

from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect
from starlette.responses import JSONResponse
from starlette.routing import Route

from console.cpq import export, leak_guard, letter
from console.cpq.service import CPQError
from console.cpq.store import stamp
from console.cpq.workdb import open_work
from .cpq_routes import CPQAPI, error


MAX_BODY_BYTES = 8192
PRIVATE_HEADERS = {
    'Cache-Control': 'private, no-store',
    'Vary': 'Cookie, Authorization',
    'X-Content-Type-Options': 'nosniff',
}


class LetterAPI(CPQAPI):
    @staticmethod
    def validate_token(token):
        if not isinstance(token, str) or not re.fullmatch(r'q_[0-9a-f]{32}', token):
            raise CPQError(404, 'QUOTE_NOT_FOUND')

    def public_row(self, token):
        self.validate_token(token)
        return self.quotes.get_public_row(token)

    async def letter_json(self, request):
        declared = request.headers.get('content-length')
        if declared is not None:
            if not declared.isascii() or not declared.isdecimal():
                raise CPQError(400, 'BAD_REQUEST')
            declared = declared.lstrip('0') or '0'
            if len(declared) > 4 or int(declared) > MAX_BODY_BYTES:
                raise CPQError(413, 'REQUEST_TOO_LARGE')
        if request.headers.get('content-type', '').split(';', 1)[0].strip().lower() != 'application/json':
            raise CPQError(415, 'UNSUPPORTED_MEDIA_TYPE')
        raw = bytearray()
        async for chunk in request.stream():
            if len(raw) + len(chunk) > MAX_BODY_BYTES:
                raise CPQError(413, 'REQUEST_TOO_LARGE')
            raw.extend(chunk)
        try:
            body = letter.strict_json_loads(raw.decode('utf-8'))
        except (ValueError, UnicodeError, RecursionError):
            raise CPQError(400, 'INVALID_JSON') from None
        if (not isinstance(body, dict) or not {'tone', 'purpose'} <= set(body)
                or set(body) - {'tone', 'purpose', 'extra_notes'}):
            raise CPQError(400, 'BAD_REQUEST')
        notes = body.get('extra_notes', '')
        try:
            letter.validate_options(body['tone'], body['purpose'], notes)
        except ValueError:
            raise CPQError(400, 'BAD_REQUEST') from None
        return {'tone': body['tone'], 'purpose': body['purpose'], 'extra_notes': notes}

    def inspection(self, row):
        try:
            dto = export.load_customer_dto(row['customer_dto_json'])
            store, mids = self.service.store, set()
            for sku in {line.sku for line in dto.lines}:
                matches = store.read('SELECT model_id FROM cpq_public_sku WHERE public_sku=?', (sku,))
                if len(matches) != 1:
                    raise ValueError('Inspection model unavailable')
                mids.add(matches[0]['model_id'])
            context = leak_guard.load_leak_context(store.db_path, store.work_db, tuple(sorted(mids)))
            costs = set(context.cost_values)
            for mid in mids:
                for packing in store.packing(mid).values():
                    if packing and packing.get('case_cost_cny') is not None:
                        costs.add(packing['case_cost_cny'])
            return dto, replace(context, cost_values=tuple(sorted(costs)))
        except Exception:
            raise letter.LetterError(422, 'LEAK_BLOCKED', ['inspection_error']) from None

    def audit_failure(self, token, actor, exc):
        action = 'LETTER_BLOCKED' if exc.code in {'LEAK_BLOCKED', 'LETTER_NUMBER_MISMATCH'} else 'LETTER_FAILED'
        detail = {'code': exc.code}
        if exc.details:
            detail.update(exc.details)
        self.service.store.append_audit(action, actor=actor, quote_token=token, detail=detail)

    def store_draft(self, token, actor, result):
        store = self.service.store
        store.initialize()
        connection = open_work(store.work_db)
        try:
            with connection:
                # Version allocation and insertion share a write transaction.
                connection.execute('BEGIN IMMEDIATE')
                row = connection.execute('SELECT status FROM cpq_quote WHERE quote_token=?', (token,)).fetchone()
                if row is None:
                    raise CPQError(404, 'QUOTE_NOT_FOUND')
                if row[0] != 'final':
                    raise CPQError(409, 'QUOTE_NOT_FINAL')
                version = connection.execute(
                    'SELECT COALESCE(MAX(version),0)+1 FROM cpq_quote_letter WHERE quote_token=?',
                    (token,),
                ).fetchone()[0]
                connection.execute(
                    '''INSERT INTO cpq_quote_letter
                       (quote_token,version,subject,body,status,created_by,created_at)
                       VALUES (?,?,?,?,?,?,?)''',
                    (token, version, result['subject'], result['body'], 'draft', actor, stamp()),
                )
        finally:
            connection.close()
        return {'quote_token': token, 'version': version, **result, 'status': 'draft'}

    def create_draft(self, token, body, actor):
        row = self.public_row(token)
        if row['status'] != 'final':
            raise CPQError(409, 'QUOTE_NOT_FINAL')
        try:
            dto, context = self.inspection(row)
            with letter.inspection_context(context):
                result = letter.generate_letter(dto, body['tone'], body['purpose'], body['extra_notes'])
            return self.store_draft(token, actor, result)
        except letter.LetterError as exc:
            self.audit_failure(token, actor, exc)
            raise
        except CPQError:
            raise
        except Exception:
            failure = letter.LetterError(503, 'LETTER_FAILED')
            self.audit_failure(token, actor, failure)
            raise failure from None

    def history(self, token, actor):
        row = self.public_row(token)
        try:
            drafts = self.service.store.read(
                '''SELECT quote_token,version,subject,body,status,created_by,created_at
                   FROM cpq_quote_letter WHERE quote_token=? AND status=? ORDER BY version''',
                (token, 'draft'),
            )
            if drafts:
                if row['status'] != 'final':
                    raise CPQError(409, 'QUOTE_NOT_FINAL')
                dto, context = self.inspection(row)
                with letter.inspection_context(context):
                    for draft in drafts:
                        draft.update(letter.validate_letter(
                            dto, {'subject': draft['subject'], 'body': draft['body']},
                        ))
            return {'quote_token': token, 'letters': drafts}
        except letter.LetterError as exc:
            self.audit_failure(token, actor, exc)
            raise
        except CPQError:
            raise
        except Exception:
            failure = letter.LetterError(503, 'LETTER_FAILED')
            self.audit_failure(token, actor, failure)
            raise failure from None

    async def create(self, request):
        try:
            self.authorize(request, 'cpq.quote')
            actor, token = self.actor(request), request.path_params['token']
            self.validate_token(token)
            body = await self.letter_json(request)
            result = await run_in_threadpool(self.create_draft, token, body, actor)
            return JSONResponse(result, status_code=201, headers=PRIVATE_HEADERS)
        except (letter.LetterError, CPQError) as exc:
            return error(exc.status_code, exc.code, exc.details)
        except ClientDisconnect:
            return error(400, 'BAD_REQUEST')
        except Exception:
            return error(503, 'LETTER_UNAVAILABLE')

    async def list_drafts(self, request):
        try:
            self.authorize(request, 'cpq.quote')
            result = await run_in_threadpool(self.history, request.path_params['token'], self.actor(request))
            return JSONResponse(result, headers=PRIVATE_HEADERS)
        except (letter.LetterError, CPQError) as exc:
            return error(exc.status_code, exc.code, exc.details)
        except Exception:
            return error(503, 'LETTER_UNAVAILABLE')


def build_letter_routes(settings):
    api = LetterAPI(settings)
    return [
        Route('/api/quotes/{token}/letter', api.create, methods=['POST'], name='cpq_letter'),
        Route('/api/quotes/{token}/letters', api.list_drafts, methods=['GET'], name='cpq_letters'),
    ]
