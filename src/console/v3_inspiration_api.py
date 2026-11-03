"""Inspiration JSON routes behind both console authentication gates.

GET lists and details; POST .../update changes only a card's status and note. The
console middleware has already checked the CSRF token and read the form body by the
time the POST handler runs.
"""
from pathlib import Path
import re
from urllib.parse import parse_qs

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from starlette.routing import Route

from .inspiration.store import InspirationStore, validate_filters

STORE_PATH = Path('/var/lib/imgw/ops-app/inspiration.sqlite3')
HEADERS = {'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'}
_FIELDS = ('card_id', 'author', 'text', 'tags', 'urls', 'summary', 'items',
           'usefulness', 'how_to_use', 'confidence', 'relevance', 'status', 'note',
           'first_seen', 'last_seen', 'times_seen', 'enriched')


def _public(card):
    return {key: card[key] for key in _FIELDS}


def _parameters(params):
    try:
        raw_page, raw_size = params.get('page', '1'), params.get('page_size', '20')
        if not re.fullmatch(r'[0-9]{1,7}', raw_page) or not re.fullmatch(r'[0-9]{1,3}', raw_size):
            raise ValueError
        page, page_size = int(raw_page), int(raw_size)
    except (TypeError, ValueError):
        raise ValueError('页码和每页数量须为有效整数') from None
    values = dict(q=params.get('q', ''), relevance=params.get('relevance'),
                  status=params.get('status'), page=page, page_size=page_size)
    validate_filters(**values)
    return values


def build(app_module, store_path=STORE_PATH):
    path = Path(store_path)

    def read_list(values):
        if not path.exists():
            return dict(items=[], total=0, page=values['page'], page_size=values['page_size'],
                        counts={'total': 0, 'relevance': {k: 0 for k in ('ai_dev', 'business', 'industry', 'other', 'unknown')},
                                'status': {k: 0 for k in ('new', 'evaluating', 'trying', 'adopted', 'ignored')}},
                        ready=False), 200
        store = InspirationStore(path, read_only=True)
        rows, total = store.list_cards(**values)
        return dict(items=[_public(row) for row in rows], total=total,
                    page=values['page'], page_size=values['page_size'], counts=store.counts(), ready=True), 200

    def read_detail(card_id):
        if not path.exists():
            return {'item': None, 'ready': False}, 200
        card = InspirationStore(path, read_only=True).get_card(card_id)
        if card is None:
            return {'error': '未找到灵感卡片'}, 404
        return {'item': _public(card), 'ready': True}, 200

    def handler(detail=False):
        async def serve(request):
            principal = getattr(request.state, 'password_principal', None)
            recheck = getattr(request.state, 'password_recheck', None)
            legacy = getattr(request.state, 'session', None)
            if (not principal or not getattr(principal, 'username', None)
                    or not callable(recheck) or not legacy or not legacy.authenticated):
                return app_module.error_response(request, 403)
            try:
                if detail:
                    card_id = request.path_params.get('card_id', '')
                    if not re.fullmatch(r'[0-9a-f]{16}', card_id):
                        raise ValueError('卡片标识无效')
                else:
                    values = _parameters(request.query_params)
            except ValueError:
                payload, code = {'error': '请求参数无效，请检查分类、状态、页码及搜索词'}, 400
            else:
                try:
                    if detail:
                        payload, code = await run_in_threadpool(read_detail, card_id)
                    else:
                        payload, code = await run_in_threadpool(read_list, values)
                except Exception:
                    payload, code = {'error': '灵感库暂时无法读取'}, 503
            try:
                authorized = await run_in_threadpool(recheck)
            except Exception:
                authorized = False
            if not authorized:
                return app_module.error_response(request, 403)
            return JSONResponse(payload, status_code=code, headers=HEADERS)
        return serve

    def write_card(card_id, fields):
        store = InspirationStore(path)
        card = store.get_card(card_id)
        if card is None:
            return {'error': '未找到灵感卡片'}, 404
        status = fields.get('status', card['status'])
        note = fields.get('note', card['note'] or '')
        store.set_status(card_id, status, note)
        return {'item': _public(store.get_card(card_id)), 'ready': True}, 200

    async def update(request):
        principal = getattr(request.state, 'password_principal', None)
        recheck = getattr(request.state, 'password_recheck', None)
        legacy = getattr(request.state, 'session', None)
        if (not principal or not getattr(principal, 'username', None)
                or not callable(recheck) or not legacy or not legacy.authenticated):
            return app_module.error_response(request, 403)
        try:
            authorized = await run_in_threadpool(recheck)
        except Exception:
            authorized = False
        if not authorized:  # re-verify before any write, not after
            return app_module.error_response(request, 403)
        card_id = request.path_params.get('card_id', '')
        form = getattr(request.state, 'form', None)
        if form is None:
            form = parse_qs((await request.body()).decode('utf8', 'replace'), max_num_fields=16)
        fields = {key: values[0] for key, values in form.items() if key in ('status', 'note') and len(values) == 1}
        if not re.fullmatch(r'[0-9a-f]{16}', card_id) or not fields or not path.exists():
            payload, code = {'error': '请求参数无效'}, 400
        else:
            try:
                payload, code = await run_in_threadpool(write_card, card_id, fields)
            except ValueError as error:
                payload, code = {'error': str(error)}, 400
            except Exception:
                payload, code = {'error': '灵感库暂时无法保存'}, 503
        return JSONResponse(payload, status_code=code, headers=HEADERS)

    return [Route('/api/v1/inspiration', handler(), methods=['GET'], name='inspiration_list'),
            Route('/api/v1/inspiration/{card_id}', handler(True), methods=['GET'], name='inspiration_detail'),
            Route('/api/v1/inspiration/{card_id}/update', update, methods=['POST'], name='inspiration_update')]
