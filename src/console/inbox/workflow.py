"""W2 operating state, customer/deal links and draft-only supplier questions.

State and action receipts share the archive transaction with Deals. Source channel
messages are read-only. DryRun writes a separate, unsendable outbox table.
"""
from datetime import datetime, timezone
import hashlib
import json
import re
import time

from console.archive.store import Store, ARCHIVE_PATH, insert
from console.archive import validation as v
from console.customers import aggregate_customers, public_customer
from console.deals.service import Deals
from console.deals import validation as dv
from console.inbox.model import make_key, message_version as version
from console.customers.identity import bindings, canonical
from console.outbox.dry_run import DryRun

SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS inbox_workflow (
     conversation_key TEXT PRIMARY KEY, customer_key TEXT, status TEXT,
     remind_at REAL, message_version TEXT, updated_at REAL NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS inbox_action_receipts (
     request_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, result_json TEXT NOT NULL)''',
)



def text(fields, name, limit=4000, required=True):
    return v.text(fields.get(name, ''), limit, required)


class Workflow:
    def __init__(self, inbox, outbox_path, archive_path=ARCHIVE_PATH, clock=None):
        self.inbox = inbox
        self.clock = clock or time.time
        self.store = Store(archive_path)
        self.deals = Deals(store=self.store, inbox=inbox, clock=self.clock)
        self.outbox = DryRun(outbox_path, self.clock)

    def states(self):
        with self.store.read() as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='inbox_workflow'").fetchone():
                return {}
            return {r['conversation_key']: dict(r) for r in db.execute('SELECT * FROM inbox_workflow')}

    def bindings(self):
        with self.store.read() as db:
            return bindings(db)

    def project(self, conv, states=None, customers=None, owners=None):
        state = (self.states() if states is None else states).get(conv['key'], {})
        fresh = state.get('message_version') == version(conv)
        remind = state.get('remind_at') if fresh else None
        done = fresh and state.get('status') == 'done'
        due = remind is not None and remind <= self.clock()
        status = 'done' if done else 'replied' if remind and not due else 'waiting' if due or conv['waiting_reply'] else 'replied'
        customer_key = state.get('customer_key') or make_key('customer', conv['channel'], conv['account'], conv['buyer_name'])
        if customers is None:
            customers = aggregate_customers(self.inbox.read()['conversations'].values())
        customer = customers.get(customer_key)
        owner = (self.bindings() if owners is None else owners).get(conv['key'])
        if owner and owner['kind'] == 'customer':
            return dict(workflow_state=status, remind_at=remind, reminder_due=due, customer_key=customer_key if customer else None, customer_party_id=owner['id'], customer_name=owner['name'])
        return dict(workflow_state=status, remind_at=remind, reminder_due=due,
                    customer_key=customer_key if customer else None,
                    customer_name=customer['buyer_name'] if customer else None)

    def context(self, key):
        conv = self.require(key)
        source = self.inbox.read()['conversations'].get(key, conv)
        p = self.project(source)
        customers = aggregate_customers(self.inbox.read()['conversations'].values())
        customer = customers.get(p['customer_key'])
        linked = self.deals.read('by_conversation', {'key': key})
        party = linked['party']
        if party and party['kind'] == 'customer':
            owners = self.bindings()
            convs = [c for k, c in self.inbox.read()['conversations'].items() if owners.get(k, {}).get('id') == party['id']]
            customer = dict(key=str(party['id']), party_id=party['id'], buyer_name=party['name'], country=party.get('country') or '', channel=conv['channel'], account=conv['account'], n_conversations=len(convs), n_messages=sum(c['n_messages'] for c in convs), last_seen=max((c['last_seen'] for c in convs), default=conv['last_seen']))
        suppliers = [dict(key=c['key'], buyer_name=c['buyer_name'], channel_label=c['channel_label'])
                     for c in self.inbox.read()['conversations'].values()
                     if c['counterpart_role'] == 'supplier' and c.get('draft_eligible', True)]
        return dict(**p, customer=customer if party and party['kind'] == 'customer' else public_customer(customer, detail=True) if customer else None,
                    deals=linked['deals'], party=linked['party'], suppliers=suppliers,
                    outbox=self.outbox.recent(key), dry_run=True)

    def require(self, key):
        conv = self.inbox.detail(key)
        if conv is None:
            raise v.Missing('未找到会话')
        return conv

    def supplier_draft(self, key, fields, who):
        v.whitelist(fields, ('request_id', 'supplier_key', 'product'))
        conv = self.require(key)
        if not conv.get('draft_eligible', True):
            raise v.Conflict('这个会话暂不能起草，请先核对会话分类')
        supplier = self.require(text(fields, 'supplier_key', 8192))
        if supplier['counterpart_role'] != 'supplier' or not supplier.get('draft_eligible', True):
            raise v.BadRequest('请选择可沟通的供应商会话')
        question = next((m['text'] for m in reversed(conv['messages']) if m['role'] == 'buyer' and m['text'].strip()), '')
        if not question:
            raise v.BadRequest('会话中没有客户问题，请先核对消息')
        linked = self.deals.read('by_conversation', {'key': key})
        products = []
        for deal in linked['deals']:
            products.extend(i['name'] for i in self.deals.read('detail', row_id=deal['id'])['items'])
        extra = text(fields, 'product', 500, False)
        if extra:
            products.append(extra)
        if not products:
            raise v.BadRequest('请补充产品名称，或先在关联案子中添加产品')
        # Only the selected customer's question and product names are transferred.
        # Do not copy internal prices, other buyer histories or full deal notes.
        body = '请协助核实以下产品和问题：\n产品：' + '、'.join(dict.fromkeys(products)) + '\n客户问题：' + question + '\n请提供可确认的规格和交期，未确认的事项请注明。'
        row = self.outbox.save(supplier['key'], supplier['channel'], key, body,
                               self.request_id(fields), who, 'draft')
        return dict(message=row, recipient=supplier['buyer_name'], notice='已起草到供应商会话，核对后再确认发送（演练）')

    @staticmethod
    def request_id(fields):
        value = fields.get('request_id', '')
        if not re.fullmatch(r'[A-Za-z0-9_-]{8,100}', value):
            raise v.BadRequest('请刷新后重新操作')
        return value

    def action(self, key, fields, who):
        v.whitelist(fields, ('action', 'request_id', 'text', 'confirm', 'dry_run', 'customer_key',
                             'title', 'deal_id', 'remind_at', 'supplier_key', 'draft_id', 'customer_party_id'))
        request_id = self.request_id(fields)
        conv = self.require(key)
        action = fields.get('action')
        if action in ('send', 'supplier_send'):
            if fields.get('dry_run') != '1' or fields.get('confirm') != '1':
                raise v.BadRequest('请人工确认演练草稿；本入口不能外发')
            if not conv.get('draft_eligible', True):
                raise v.Conflict('这个会话暂不能起草，请先核对会话分类')
            body = text(fields, 'text')
            if '【待确认' in body:
                raise v.BadRequest('请先核对并修改待确认内容')
            target = conv
            if action == 'supplier_send':
                target = self.require(text(fields, 'supplier_key', 8192))
                if target['counterpart_role'] != 'supplier' or not target.get('draft_eligible', True):
                    raise v.BadRequest('请选择可沟通的供应商会话')
                draft_id = dv.identity(fields.get('draft_id', ''))
                if not any(r['id'] == draft_id and r['source_key'] == key and
                           r['conversation_key'] == target['key'] and r['state'] == 'draft'
                           for r in self.outbox.recent(key)):
                    raise v.BadRequest('请先为这个供应商起草询问')
            row = self.outbox.save(target['key'], target['channel'], key, body, request_id, who)
            return dict(message=row, notice='已进入发送队列（演练）')
        fingerprint = hashlib.sha256(json.dumps([key, fields], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        stamp = datetime.fromtimestamp(self.clock(), timezone.utc).isoformat()
        with self.store.write() as db:
            for statement in SCHEMA:
                db.execute(statement)
            old = db.execute('SELECT * FROM inbox_action_receipts WHERE request_id=?', (request_id,)).fetchone()
            if old:
                if old['fingerprint'] != fingerprint:
                    raise v.Conflict('这次操作已保存，请核对后再操作')
                return json.loads(old['result_json'])
            db.execute('INSERT INTO inbox_workflow(conversation_key,updated_at) VALUES(?,?) '
                       'ON CONFLICT DO NOTHING', (key, self.clock()))
            result = dict(ok=True)
            if action in ('done', 'reopen', 'remind'):
                remind = None
                if action == 'remind':
                    try:
                        raw = datetime.fromisoformat(fields.get('remind_at', '').replace('Z', '+00:00'))
                        if raw.utcoffset() is None:
                            raise ValueError
                        remind = raw.timestamp()
                        if not self.clock() < remind <= self.clock() + 366 * 86400:
                            raise ValueError
                    except (ValueError, OverflowError):
                        raise v.BadRequest('请选择未来一年内的提醒时间') from None
                db.execute('UPDATE inbox_workflow SET status=?,remind_at=?,message_version=?,updated_at=? '
                           'WHERE conversation_key=?', ('done' if action == 'done' else None, remind,
                                                         version(self.inbox.read()['conversations'].get(key, conv)), self.clock(), key))
                result['notice'] = {'done': '已标记已处理，新消息到达会再次提醒',
                                    'reopen': '已恢复跟进', 'remind': '提醒已记下，到时会回到要我回复'}[action]
            elif action == 'assign':
                if fields.get('customer_party_id'):
                    from console.archive.service import get
                    party_id = canonical(db, dv.identity(fields['customer_party_id']))
                    party = get(db, 'parties', party_id)
                    if party['kind'] != 'customer' or party['status'] != 'active':
                        raise v.BadRequest('请选择有效客户')
                    name = party['name']
                    customer_key = None
                else:
                    customers = aggregate_customers(self.inbox.read()['conversations'].values())
                    customer = customers.get(fields.get('customer_key'))
                    if not customer:
                        raise v.BadRequest('请选择客户列表中的客户')
                    name, customer_key = customer['buyer_name'], customer['key']
                    party = db.execute("SELECT id FROM parties WHERE kind='customer' AND name=?", (name,)).fetchone()
                    party_id = canonical(db, party['id']) if party else insert(db, 'parties', dict(kind='customer', name=name,
                        short_name=name, country=customer['country'], created_at=stamp, updated_at=stamp), who)
                old = db.execute('SELECT party_id FROM identities WHERE conversation_key=?', (key,)).fetchone()
                # Linking to the displayed canonical customer must not move the original FK.
                if not old or canonical(db, old['party_id']) != party_id:
                    self.deals._identity(db, key, party_id, who, stamp, fields.get('confirm') == '1')
                db.execute('UPDATE inbox_workflow SET customer_key=?,updated_at=? WHERE conversation_key=?',
                           (customer_key, self.clock(), key))
                result['notice'] = '会话已归到' + name
            elif action == 'create_deal':
                # Repeated intent, including a refreshed browser, must not create
                # multiple open deals from the same source conversation.
                existing = db.execute("SELECT id FROM deals WHERE source_conversation_key=? AND status='open' ORDER BY id LIMIT 1", (key,)).fetchone()
                if existing:
                    result['id'] = existing['id']
                else:
                    values = dv.fields('create', dict(conversation_key=key, party_kind='supplier' if conv['counterpart_role'] == 'supplier' else 'customer',
                        party_name=conv['buyer_name'], title=text(fields, 'title', 500, False) or conv['buyer_name'] + '的询盘'))
                    result.update(self.deals._write(db, 'create', None, values, who, stamp, None))
                result['notice'] = '案子已建好，会话已关联'
            elif action == 'link_deal':
                deal_id = dv.identity(fields.get('deal_id', ''))
                values = dv.fields('conversation', dict(conversation_key=key))
                result.update(self.deals._write(db, 'conversation', deal_id, values, who, stamp, None))
                result['notice'] = '会话已关联到案子'
            else:
                raise v.BadRequest('请选择有效操作')
            db.execute('INSERT INTO inbox_action_receipts VALUES(?,?,?)',
                       (request_id, fingerprint, json.dumps(result, ensure_ascii=False)))
        return result
