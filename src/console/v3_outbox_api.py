"""Authenticated, explicit POST sends. CSRF remains the host middleware's job."""
from pathlib import Path
from urllib.parse import parse_qs

from .outbox.attachments import MAX_SIZE

from starlette.concurrency import run_in_threadpool
from starlette.routing import Route

from .channels import build_inbox
from .channels.whatsapp import CONFIG_PATH
from .inbox.http import authorised, forbidden, form_fields, get_handler, recheck, response
from .inbox.model import BadRequest, decode_key, single
from .inbox.store import INBOUND_PATH
from .outbox import BridgeClient, Outbox, OUTBOX_PATH


def build(app_module, *, outbox=None, inbox=None, outbox_path=None,
          config_path=None, api=None, post=None, get=None, stat_fn=None, group_gid=None,
          clock=None, inbound_path=INBOUND_PATH, wechat_path=None, fetch=None, candidate_path=None):
    local = Path(inbound_path) != Path(INBOUND_PATH)
    root = Path(inbound_path).parent
    if outbox_path is None:
        outbox_path = root / 'outbox.sqlite3' if local else OUTBOX_PATH
    if config_path is None:
        config_path = root / 'disabled-whatsapp.json' if local else CONFIG_PATH
    service = outbox if outbox is not None else Outbox(outbox_path, inbox=inbox if inbox is not None else
        build_inbox(inbound_path, wechat_path=wechat_path, whatsapp_config_path=config_path, fetch=fetch,
                    stat_fn=stat_fn, group_gid=group_gid),
        bridge=BridgeClient(config_path, api=api, post=post, get=get, stat_fn=stat_fn, group_gid=group_gid), clock=clock, candidate_path=candidate_path)

    def conversation(request):
        key = single(request.query_params, 'conversation')
        decode_key(key, 'conversation')
        return key

    def read(parse, operation):
        return get_handler(app_module, parse, lambda value: (operation(value), 200), '发送记录暂时无法读取')

    def write(operation, action):
        async def serve(request):
            if not authorised(request) or not await recheck(request):
                return forbidden(app_module, request)
            try:
                fields = await form_fields(request)
                payload, code = await run_in_threadpool(operation, request, fields)
            except BadRequest:
                try:
                    await run_in_threadpool(service.reject, 'form_invalid', action=action)
                    payload, code = {'error': '表单参数无效，请每项传入一个文本值'}, 400
                except Exception:
                    payload, code = {'error': '发送服务暂时不可用，请先核对发送记录'}, 503
            except Exception:
                payload, code = {'error': '发送服务暂时不可用，请先核对发送记录'}, 503
            result = response(payload, code)
            if code == 429:
                result.headers['Retry-After'] = str(payload['retry_after'])
            return result
        return serve

    async def candidate(request):
        if not authorised(request) or not await recheck(request):
            return forbidden(app_module, request)
        try:
            limit = 12 * ((MAX_SIZE + 2) // 3) + 8192
            length = request.headers.get('content-length')
            if length and (not length.isdigit() or int(length) > limit):
                raise BadRequest('文件超过大小限制，请选择较小的文件')
            if request.headers.get('content-type', '').split(';')[0].lower() != 'application/x-www-form-urlencoded':
                raise BadRequest('上传格式无效，请重新选择文件')
            raw = getattr(request.state, 'form', None)
            if raw is None:
                chunks = bytearray()
                async for chunk in request.stream():
                    chunks.extend(chunk)
                    if len(chunks) > limit:
                        raise BadRequest('文件超过大小限制，请选择较小的文件')
                raw = parse_qs(chunks.decode('utf8', 'strict'), keep_blank_values=True, max_num_fields=8)
            if set(raw) != {'filename', 'mimetype', 'data', 'request_id'} or any(len(v) != 1 for v in raw.values()):
                raise BadRequest('附件参数无效，请重新选择文件')
            fields = {k: v[0] for k, v in raw.items()}
            payload, code = await run_in_threadpool(service.create_attachment, request.path_params.get('key', ''), fields)
        except (BadRequest, UnicodeError, ValueError) as exc:
            payload, code = {'error': str(exc) if isinstance(exc, BadRequest) else '附件参数无效，请重新选择文件'}, 400
        except Exception:
            payload, code = {'error': '附件候选暂时无法保存，请稍后再试'}, 503
        return response(payload, code)

    return [
        Route('/api/v1/inbox/conversations/{key}/attachments', candidate,
              methods=['POST'], name='outbox_attachment_candidate'),
        Route('/api/v1/inbox/conversations/{key}/attachments/send',
              write(lambda r, f: service.send_attachment(r.path_params.get('key', ''), f), 'send_rejected'),
              methods=['POST'], name='outbox_attachment_send'),
        Route('/api/v1/outbox/settings', read(lambda r: None, lambda _: service.settings()),
              methods=['GET'], name='outbox_settings'),
        Route('/api/v1/outbox/settings', write(lambda r, f: service.set_settings(f), 'settings_rejected'),
              methods=['POST'], name='outbox_settings_update'),
        Route('/api/v1/inbox/conversations/{key}/send',
              write(lambda r, f: service.send(r.path_params.get('key', ''), f), 'send_rejected'),
              methods=['POST'], name='outbox_send'),
        Route('/api/v1/outbox/recent', read(lambda r: None, lambda _: service.recent()),
              methods=['GET'], name='outbox_recent'),
        Route('/api/v1/outbox', read(conversation, service.recent), methods=['GET'], name='outbox_list'),
    ]
