"""Project existing local records into actionable reminders, without changing them.

Schema contract: archive migrations v2/v3/v4/v8. Only source snapshots are read;
no Store, migrations, AI runner, outbox or WhatsApp bridge is constructed here.
"""
from datetime import date, datetime, timedelta
import hashlib
import json
from pathlib import Path
import time
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from console.archive.store import ARCHIVE_PATH
from console.fulfil.nodes import progress
from console.inbox.store import INBOUND_PATH, WECHAT_PATH
from console.overview import Overview
from console.overview.safety import database
from console.pipeline import PipelineHealth
from . import inbox as local_inbox

KINDS = dict(buyer_reply='待回复买家', supplier_chase='要催的供应商',
             payment='待确认收款', quote_expiry='报价到期', logistics='物流节点超期',
             ownership='待你确认归属', pipeline='管道异常')


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     default=str).encode()).hexdigest()[:24]


def inbox_link(key, channel=''):
    # c is a compatibility alias until the inbox owner accepts conversation.
    return '/inbox?' + urlencode(dict(conversation=key, c=key, role='all', channel=channel))


class OpsQueue:
    def __init__(self, *, paths=None, inbox=None, overview=None, pipeline=None, clock=None):
        self.clock = clock or time.time
        defaults = dict(archive=ARCHIVE_PATH, inbound=INBOUND_PATH, wechat=WECHAT_PATH)
        if paths is not None:
            if not paths or set(paths) - set(defaults):
                raise ValueError('待办来源参数无效')
            if any(not Path(p).is_absolute() for p in paths.values()):
                raise ValueError('待办来源须为绝对路径')
            root = Path(next(iter(paths.values()))).parent
            defaults = {key: paths.get(key, root / ('disabled-queue-' + key)) for key in defaults}
        self.paths = defaults
        self.inbox = inbox
        # Explicit test paths disable ALL omitted overview/pipeline sources.
        isolated = {'paths': {'inbound': defaults['inbound']}} if paths is not None else {}
        self.overview = overview if overview is not None else Overview(clock=self.clock, **isolated)
        self.pipeline = pipeline if pipeline is not None else PipelineHealth(clock=self.clock, config={}, **isolated)

    def read(self):
        now = self.clock()
        today = datetime.fromtimestamp(now, ZoneInfo('Asia/Shanghai')).date()
        items, unavailable = [], []

        def add(kind, identity, title, detail, to, action, priority, evidence, due=None):
            items.append(dict(id=kind + ':' + fingerprint(identity), kind=kind, label=KINDS[kind],
                title=title, detail=detail, to=to, action=action, priority=priority, due_on=due,
                revision=fingerprint(evidence)))

        conversations = {}
        if self.inbox is not None:
            try:
                snapshot = self.inbox.read(now)
                if not snapshot['ready']:
                    raise ValueError('unavailable')
                conversations = snapshot['conversations']
            except Exception:
                unavailable.append('会话待办暂时读不到，请到商机沟通核对。')
        else:
            for name, label, reader in [('inbound', '阿里国际站', local_inbox.alibaba),
                                         ('wechat', '微信', local_inbox.wechat)]:
                try:
                    conversations.update(reader(self.paths[name], now))
                except Exception:
                    unavailable.append(label + '会话待办暂时读不到，请到商机沟通核对。')
            unavailable.append('WhatsApp 待办未汇入：此处只读本地记录，请到商机沟通核对。')

        owners = {}
        # Separate source categories fail independently; missing tables are never empty success.
        def archive_read(label, reader):
            try:
                with database(self.paths['archive'], 1) as db:
                    import sqlite3
                    db.row_factory = sqlite3.Row
                    return reader(db)
            except Exception:
                unavailable.append(label + '暂时读不到，请到对应页面核对后刷新。')
                return None

        result = archive_read('会话归属', lambda db: {r['conversation_key']: dict(r) for r in db.execute(
            'SELECT i.conversation_key,p.kind,p.name FROM identities i JOIN parties p ON p.id=i.party_id')})
        owners.update(result or {})
        for key, conv in conversations.items():
            owner = owners.get(key, {})
            role = owner.get('kind', conv.get('counterpart_role', 'customer'))
            last = next((m for m in reversed(conv.get('messages', ())) if m['role'] != 'unknown'), None)
            if not last or conv.get('dormant') or conv.get('ignored'):
                continue
            age = max(0, now - last['sent_at']) / 3600
            name = owner.get('name') or conv.get('buyer_name') or '未命名联系人'
            target = inbox_link(key, conv['channel'])
            evidence = [last['sent_at'], last['role'], last.get('text'), role]
            if role == 'customer' and conv.get('waiting_reply'):
                add('buyer_reply', key, name, f'买家等了 {age:.0f} 小时，请核对需求并回复。',
                    target, '去回复买家', 2 if age >= 24 else 4, evidence)
            elif role in ('supplier', 'vendor', 'forwarder') and last['role'] == 'seller' and age >= 48:
                add('supplier_chase', key, name, f'询问后已等 {age:.0f} 小时，请跟进价格、资料或交期。',
                    target, '去催供应商', 4, evidence)

        def payments(db):
            return [dict(r) for r in db.execute("SELECT p.id,p.deal_id,p.updated_at,d.title FROM deal_payments p "
                "JOIN deals d ON d.id=p.deal_id WHERE p.status='pending' AND d.status!='lost'")]
        for row in archive_read('待核对收款', payments) or []:
            add('payment', row['id'], row['title'], '有待核对的收款记录；请先查到账凭证，再由你确认。',
                f"/deals/{row['deal_id']}", '去核对收款', 2, row)

        def quotes(db):
            return [dict(r) for r in db.execute("SELECT q.id,q.deal_id,q.valid_until,q.updated_at,d.title FROM quote_rounds q "
                "JOIN deals d ON d.id=q.deal_id WHERE d.status='open' AND q.status IN ('draft','sent','accepted') "
                "AND q.round_no=(SELECT MAX(q2.round_no) FROM quote_rounds q2 WHERE q2.deal_id=q.deal_id)")]
        for row in archive_read('报价有效期', quotes) or []:
            if not row['valid_until']:
                continue
            try:
                due = date.fromisoformat(row['valid_until'])
            except ValueError:
                unavailable.append('有报价有效期无法识别，请到案子核对。')
                continue
            if due <= today + timedelta(days=3):
                detail = '报价已到期，请核对成本并准备新一轮报价。' if due < today else '报价即将到期，请跟进买家并核对成本。'
                add('quote_expiry', row['id'], row['title'], detail, f"/deals/{row['deal_id']}",
                    '去跟进报价', 3 if due < today else 5, row, str(due))

        def logistics(db):
            # Reuse fulfilment's actual due-date, shipped and signed semantics.
            db.execute('SELECT id FROM fulfilment_nodes LIMIT 1')
            rows = [dict(r) for r in db.execute("SELECT * FROM deals WHERE status='won'")]
            result = []
            for deal in rows:
                value = progress(db, deal, str(today))
                if value['eligible'] and value['overdue']:
                    result.append((deal, value))
            return result
        for deal, value in archive_read('物流节点', logistics) or []:
            late = [n['label'] for n in value['items'] if n['overdue']]
            detail = '、'.join(late) or '承诺交期或预计到达日期'
            add('logistics', deal['id'], deal['title'], detail + '已超期，请核对实际进度并更新日期。',
                f"/deals/{deal['id']}", '去跟进物流', 1, value, value['next_due'])

        def triage(db):
            return [dict(r) for r in db.execute("SELECT id,conversation_key,updated_at FROM triage_suggestions WHERE status='pending'")]
        for row in archive_read('待确认归属', triage) or []:
            add('ownership', row['id'], '有一条会话等你确认归属', '核对往来单位和案子，再采用或修改建议。',
                '/triage?' + urlencode(dict(conversation=row['conversation_key'], suggestion=row['id'])),
                '去确认归属', 5, row)

        # One task per channel; do not turn a changing generated_at into a new revision.
        try:
            snapshot = self.overview.read()
            for channel in snapshot['channels']:
                stages = [s for s in channel['stages'] if s['state'] in ('interrupted', 'slow', 'unavailable')]
                if stages:
                    states = [(s['key'], s['state']) for s in stages]
                    urgent = any(s['state'] == 'interrupted' for s in stages)
                    add('pipeline', channel['key'], channel['label'] + '需要检查',
                        '、'.join(s['label'] for s in stages) + '有异常或暂时读不到，请查看恢复步骤。',
                        '/system/pipeline?' + urlencode(dict(channel=channel['key'])), '查看恢复步骤',
                        0 if urgent else 6, states)
        except Exception:
            unavailable.append('渠道汇总暂时读不到，请查看数据管道健康。')
        try:
            health = self.pipeline.read()
            jobs = health.get('failed_jobs', {})
            if jobs.get('count'):
                add('pipeline', 'failed_jobs', '有处理失败的任务', '请查看失败原因，核对恢复步骤后再处理。',
                    '/system/pipeline', '查看恢复步骤', 0, jobs['count'])
        except Exception:
            unavailable.append('数据处理任务暂时读不到，请查看数据管道健康。')

        items.sort(key=lambda item: (item['priority'], item['due_on'] or '9999', item['id']))
        return dict(generated_at=now, items=items, total=len(items), complete=not unavailable,
                    unavailable=list(dict.fromkeys(unavailable)),
                    counts={kind: sum(i['kind'] == kind for i in items) for kind in KINDS})
