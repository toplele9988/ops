"""Single atomic boundary for human-confirmed fulfilment operations.

No channel, outbox, transport, AI runner or shipping tracker is constructed.
The only stage transition is inside an explicit payment confirmation request.
"""
from datetime import datetime, timezone
import sqlite3
import time
from zoneinfo import ZoneInfo

from console.archive import validation as v
from console.archive.store import ARCHIVE_PATH, Store
from console.deals.service import Deals
from console.deals import operations
from .common import iso_day, record
from . import issues, nodes, payments, reorder


class Fulfil:
    def __init__(self, path=ARCHIVE_PATH, *, store=None, deals=None, clock=None, today=None):
        self.store = Store(store.path, connection=store.connection, target_version=8) if store else Store(path, target_version=8)
        self.clock = clock or time.time
        self.today = today or (lambda: datetime.fromtimestamp(self.clock(), ZoneInfo('Asia/Shanghai')).date())
        self.deals = deals or Deals(store=self.store, lookup=lambda key: None, clock=self.clock)

    def day(self):
        return iso_day(self.today())

    def write(self, operation, row_id, fields, who, child_id=None):
        fields, operation_key = operations.split(fields)
        stamp = datetime.fromtimestamp(self.clock(), timezone.utc).isoformat(timespec='microseconds')
        try:
            with self.store.write() as db:
                return operations.once(db, f'fulfil:{who}:{operation}:{row_id}:{child_id}', operation_key,
                    fields, lambda: self._dispatch(db, operation, row_id, fields, who, stamp, child_id))
        except sqlite3.IntegrityError:
            raise v.Conflict('记录重复或关联、字段约束不满足') from None

    def _dispatch(self, db, operation, row_id, fields, who, stamp, child_id):
        if operation in ('payment_create', 'payment_confirm', 'payment_void'):
            fn = {'payment_create': payments.create, 'payment_confirm': payments.confirm, 'payment_void': payments.void}[operation]
            return fn(db, row_id, fields, who, stamp, self.deals)
        if operation in ('node_update', 'node_update_id'):
            if operation == 'node_update_id':
                node = record(db, 'fulfilment_nodes', row_id)
                row_id, child_id = node['deal_id'], node['node']
            return nodes.write(db, row_id, child_id, fields, who, stamp, self.deals, self.day())
        fn = {'issue_create': issues.create, 'issue_update': issues.change, 'issue_note': issues.note, 'reorder': reorder.copy}.get(operation)
        if fn is None:
            raise v.BadRequest('操作无效')
        return fn(db, row_id, fields, who, stamp, self.deals)

    def read(self, resource, params=None, row_id=None):
        params = params or {}
        with self.store.read() as db:
            if resource in ('payments', 'fulfilment', 'issues'):
                v.whitelist(params, ())
                deal = record(db, 'deals', row_id)
                if resource == 'payments':
                    return payments.list_payments(db, row_id)
                if resource == 'issues':
                    return issues.listing(db, row_id)
                return nodes.progress(db, deal, self.day())
            if resource == 'board':
                v.whitelist(params, ())
                return nodes.board(db, self.day())
            if resource == 'issue_summary':
                return issues.summary(db, params)
            if resource == 'issue_draft':
                return issues.draft(db, row_id, params)
            if resource == 'reorder_leads':
                return reorder.leads(db, params, self.day())
            raise v.BadRequest('查询无效')
