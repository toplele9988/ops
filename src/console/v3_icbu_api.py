"""Authenticated JSON API for legacy Alibaba draft review and execution."""
import os
import re

from starlette.concurrency import run_in_threadpool
from starlette.routing import Route

from .icbu import ICBUAdapter
from .icbu.adapter import BadRequest, Conflict, Disabled, Missing
from .archive.validation import BadRequest as ArchiveBadRequest
from .inbox.http import authorised, forbidden, recheck, response
from .inbox.model import BadRequest as FormBadRequest
from .v3_archive_api import form_fields


PREFIX = '/api/v1/icbu/'
ALLOWED_MODES = ('review', 'test_auto', 'auto')
CONVERSATION = re.compile(r'tm:[A-Za-z0-9_-]{1,128}\Z')


def _owner(app_module):
    # Preserve the legacy inquiry owner projection. Never return the config.
    return app_module.inquiry_owner(app_module.read_json_mapping(app_module.data.CONFIG))


def _query_one(request, allowed, key=None, default=None):
    query = request.query_params
    keys = set(query.keys()) if hasattr(query, 'keys') else set(query)
    if keys - set(allowed):
        raise BadRequest('查询参数无效')
    if key is None:
        if keys:
            raise BadRequest('此接口不接受查询参数')
        return default
    if key not in keys:
        return default
    values = query.getlist(key) if hasattr(query, 'getlist') else [query[key]]
    if len(values) != 1 or not isinstance(values[0], str):
        raise BadRequest('查询参数重复或无效')
    return values[0]


def _page(request):
    raw = _query_one(request, ('page',), 'page', '1')
    try:
        return max(1, min(100000, int(raw)))
    except (TypeError, ValueError):
        # Match operations_readonly.bounded_page's legacy fallback.
        return 1


def _ensure_fields(fields, allowed, required=()):
    unknown = set(fields) - set(allowed)
    if unknown or not set(required) <= set(fields):
        raise BadRequest('表单字段无效或缺少必填项')


def _content_type(request):
    value = request.headers.get('content-type', '').split(';', 1)[0].strip().lower()
    if value and value != 'application/x-www-form-urlencoded':
        raise BadRequest('请使用 URL 编码表单提交')


def _login_required():
    return response({'error': '需要登录控制台后操作', 'code': 'operation_requires_console_login'}, 403)


def build(app_module, *, service=None, operations=None, readonly=None, command_reader=None,
          command_counts_reader=None, settings_reader=None, validation_path=None,
          owner_resolver=None, allow_writes=True):
    """Build routes; dependencies are injectable so tests never touch live stores.

    ``allow_writes=False`` is mandatory for rehearsal hosts. It denies both
    settings changes and manual command submissions before reading their body.
    """
    if service is None:
        options = {'operations': operations, 'readonly': readonly,
                   'command_reader': command_reader,
                   'command_counts_reader': command_counts_reader,
                   'settings_reader': settings_reader}
        if validation_path is not None:
            options['validation_path'] = validation_path
        service = ICBUAdapter(**options)
    # A rehearsal process cannot opt back into live queue writes by omitting an
    # argument or accidentally passing True.
    allow_writes = bool(allow_writes) and os.environ.get('IMGW_REHEARSAL') != '1'
    resolve_owner = owner_resolver or (lambda: _owner(app_module))

    def read(operation, unavailable):
        async def serve(request):
            if not authorised(request):
                return forbidden(app_module, request)
            try:
                payload = await run_in_threadpool(operation, request)
                code = 200
            except (BadRequest, FormBadRequest, ArchiveBadRequest) as error:
                payload, code = {'error': str(error)}, 400
            except Missing as error:
                payload, code = {'error': str(error)}, 404
            except (Conflict, Disabled) as error:
                payload, code = {'error': str(error)}, 409
            except Exception:
                payload, code = {'error': unavailable}, 503
            if not await recheck(request):
                return forbidden(app_module, request)
            return response(payload, code)
        return serve

    def write(operation, unavailable):
        async def serve(request):
            session = getattr(request.state, 'session', None)
            console_login = (bool(getattr(request.state, 'proxy_trusted', False))
                             or bool(session and getattr(session, 'authenticated', False)))
            if not console_login:
                return _login_required()
            if not authorised(request):
                return forbidden(app_module, request)
            if not await recheck(request):
                return forbidden(app_module, request)
            if not allow_writes:
                return response({'error': '演练环境禁止阿里草稿写操作', 'code': 'writes_disabled'}, 403)
            try:
                _content_type(request)
                fields = await form_fields(request)
                payload = await run_in_threadpool(operation, request, fields)
                code = 200
            except (BadRequest, FormBadRequest, ArchiveBadRequest) as error:
                payload, code = {'error': str(error)}, 400
            except Missing as error:
                payload, code = {'error': str(error)}, 404
            except Conflict as error:
                payload, code = {'error': str(error)}, 409
            except ValueError:
                # Legacy validators raise opaque English codes; do not leak them.
                payload, code = {'error': '阿里草稿操作条件未通过，请刷新后核对'}, 409
            except Exception:
                payload, code = {'error': unavailable}, 503
            return response(payload, code)
        return serve

    def list_drafts(request):
        return service.drafts(_page(request))

    def detail(request):
        _query_one(request, (), None)
        return service.draft(request.path_params.get('identifier', ''))

    def settings_read(request):
        _query_one(request, (), None)
        return service.settings()

    def settings_write(request, fields):
        _ensure_fields(fields, ('mode', 'conversations'), required=('mode',))
        mode = fields['mode']
        if mode not in ALLOWED_MODES:
            raise Conflict('运行模式无效')
        conversations_text = fields.get('conversations', '')
        conversations = [line.strip() for line in conversations_text.splitlines() if line.strip()]
        if (len(conversations) > 200
                or any(CONVERSATION.fullmatch(item) is None for item in conversations)):
            raise Conflict('会话列表无效')
        if mode == 'test_auto' and not conversations:
            raise Conflict('测试自动模式至少需要一个会话')
        return service.configure(mode, conversations)

    def command_write(request, fields):
        _ensure_fields(fields, ('action', 'draft_sha', 'text'),
                       required=('action', 'draft_sha', 'text'))
        return service.command(request.path_params.get('identifier', ''), fields['action'],
                               fields['draft_sha'], fields['text'])

    def status_read(request):
        _query_one(request, (), None)
        return service.status(resolve_owner())

    def validation_read(request):
        _query_one(request, (), None)
        return service.validation()

    return [
        Route(PREFIX + 'drafts', read(list_drafts, '阿里草稿列表暂时无法读取'),
              methods=['GET'], name='icbu_drafts'),
        Route(PREFIX + 'drafts/{identifier}', read(detail, '阿里草稿暂时无法读取'),
              methods=['GET'], name='icbu_draft_detail'),
        Route(PREFIX + 'drafts/{identifier}/command', write(command_write, '阿里执行操作暂时无法完成'),
              methods=['POST'], name='icbu_draft_command'),
        Route(PREFIX + 'settings', read(settings_read, '阿里运行设置暂时无法读取'),
              methods=['GET'], name='icbu_settings'),
        Route(PREFIX + 'settings', write(settings_write, '阿里运行设置暂时无法保存'),
              methods=['POST'], name='icbu_settings_update'),
        Route(PREFIX + 'status', read(status_read, '阿里执行状态暂时无法读取'),
              methods=['GET'], name='icbu_status'),
        Route(PREFIX + 'validation', read(validation_read, '合成草稿验收记录暂时无法读取'),
              methods=['GET'], name='icbu_validation'),
    ]
