"""Transactional deal operations, identities and read projections in the archive DB."""
from collections import defaultdict
from datetime import datetime, timezone
import sqlite3
import time

from console.archive import validation as v
from console.archive.service import get, page
from console.archive.store import Store, insert, update, audit
from console.inbox.model import decode_key
from . import validation
from .rules import transition, pending_templates
from .stages import STAGES, LABELS, progress, STATUSES
from .waiting import evaluate, inbox_lookup, seconds, OVERDUE_HOURS
from . import operations



class Deals:
    def __init__(self, path=None, *, store=None, inbox=None, lookup=None, clock=None, overdue_hours=OVERDUE_HOURS):
        self.store = store or (Store(path) if path is not None else Store())
        if inbox is None and lookup is None:
            from console.channels import shared_inbox
            inbox = shared_inbox()
        self.inbox = inbox
        self.lookup = lookup
        self.clock = clock or time.time
        self.overdue_hours = overdue_hours

    @staticmethod
    def _event(db, deal_id, kind, summary, who, stamp):
        insert(db, 'deal_events', dict(deal_id=deal_id, at=stamp, kind=kind, summary=summary, who=who), who)
        update(db, 'deals', deal_id, dict(updated_at=stamp), who)

    def quote_event(self, db, deal_id, summary, who, stamp, *, accepted=False):
        """Quotes call this inside their transaction; acceptance is not payment."""
        self._event(db, deal_id, 'system', summary, who, stamp)
        if accepted:
            from .stages import KEYS
            deal = get(db, 'deals', deal_id)
            if KEYS.index(deal['stage']) < KEYS.index('awaiting_payment'):
                self._write(db, 'update', deal_id, {'stage': 'awaiting_payment'}, who, stamp, None)

    def _participant(self, db, deal_id, party_id, who, stamp, role=None, note=None, edit=False):
        party = get(db, 'parties', party_id)
        old = db.execute('SELECT * FROM deal_participants WHERE deal_id=? AND party_id=?', (deal_id, party_id)).fetchone()
        if old:
            values = {}
            if edit and role is not None and role != old['role']:
                values['role'] = role
            if edit and note != old['note']:
                values['note'] = note
            if values:
                update(db, 'deal_participants', old['id'], values, who)
                self._event(db, deal_id, 'participant', '更新参与方', who, stamp)
            return old['id']
        row_id = insert(db, 'deal_participants', dict(deal_id=deal_id, party_id=party_id,
                        role=role or party['kind'], note=note, created_at=stamp), who)
        self._event(db, deal_id, 'participant', '添加参与方', who, stamp)
        return row_id

    def _identity(self, db, key, party_id, who, stamp, confirm=False):
        get(db, 'parties', party_id)
        old = db.execute('SELECT * FROM identities WHERE conversation_key=?', (key,)).fetchone()
        if old and old['party_id'] != party_id and not confirm:
            raise v.Conflict('该会话已属于其他往来单位，请确认后更换归属')
        if old:
            row_id = old['id']
            if old['party_id'] != party_id:
                update(db, 'identities', row_id, dict(party_id=party_id), who)
        else:
            row_id = insert(db, 'identities', dict(party_id=party_id, conversation_key=key,
                channel=decode_key(key, 'conversation')[0], created_at=stamp, created_by=who), who)
        # Keep existing links consistent with the single global identity. Historical
        # participants remain visible (possibly unlinked); do not erase their notes.
        for link in db.execute('SELECT * FROM deal_conversations WHERE conversation_key=?', (key,)).fetchall():
            if link['party_id'] != party_id:
                self._participant(db, link['deal_id'], party_id, who, stamp)
                update(db, 'deal_conversations', link['id'], dict(party_id=party_id), who)
                self._event(db, link['deal_id'], 'link', '更新会话归属', who, stamp)
        return row_id

    def _link(self, db, deal_id, key, party_id, who, stamp):
        owner = db.execute('SELECT party_id FROM identities WHERE conversation_key=?', (key,)).fetchone()
        if party_id is not None:
            self._identity(db, key, party_id, who, stamp)
        elif owner:
            party_id = owner['party_id']
        if party_id is not None:
            self._participant(db, deal_id, party_id, who, stamp)
        old = db.execute('SELECT * FROM deal_conversations WHERE deal_id=? AND conversation_key=?', (deal_id, key)).fetchone()
        if old:
            return old['id']
        row_id = insert(db, 'deal_conversations', dict(deal_id=deal_id, conversation_key=key,
                        party_id=party_id, created_at=stamp), who)
        self._event(db, deal_id, 'link', '关联会话', who, stamp)
        return row_id

    def _templates(self, db, deal, who, stamp):
        existing = {r[0] for r in db.execute('SELECT template_key FROM deal_tasks WHERE deal_id=?', (deal['id'],))}
        made = 0
        for template in pending_templates(deal['stage'], deal['status'], existing):
            insert(db, 'deal_tasks', dict(template, deal_id=deal['id'], created_at=stamp), who)
            made += 1
        if made:  # one timeline line per stage change, not one per task
            self._event(db, deal['id'], 'task', f'生成了 {made} 条阶段默认待办', who, stamp)

    @staticmethod
    def _remove(db, table, row_id, who):
        db.execute('DELETE FROM ' + table + ' WHERE id=?', (row_id,))
        audit(db, who, 'remove', table, row_id, ('id',))

    def write(self, operation, row_id, fields, who, child_id=None):
        fields, operation_key = operations.split(fields)
        values = validation.fields(operation, fields)
        stamp = datetime.fromtimestamp(self.clock(), timezone.utc).isoformat(timespec='microseconds')
        try:
            with self.store.write() as db:
                return operations.once(db, f'deals:{who}:{operation}:{row_id}:{child_id}', operation_key,
                    fields, lambda: self._write(db, operation, row_id, values, who, stamp, child_id))
        except sqlite3.IntegrityError:
            raise v.Conflict('记录重复或关联、字段约束不满足') from None

    def _write(self, db, op, row_id, values, who, stamp, child_id, *, payment_confirmed=False):
        if op == 'identity':
            identity = self._identity(db, values['conversation_key'], values['party_id'], who, stamp, values.get('confirm', False))
            return dict(ok=True, id=identity)
        if op == 'create':
            key = values.get('conversation_key')
            customer_id = values.get('customer_party_id')
            owner = db.execute('SELECT party_id FROM identities WHERE conversation_key=?', (key,)).fetchone() if key else None
            owner_party = get(db, 'parties', owner['party_id']) if owner else None
            if owner_party and owner_party['kind'] == 'customer':
                if customer_id is not None and customer_id != owner_party['id']:
                    raise v.Conflict('会话已属于另一位客户，请先核对客户归属再建案')
                customer_id = owner_party['id']
            elif values.get('customer_name'):
                existing = db.execute("SELECT id FROM parties WHERE kind='customer' AND name=?", (values['customer_name'],)).fetchone()
                customer_id = existing['id'] if existing else insert(db, 'parties', dict(kind='customer',
                    name=values['customer_name'], short_name=values['customer_name'], created_at=stamp, updated_at=stamp), who)
            if customer_id is not None and get(db, 'parties', customer_id)['kind'] != 'customer':
                raise v.BadRequest('客户须选择客户类型的往来单位')
            party_id = owner['party_id'] if owner else None
            if key and not owner and values.get('party_name'):
                existing = db.execute('SELECT id FROM parties WHERE kind=? AND name=?', (values['party_kind'], values['party_name'])).fetchone()
                party_id = existing['id'] if existing else insert(db, 'parties', dict(kind=values['party_kind'],
                    name=values['party_name'], short_name=values['party_name'], created_at=stamp, updated_at=stamp), who)
            elif key and not owner and customer_id is not None:
                party_id = customer_id
            if customer_id is None and party_id is not None and get(db, 'parties', party_id)['kind'] == 'customer':
                customer_id = party_id
            customer = get(db, 'parties', customer_id) if customer_id is not None else None
            title = values.get('title') or ((customer['name'][:497] + '的案子') if customer else '未命名案子')
            changes, events = transition(dict(stage='inquiry', status='open'), {'stage': values['stage']}, stamp, creating=True)
            row_id = insert(db, 'deals', dict(title=title, customer_party_id=customer_id,
                source_conversation_key=key, created_at=stamp, updated_at=stamp, **changes), who)
            self._event(db, row_id, 'created', '创建案子', who, stamp)
            if customer_id is not None:
                self._participant(db, row_id, customer_id, who, stamp, role='customer')
            if key:
                self._link(db, row_id, key, party_id, who, stamp)
            for kind, summary in events:
                self._event(db, row_id, kind, summary, who, stamp)
            self._templates(db, get(db, 'deals', row_id), who, stamp)
            if values.get('destination_country'):
                operations.context(db, row_id, values['destination_country'])
            if values.get('product_name'):
                self._write(db, 'item', row_id, dict(name=values['product_name'], qty=values['qty']), who, stamp, None)
            return dict(ok=True, id=row_id)
        if op == 'task_status':
            task = get(db, 'deal_tasks', row_id)
            if task['status'] != values['status']:
                update(db, 'deal_tasks', row_id, dict(values, done_at=stamp if values['status'] == 'done' else None), who)
                label = {'open': '重新打开待办', 'done': '完成待办', 'skipped': '跳过待办'}[values['status']]
                self._event(db, task['deal_id'], 'task', label, who, stamp)
            return dict(ok=True, id=row_id)
        deal = get(db, 'deals', row_id)
        if op == 'draft':
            kind = values.get('kind')
            if not kind:
                raise v.BadRequest('请选择要起草的内容')
            if kind == 'supplier_order' and deal['status'] != 'won':
                raise v.BadRequest('请先人工确认收款，再起草采购单')
            party_id = values.get('party_id')
            participant = db.execute('SELECT role FROM deal_participants WHERE deal_id=? AND party_id=?', (row_id, party_id)).fetchone()
            if not participant or participant['role'] not in ('supplier', 'forwarder', 'vendor'):
                raise v.BadRequest('请先把供应商或货代加入这个案子')
            key = values.get('conversation_key')
            if key and not db.execute('SELECT 1 FROM deal_conversations WHERE deal_id=? AND party_id=? AND conversation_key=?', (row_id, party_id, key)).fetchone():
                raise v.BadRequest('请选择该参与方在本案已关联的会话')
            items = list(db.execute('SELECT name,qty FROM deal_items WHERE deal_id=? ORDER BY id', (row_id,)))
            text = ('采购单草稿，请核对交期：' if kind == 'supplier_order' else '请提供新的有效报价、有效期与交期：') + '；'.join(f"{it['name']} × {it['qty'] or '待确认'}" for it in items)
            result = operations.save_draft(db, row_id, kind, text, who, stamp, conversation_key=key)
            self._event(db, row_id, 'system', '采购单已存演练草稿，未发送' if kind == 'supplier_order' else '询价已存演练草稿，未发送', who, stamp)
            return result
        if op == 'update':
            changes, events = transition(deal, values, stamp, payment_confirmed=payment_confirmed)
            if changes:
                update(db, 'deals', row_id, dict(changes, updated_at=stamp), who)
                for kind, summary in events:
                    self._event(db, row_id, kind, summary, who, stamp)
                if set(values) - {'stage', 'status'}:
                    self._event(db, row_id, 'system', '更新案子资料', who, stamp)
                if deal['stage'] != changes.get('stage', deal['stage']):
                    self._templates(db, get(db, 'deals', row_id), who, stamp)
        elif op == 'participant':
            child_id = self._participant(db, row_id, values['party_id'], who, stamp,
                                         values.get('role'), values.get('note'), edit=True)
        elif op == 'conversation':
            child_id = self._link(db, row_id, values['conversation_key'], values.get('party_id'), who, stamp)
        elif op in ('item', 'task'):
            if op == 'item' and values.get('product_id') is not None:
                get(db, 'products', values['product_id'])
            if op == 'task':
                values.update(stage=deal['stage'], created_at=stamp)
            child_id = insert(db, 'deal_' + ('items' if op == 'item' else 'tasks'), dict(values, deal_id=row_id), who)
            self._event(db, row_id, op, '添加产品项' if op == 'item' else '添加待办', who, stamp)
        elif op.endswith('_remove'):
            resource = op.removesuffix('_remove')
            table = 'deal_' + {'participant': 'participants', 'conversation': 'conversations', 'item': 'items'}[resource]
            child = get(db, table, child_id)
            if child['deal_id'] != row_id:
                raise v.Missing('未找到该案子内的记录')
            if resource == 'participant':
                for link in db.execute('SELECT id FROM deal_conversations WHERE deal_id=? AND party_id=?',
                                       (row_id, child['party_id'])).fetchall():
                    self._remove(db, 'deal_conversations', link['id'], who)
            self._remove(db, table, child_id, who)
            self._event(db, row_id, {'participant': 'participant', 'conversation': 'link', 'item': 'item'}[resource],
                        {'participant': '移除参与方及其案内会话关联', 'conversation': '解除会话关联', 'item': '移除产品项'}[resource], who, stamp)
        elif op == 'event':
            self._event(db, row_id, 'note', values['note'], who, stamp)
        else:
            raise v.BadRequest('操作无效')
        return dict(ok=True, id=child_id if child_id is not None else row_id)

    def read(self, resource, params=None, row_id=None):
        params = params or {}
        now = self.clock()
        lookup = self.lookup if self.lookup is not None else inbox_lookup(self.inbox, now)
        with self.store.read() as db:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            # GET on v1 is empty for the new domain; it never migrates a live file.
            parties = {r['id']: dict(r) for r in db.execute('SELECT * FROM parties')}
            if resource in ('by_conversation', 'identity_lookup'):
                key = params['key']
                owner = db.execute('SELECT party_id FROM identities WHERE conversation_key=?', (key,)).fetchone() if 'identities' in tables else None
                party = {k: parties[owner[0]][k] for k in ('id', 'name', 'kind')} if owner else None
                if resource == 'identity_lookup':
                    return {'party': party} if party else None
                deals = db.execute('SELECT d.id,d.title,d.stage,d.status FROM deals d JOIN deal_conversations c ON c.deal_id=d.id WHERE c.conversation_key=? ORDER BY d.id', (key,)) if 'deals' in tables else ()
                return dict(deals=[dict(r, stage_label=LABELS[r['stage']]) for r in deals], party=party)
            groups = {}
            for table in ('deal_participants', 'deal_conversations', 'deal_items', 'deal_tasks', 'deal_events'):
                grouped = defaultdict(list)
                if table in tables:
                    query = 'SELECT * FROM ' + table
                    args = ()
                    if row_id is not None:
                        query += ' WHERE deal_id=?'
                        args = (row_id,)
                    for r in db.execute(query + ' ORDER BY id', args):
                        grouped[r['deal_id']].append(dict(r))
                groups[table] = grouped
            rows = db.execute('SELECT * FROM deals' + (' WHERE id=?' if row_id is not None else ''),
                              (row_id,) if row_id is not None else ()) if 'deals' in tables else ()
            items = [self._view(dict(r), parties, groups, lookup, now, detail=row_id is not None) for r in rows]
            if row_id is not None and items:
                items[0]['destination_country'] = operations.context(db, row_id)
                items[0]['drafts'] = operations.drafts(db, row_id)
        if row_id is not None:
            if not items:
                raise v.Missing('未找到该案子')
            return items[0]
        if resource == 'summary':
            return dict({s: sum(d['status'] == s for d in items) for s in STATUSES},
                by_stage=[dict(stage=k, label=label, count=sum(d['stage'] == k and d['status'] == 'open' for d in items)) for k, label, _ in STAGES],
                waiting_on_us=sum(d['waiting_on_us'] for d in items), overdue=sum(bool(d['overdue']) for d in items),
                due_tasks=sum(d['due_tasks'] for d in items),
                stale=sum(d['status'] == 'open' and (d['last_activity_at'] is None or now - d['last_activity_at'] >= 7*86400) for d in items))
        filtered = [d for d in items if
            (params.get('status', 'open') == 'all' or d['status'] == params.get('status', 'open'))
            and (not params.get('stage') or d['stage'] == params['stage'])
            and (not params.get('party') or any(p['party_id'] == params['party'] for p in d['participants']))
            and (not params.get('q') or any(params['q'].casefold() in text.casefold() for text in (d['title'], d['customer']['name'] if d['customer'] else '')))
            and (not params.get('waiting') or (d['needs_attention'] if params['waiting'] == 'us' else bool(d['waiting_on'])))]
        filtered.sort(key=lambda d: (-(d['last_activity_at'] or 0), -d['id']))
        return page(filtered, ({'page': 1, 'page_size': 30} | {k: value for k, value in params.items() if k in ('page', 'page_size')}))

    def _view(self, deal, parties, groups, lookup, now, detail):
        did = deal['id']
        tasks, events = groups['deal_tasks'][did], groups['deal_events'][did]
        participants = [dict(p, name=parties[p['party_id']]['name']) for p in groups['deal_participants'][did]]
        state = evaluate(participants, groups['deal_conversations'][did], tasks, lookup, now, self.overdue_hours)
        customer = parties.get(deal['customer_party_id'])
        result = dict(deal, customer={k: customer[k] for k in ('id', 'name')} if customer else None,
                      stage_label=LABELS[deal['stage']], **state)
        value = result.pop('value_fen')
        result['value'] = v.fen_to_money(value) if value is not None else None
        activity = [seconds(e['at']) for e in events] + [c['last_at'] for c in state['conversations']]
        result['last_activity_at'] = max((a for a in activity if a is not None), default=None)
        result['open_tasks'] = sum(t['status'] == 'open' for t in tasks)
        if detail:
            result['items'] = []
            for item in groups['deal_items'][did]:
                item = dict(item)
                value = item.pop('target_price_fen')
                result['items'].append(dict(item, target_price=v.fen_to_money(value) if value is not None else None))
            result['tasks'] = {key: [t for t in tasks if t['stage'] == key] for key, _, _ in STAGES}
            result['events'] = sorted(events, key=lambda e: (e['at'], e['id']), reverse=True)[:100]
            result['stages'] = progress(deal['stage'])
        else:
            result = {k: result[k] for k in ('id', 'title', 'customer', 'stage', 'stage_label', 'status', 'value', 'currency',
                'next_action', 'next_action_due', 'last_activity_at', 'waiting_on_us', 'waiting_on', 'overdue', 'needs_attention', 'open_tasks', 'due_tasks')}
            result['participants'] = [{k: p[k] for k in ('party_id', 'name', 'role', 'state', 'last_at')} for p in state['participants']]
        return result
