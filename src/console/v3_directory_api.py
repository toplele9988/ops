"""Read-only WeChat directory routes; host middleware retains its CSRF check."""
from starlette.concurrency import run_in_threadpool
from starlette.routing import Route

from .archive.validation import BadRequest, choice, integer, text
from .directory import RefreshFailed, RefreshLimited, WechatDirectory
from .directory.wtapi_source import build_readonly_refresh_client
from .inbox.http import authorised, forbidden, recheck, response
from .v3_archive_api import form_fields


def parameters(params):
    allowed = {'kind', 'state', 'q', 'page', 'page_size'}
    if set(params) - allowed:
        raise BadRequest('包含不支持的查询参数')
    for key, value in params.items():
        if hasattr(params, 'getlist') and len(params.getlist(key)) != 1:
            raise BadRequest('同一参数只能传入一次')
        if not isinstance(value, str):
            raise BadRequest('请求参数须为文本')
    return {
        'kind': choice(params.get('kind', 'group'), ('', 'all', 'group', 'private', 'official')),
        'state': choice(params.get('state', ''), ('', 'collecting', 'enrolled_silent',
                                                'enrolled_no_messages', 'not_enrolled')),
        'q': text(params.get('q', ''), 200) or '',
        'page': integer(params.get('page', '1')),
        'page_size': integer(params.get('page_size', '50'), maximum=100),
    }


def build(app_module, *, service=None, discovery_path=None, config_path=None, state_path=None,
          cache_path=None, clock=None, contact_fetcher=None, readonly_client=None, allow_refresh=True):
    if service is None:
        options = dict(discovery_path=discovery_path, config_path=config_path, state_path=state_path,
                       cache_path=cache_path, clock=clock, contact_fetcher=contact_fetcher,
                       readonly_client=readonly_client)
        if allow_refresh and contact_fetcher is None and readonly_client is None:
            # Resolve the isolated service path only on an authorised refresh.
            # Mounting and GETs never construct a credential-bearing client.
            options['readonly_client_factory'] = lambda: build_readonly_refresh_client(
                config_path=service.config_path)
        service = WechatDirectory(**{key: value for key, value in options.items() if value is not None})

    def handler(action):
        refreshing = action == 'refresh'

        async def serve(request):
            if not authorised(request):
                return forbidden(app_module, request)
            if refreshing and not await recheck(request):
                return forbidden(app_module, request)
            if refreshing and not allow_refresh:
                return response({'error': '演练环境禁止调用真实通讯录刷新', 'code': 'refresh_disabled'}, 403)
            retry_after = None
            try:
                if action == 'list':
                    payload = await run_in_threadpool(service.list, parameters(request.query_params))
                else:
                    if request.query_params:
                        raise BadRequest('此接口不接受查询参数')
                    if refreshing:
                        media_type = request.headers.get('content-type', '').split(';', 1)[0].strip().lower()
                        if media_type and media_type != 'application/x-www-form-urlencoded':
                            raise BadRequest('请使用 URL 编码表单提交')
                        fields = await form_fields(request)
                        if set(fields) != {'confirm'} or fields['confirm'] != '1':
                            raise BadRequest('刷新通讯录须确认 confirm=1，且不能附加其他字段')
                        payload = await run_in_threadpool(service.refresh)
                    else:
                        payload = await run_in_threadpool(service.summary)
                code = 200
            except RefreshLimited as error:
                retry_after = error.retry_after
                payload, code = {'error': str(error), 'retry_after': retry_after}, 429
            except RefreshFailed as error:
                payload, code = {'error': str(error)}, error.status_code
            except BadRequest as error:
                payload, code = {'error': str(error)}, 400
            except Exception:
                payload, code = {'error': '通讯录刷新暂时无法完成' if refreshing else '通讯录暂时无法读取'}, 503
            if not refreshing and not await recheck(request):
                return forbidden(app_module, request)
            result = response(payload, code)
            if retry_after is not None:
                result.headers['Retry-After'] = str(retry_after)
            return result
        return serve

    return [Route('/api/v1/directory/wechat' + suffix, handler(action),
                  methods=['POST' if action == 'refresh' else 'GET'], name='directory_wechat_' + action)
            for suffix, action in (('', 'list'), ('/summary', 'summary'), ('/refresh', 'refresh'))]
