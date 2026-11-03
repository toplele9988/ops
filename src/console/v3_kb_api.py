"""Authenticated Knowledge Hub routes; mount behind existing CSRF middleware."""
from collections import deque
import math
import re
import threading
import time

from starlette.concurrency import run_in_threadpool
from starlette.routing import Route

from .inbox.http import authorised, forbidden, form_fields, get_handler, recheck, response
from .inbox.model import BadRequest, single
from .kb.hub import ASK_ERROR, default_hub
from .kb.policy import FAMILIES


class AskLimiter:
    def __init__(self):
        self.calls = deque()
        self.lock = threading.Lock()

    def reserve(self, now):
        with self.lock:
            while self.calls and self.calls[0] <= now - 3600:
                self.calls.popleft()
            minute = [t for t in self.calls if t > now - 60]
            if len(self.calls) >= 60:
                return max(1, math.ceil(self.calls[0] + 3600 - now))
            if len(minute) >= 6:
                return max(1, math.ceil(minute[0] + 60 - now))
            self.calls.append(now)
            return 0


LIMITER = AskLimiter()


def _parameters(params, search=True):
    q = single(params, 'q')
    purpose = single(params, 'purpose') or 'owner'
    if not q.strip() or len(q) > 500:
        raise BadRequest('问题须为 1 至 500 字')
    if purpose not in ('owner', 'product_facts'):
        raise BadRequest('用途须为 owner 或 product_facts')
    result = dict(query=q, purpose=purpose)
    if search:
        family = single(params, 'family')
        top = single(params, 'top_k') or '8'
        if family and family not in FAMILIES:
            raise BadRequest('资料族无效')
        if not re.fullmatch(r'[0-9]{1,2}', top) or not 1 <= int(top) <= 20:
            raise BadRequest('条数须为 1 至 20')
        result.update(top_k=int(top), families=[family] if family else None)
    return result


def build(app_module, *, hub=None, runner=None, limiter=None, clock=time.monotonic, warm=None):
    service = hub if hub is not None else default_hub()
    # The first load of the 40k-chunk index takes ~8 s; do it in the background at startup so nobody waits for it.
    # Only for the real hub (tests inject their own and never start threads).
    if warm is None:
        warm = hub is None
    if warm:
        def _warm():
            try:
                service.index.snapshot()
            except Exception:
                pass  # first real search retries and reports its own error
        threading.Thread(target=_warm, name='kb-warm', daemon=True).start()
    admission = limiter if limiter is not None else LIMITER

    def brief_parameter(request):
        version = single(request.query_params, 'version')
        if version and (not re.fullmatch(r'[0-9]{1,18}', version) or int(version) < 1):
            raise BadRequest('版本号无效')
        return int(version) if version else None

    def read_brief(version):
        if version is not None:
            item = service.brief.version(version)
            return (item, 200) if item else ({'error': '未找到公司背景版本'}, 404)
        return dict(text=service.brief.read(), versions=service.brief.versions(5)), 200

    async def save_brief(request):
        if not authorised(request) or not await recheck(request):
            return forbidden(app_module, request)
        try:
            fields = await form_fields(request)
            text = fields.get('text', '')
            if not text.strip() or len(text) > 8000:
                raise BadRequest('公司背景须为 1 至 8000 字')
            try:
                version = await run_in_threadpool(service.brief.save_brief, text,
                    request.state.password_principal.username)
            except ValueError:
                raise BadRequest('公司背景内容无效') from None
            return response(dict(version=version))
        except BadRequest as error:
            return response({'error': str(error)}, 400)
        except Exception:
            return response({'error': '公司背景暂时无法保存'}, 503)

    async def ask(request):
        if not authorised(request) or not await recheck(request):
            return forbidden(app_module, request)
        try:
            fields = await form_fields(request)
            values = _parameters(fields, search=False)
            retry = admission.reserve(clock())
            if retry:
                result = response({'error': '提问过于频繁，请稍后再试', 'retry_after': retry}, 429)
                result.headers['Retry-After'] = str(retry)
                return result
            ready = await run_in_threadpool(lambda: service.index.snapshot() is not None)
            if not ready:
                return response(dict(ready=False, answer='知识库里没有找到相关资料', sources=[], mode='lexical'))
            result = await run_in_threadpool(service.ask, values['query'], values['purpose'], runner)
            if result['answer'] == ASK_ERROR:
                return response({'error': ASK_ERROR}, 502)
            return response(dict(result, ready=True))
        except BadRequest as error:
            return response({'error': str(error)}, 400)
        except Exception:
            return response({'error': '知识库暂时无法读取'}, 503)

    def get(parse, read):
        return get_handler(app_module, parse, read, '知识库暂时无法读取')

    return [
        Route('/api/v1/kb/search', get(lambda r: _parameters(r.query_params),
            lambda values: (service.search(**values), 200)), methods=['GET'], name='kb_search'),
        Route('/api/v1/kb/ask', ask, methods=['POST'], name='kb_ask'),
        Route('/api/v1/kb/brief', get(brief_parameter, read_brief), methods=['GET'], name='kb_brief'),
        Route('/api/v1/kb/brief', save_brief, methods=['POST'], name='kb_save_brief'),
        Route('/api/v1/kb/stats', get(lambda r: None, lambda _: (service.stats(), 200)),
              methods=['GET'], name='kb_stats'),
    ]
