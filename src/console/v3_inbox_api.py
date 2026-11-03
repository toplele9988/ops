"""Inbox JSON and AI reply drafts, with read-only outbox availability projection.

Register the returned routes behind the console middleware (including CSRF).
All blocking work, including the injectable AI runner, runs in a worker thread.
"""
from pathlib import Path
import re
import hashlib
import json
import time

from starlette.concurrency import run_in_threadpool
from starlette.routing import Route

from .inbox.ai import official_runner
from .inbox import ai, drafts
from .inbox.http import authorised, forbidden, form_fields, get_handler, recheck, response
from .inbox.model import (BadRequest, conversation_item, decode_key,
                          matches, paginate, parameters)
from .inbox.store import DRAFTS_PATH, INBOUND_PATH
from .channels import build_inbox
from .channels.whatsapp import CONFIG_PATH
from .outbox import BridgeClient, Outbox, OUTBOX_PATH
from .kb import Hub, default_hub
from .kb.drafting import draft_context
from .classify import Policy, PolicyInbox
from .inbox.workflow import Workflow, version
from .customers import aggregate_customers
from .archive.store import ARCHIVE_PATH
from .archive import validation as v


def build(app_module, inbound_path=INBOUND_PATH, drafts_path=DRAFTS_PATH, runner=None, *,
          inbox=None, wechat_path=None, whatsapp_config_path=None, fetch=None, clock=None,
          include_roles=('customer', 'supplier', 'unclassified'), stat_fn=None, group_gid=None,
          outbox=None, outbox_path=None, bridge=None, hub=None, knowledge_timeout=3, classify_path=None, archive_path=None):
    cache = inbox if inbox is not None else build_inbox(inbound_path, wechat_path=wechat_path,
        whatsapp_config_path=whatsapp_config_path, fetch=fetch, clock=clock, include_roles=include_roles,
        stat_fn=stat_fn, group_gid=group_gid)
    draft_path = Path(drafts_path)
    run = official_runner if runner is None else runner
    # Explicit nonproduction inbound paths keep the default outbox/config isolated too.
    local = Path(inbound_path) != Path(INBOUND_PATH)
    root = Path(inbound_path).parent
    policy_path = classify_path
    if policy_path is None:
        if local:
            policy_path = root / 'classify.sqlite3'
        elif draft_path != Path(DRAFTS_PATH):
            policy_path = draft_path.parent / 'classify.sqlite3'
    policy = Policy(policy_path)
    cache = PolicyInbox(cache, policy)
    knowledge_hub = hub if hub is not None else (Hub(root / 'catalog.sqlite3', root / 'company-brief.md',
        root / 'kb.sqlite3', embedder=None) if local else default_hub())
    if outbox_path is None:
        outbox_path = root / 'outbox.sqlite3' if local else OUTBOX_PATH
    bridge_config = whatsapp_config_path
    if bridge_config is None:
        bridge_config = root / 'disabled-whatsapp.json' if local else CONFIG_PATH
    send_service = outbox if outbox is not None else Outbox(outbox_path,
        bridge=bridge if bridge is not None else BridgeClient(bridge_config, stat_fn=stat_fn, group_gid=group_gid))

    workflow = Workflow(cache, outbox_path, archive_path or (root / "archive.sqlite3" if local else ARCHIVE_PATH), clock)

    def summary(_):
        result = cache.summary(time.time())
        _, new = drafts.read_drafts(draft_path)
        snapshot = cache.read()
        states = workflow.states()
        customers = aggregate_customers(snapshot['conversations'].values())
        projected = [(c, workflow.project(c, states, customers)) for c in snapshot['conversations'].values()]
        result['waiting_total'] = sum(p['workflow_state'] == 'waiting' for c, p in projected
                                      if c['counterpart_role'] != 'supplier')
        for channel in result.get('by_channel', []):
            channel['waiting'] = sum(p['workflow_state'] == 'waiting' for c, p in projected
                                     if c['channel'] == channel['channel'] and c['counterpart_role'] != 'supplier')
        result['revision'] = hashlib.sha256(json.dumps(sorted((c['key'], version(c)) for c, p in projected)).encode()).hexdigest()
        result['conversation_count'] = len(projected)
        return dict(result, draft_new=new), 200

    def conversations(values):
        snapshot = cache.read()
        latest, _ = drafts.read_drafts(draft_path)
        now = time.time()
        states = workflow.states()
        state = values.pop('workflow_state', values['state'])
        values['state'] = 'all'
        rows = []
        customers = aggregate_customers(snapshot['conversations'].values())
        for conv in snapshot['conversations'].values():
            projected = workflow.project(conv, states, customers)
            if state != 'all' and projected['workflow_state'] != state:
                continue
            search = ' '.join(m['text'] for m in conv['messages']) + ' ' + (projected['customer_name'] or '')
            if matches(conv, values, search):
                rows.append(dict(conversation_item(conv, now, latest), **projected))
        rows.sort(key=lambda c: (c['workflow_state'] != 'waiting', -c['last']['sent_at'], c['key']))
        return paginate(rows, values, snapshot['ready']), 200

    def list_parameters(request):
        state = request.query_params.get('state', 'all')
        from starlette.datastructures import QueryParams
        from .inbox.model import single
        single(request.query_params, 'state', 'all')
        pairs = request.query_params.multi_items() if hasattr(request.query_params, 'multi_items') else request.query_params.items()
        params = QueryParams([(k, 'all' if k == 'state' and v == 'done' else v) for k, v in pairs])
        values = parameters(params)
        values['workflow_state'] = state
        return values

    def key_parameter(request):
        key = request.path_params.get('key', '')
        decode_key(key, 'conversation')
        return key

    def detail(key):
        conv = cache.detail(key)
        if conv is None:
            return {'error': '未找到会话'}, 404
        latest, _ = drafts.read_drafts(draft_path)
        fields = ('key', 'channel', 'account', 'buyer_name', 'country', 'first_seen', 'last_seen',
                  'n_messages', 'source_url', 'channel_label', 'counterpart_role', 'dormant', 'can_send', 'copy_hint')
        send_state = send_service.send_state(conv['channel'])
        can_send = send_state['enabled'] and send_state['available']
        conversation = {k: conv[k] for k in fields}
        conversation.update({k: conv[k] for k in ('classification', 'ignored', 'reply_eligible', 'draft_eligible', 'kb_eligible')})
        copy_hint = '请复制草稿到 WhatsApp 发送' if conv['channel'] == 'whatsapp' else conv['copy_hint']
        conversation.update(can_send=can_send, copy_hint='请核对收件人与内容，确认后从后台发送' if can_send else
                            send_state['reason'] + '；' + copy_hint)
        return dict(conversation=conversation, send_state=send_state,
                    messages=[dict(role=m['role'], text=m['text'], sent_at=m['sent_at'], sender_label=m.get('sender_label'), blocks=m.get('blocks'))
                              for m in conv['messages']], draft=latest.get(key)), 200

    def generate(key, request_id=None):
        conv = cache.detail(key)
        if conv is None:
            return {'error': '未找到会话'}, 404
        if not conv['draft_eligible']:
            return {'error': '这个会话已标记为忽略或内部；如需起草，请先到待分类页面撤销标记'}, 409
        existing = drafts.find_request(draft_path, key, request_id)
        if existing:
            return {'draft': existing}, 200
        retry = drafts.LIMITER.reserve(key, time.monotonic())
        if retry:
            return {'error': '刚刚生成过，请稍后再试', 'retry_after': retry}, 429
        try:
            try:
                knowledge = draft_context(knowledge_hub, conv, timeout=knowledge_timeout)
                draft = ai.parse_output(run(ai.make_prompt(conv['messages'], conv['channel'],
                    conv['counterpart_role'], knowledge=knowledge['text'])))
                draft['sources'] = [{k: s[k] for k in ('title', 'type', 'date')} for s in knowledge['sources']]
                draft['text'] = re.sub(r'资料\s*[0-9]+|\[[0-9]+\]', '', draft['text']).strip()
                if not draft['text']:
                    raise ValueError(ai.AI_ERROR)
                if draft['sources'] and re.search(r'[0-9]', draft['text']):
                    draft['needs_confirmation'] = (['请核对所用资料的数字、来源与日期']
                        + draft['needs_confirmation'])[:10]
            except Exception:
                return {'error': ai.AI_ERROR}, 502
            # Local persistence only; there is no send adapter or send operation.
            if not policy.flags(key)['draft_eligible']:
                return {'error': '起草期间这个会话被标记为忽略或内部，结果未保存；请先核对分类'}, 409
            return {'draft': drafts.save_draft(draft_path, key, draft, time.time(), request_id)}, 200
        finally:
            drafts.LIMITER.finish(key)

    async def generate_draft(request):
        if not authorised(request) or not await recheck(request):
            return forbidden(app_module, request)
        try:
            key = key_parameter(request)
            fields = await form_fields(request)
            request_id = workflow.request_id(fields) if 'request_id' in fields else None
            payload, code = await run_in_threadpool(generate, key, request_id)
        except (BadRequest, v.BadRequest) as error:
            payload, code = {'error': str(error)}, 400
        except Exception:
            payload, code = {'error': '草稿暂时无法保存'}, 503
        result = response(payload, code)
        if code == 429:
            result.headers['Retry-After'] = str(payload['retry_after'])
        return result

    async def draft_status(request):
        if not authorised(request) or not await recheck(request):
            return forbidden(app_module, request)
        try:
            raw_id = request.path_params.get('id', '')
            if not re.fullmatch(r'[0-9]{1,18}', raw_id) or int(raw_id) < 1:
                raise BadRequest('草稿标识无效')
            fields = await form_fields(request)
            if fields.get('status') not in ('used', 'discarded'):
                raise BadRequest('草稿状态须为 used 或 discarded')
            result = await run_in_threadpool(drafts.set_status, draft_path, int(raw_id), fields['status'])
            payload, code = ({'draft': result}, 200) if result else ({'error': '未找到草稿'}, 404)
        except (BadRequest, v.BadRequest) as error:
            payload, code = {'error': str(error)}, 400
        except Exception:
            payload, code = {'error': '草稿状态暂时无法保存'}, 503
        return response(payload, code)

    async def operate(request):
        if not authorised(request) or not await recheck(request):
            return forbidden(app_module, request)
        try:
            key = key_parameter(request)
            fields = await form_fields(request)
            if not await recheck(request):
                return forbidden(app_module, request)
            call = workflow.supplier_draft if request.url.path.endswith('/supplier-draft') else workflow.action
            payload = await run_in_threadpool(call, key, fields, request.state.password_principal.username)
            code = 200
        except (BadRequest, v.BadRequest, v.Missing, v.Conflict) as error:
            payload, code = {'error': str(error)}, 404 if isinstance(error, v.Missing) else 409 if isinstance(error, v.Conflict) else 400
        except Exception:
            payload, code = {'error': '操作结果未能确认，请刷新记录并人工核对；不会自动重发'}, 503
        return response(payload, code)

    def context(key):
        try:
            return workflow.context(key), 200
        except v.Missing as error:
            return {'error': str(error)}, 404

    def get(parse, read):
        return get_handler(app_module, parse, read, '收件箱暂时无法读取')

    return [Route('/api/v1/inbox/conversations/{key}/ops/context', get(key_parameter, context), methods=['GET'], name='inbox_ops_context'),
            Route('/api/v1/inbox/conversations/{key}/ops/action', operate, methods=['POST'], name='inbox_ops_action'),
            Route('/api/v1/inbox/conversations/{key}/ops/supplier-draft', operate, methods=['POST'], name='inbox_ops_supplier_draft'),
            Route('/api/v1/inbox/summary', get(lambda r: None, summary), methods=['GET'], name='inbox_summary'),
            Route('/api/v1/inbox/conversations', get(list_parameters, conversations),
                  methods=['GET'], name='inbox_conversations'),
            Route('/api/v1/inbox/conversations/{key}', get(key_parameter, detail), methods=['GET'], name='inbox_detail'),
            Route('/api/v1/inbox/conversations/{key}/draft', generate_draft, methods=['POST'], name='inbox_draft'),
            Route('/api/v1/inbox/drafts/{id}/status', draft_status, methods=['POST'], name='inbox_draft_status')]
