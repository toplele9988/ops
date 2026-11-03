"""Order hints use existing deal writes; receiving money always needs a human."""
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re
import sqlite3

from console.archive import validation as v
from console.archive.service import get
from console.archive.store import ARCHIVE_PATH, _LOCK as ARCHIVE_LOCK, configure
from console.deals.service import Deals
from console.deals.validation import fields
from .store import _LOCK, check_path, file_lock

# Only currencies represented by the existing two-decimal payment model are accepted.
CURRENCIES = frozenset(('USD', 'CNY', 'EUR', 'GBP', 'AUD', 'CAD', 'HKD', 'SGD', 'NZD',
                       'CHF', 'BRL', 'MXN', 'ZAR', 'AED', 'SAR', 'THB', 'TRY', 'PLN'))
NEXT_ACTION = '阿里订单显示已付款，请先核对订单归属和实际到账，再在案子里确认收款'


def _identifier(value, label):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_.:@-]{1,128}', value):
        raise v.BadRequest(label + '格式无效，请核对官方接口字段')
    return value


def _milliseconds(value, *, optional=False):
    if optional and value is None:
        return None
    if type(value) is not int or not 0 < value <= 253402300799000:
        raise v.BadRequest('订单时间无效，请核对官方接口字段')
    return value


def marker(order_id, *, claimed=False):
    return ('〔阿里订单认领:' if claimed else '〔阿里订单候选:') + hashlib.sha256(order_id.encode('utf8')).hexdigest() + '〕'


class Candidates:
    def __init__(self, store, archive_path=ARCHIVE_PATH, *, deals=None):
        self.store = store
        self.archive_path = Path(archive_path).absolute()
        self.deals = deals or Deals(self.archive_path, lookup=lambda key: None, clock=store.clock)
        if self.archive_path == self.store.path or (self.archive_path.exists() and self.store.path.exists()
                                                   and self.archive_path.samefile(self.store.path)):
            raise v.BadRequest('阿里订单候选须保存到独立数据库')

    @contextmanager
    def _write(self):
        # Initialize only our DB. The archive is never migrated or created by this module.
        check_path(self.archive_path, private=False)
        if not self.archive_path.exists():
            raise v.BadRequest('案子档案尚未准备好，请管理员先准备案子库，再读取订单')
        with self.store.write():
            pass
        with _LOCK, ARCHIVE_LOCK, file_lock(Path(str(self.store.path) + '.lock')):
            self.store._check()
            check_path(self.archive_path, private=False)
            with closing(sqlite3.connect(self.archive_path.as_uri() + '?mode=rw', uri=True, timeout=10)) as db:
                configure(db)
                tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if not {'deals', 'deal_events', 'deal_tasks', 'identities', 'parties', 'audit'} <= tables:
                    raise v.BadRequest('案子档案结构尚未准备好，请管理员先核对已有案子模块')
                db.execute('ATTACH DATABASE ? AS alibaba_state', (str(self.store.path),))
                db.execute('BEGIN IMMEDIATE')
                try:
                    yield db
                    db.commit()
                except BaseException:
                    db.rollback()
                    raise

    def _stamp(self):
        return datetime.fromtimestamp(self.store.clock(), timezone.utc).isoformat(timespec='microseconds')

    def _audit(self, db, who, operation='candidate'):
        db.execute('INSERT INTO alibaba_state.audit(at,who,operation,result) VALUES(?,?,?,?)',
                   (self.store.clock(), who, operation, 'ok'))

    @staticmethod
    def _reference(db, order_id, deal_id):
        latest = db.execute(
            "SELECT d.id FROM deals d JOIN deal_events e ON e.deal_id=d.id "
            "WHERE e.kind='system' AND e.summary=? ORDER BY e.id DESC LIMIT 1",
            (marker(order_id, claimed=True),)).fetchone()
        if latest:
            return 'claimed' if latest[0] == deal_id else None
        rows = {r[0] for r in db.execute(
            "SELECT e.summary FROM deals d JOIN deal_events e ON e.deal_id=d.id "
            "WHERE d.id=? AND e.kind='system' AND e.summary IN (?,?)",
            (deal_id, marker(order_id), marker(order_id, claimed=True)))}
        if marker(order_id, claimed=True) in rows:
            return 'claimed'
        return 'unclaimed' if marker(order_id) in rows else None

    def _recover(self, db, order_id, who, stamp, payment_status='paid'):
        claim = db.execute("SELECT d.id FROM deals d JOIN deal_events e ON e.deal_id=d.id "
                           "WHERE e.kind='system' AND e.summary=? ORDER BY e.id DESC LIMIT 1",
                           (marker(order_id, claimed=True),)).fetchone()
        if claim:
            return claim['id'], False, True
        source = marker(order_id)
        old = db.execute("SELECT d.id FROM deals d JOIN deal_events e ON e.deal_id=d.id "
                         "WHERE e.kind='system' AND e.summary=? ORDER BY e.id LIMIT 1", (source,)).fetchone()
        if old:
            return old['id'], False, False
        values = fields('create', dict(title='阿里订单待认领', stage='awaiting_payment'))
        deal_id = self.deals._write(db, 'create', None, values, who, stamp, None)['id']
        next_action = NEXT_ACTION if payment_status == 'paid' else '阿里订单付款状态已变化，请核对订单归属和原收款记录'
        self.deals._write(db, 'update', deal_id, dict(next_action=next_action), who, stamp, None)
        self.deals._event(db, deal_id, 'system', source, who, stamp)
        return deal_id, True, False

    def upsert(self, order, who):
        order_id = _identifier(order.get('order_id'), '订单号')
        payment_status = v.choice(order.get('payment_status'), ('paid', 'unpaid', 'unknown'))
        modified_at_ms = _milliseconds(order.get('modified_at_ms'))
        if payment_status != 'paid':
            with self.store.read() as db:
                exists = db.execute('SELECT 1 FROM order_candidates WHERE order_id=?', (order_id,)).fetchone()
            if not exists:
                return dict(ok=True, order_id=order_id, skipped=True, created=False, changed=False, deal_id=None)
            with self._write() as db:
                old = db.execute('SELECT * FROM alibaba_state.order_candidates WHERE order_id=?', (order_id,)).fetchone()
                stamp = self._stamp()
                claim_state = self._reference(db, order_id, old['deal_id'])
                deal_id, created = old['deal_id'], False
                if not claim_state or claim_state != old['claim_state']:
                    deal_id, created, claimed = self._recover(db, order_id, who, stamp, payment_status)
                    claim_state = 'claimed' if claimed else 'unclaimed'
                changed = (deal_id != old['deal_id'] or claim_state != old['claim_state'] or
                           modified_at_ms >= old['modified_at_ms'] and payment_status != old['payment_status'])
                if modified_at_ms >= old['modified_at_ms']:
                    db.execute('UPDATE alibaba_state.order_candidates SET payment_status=?,modified_at_ms=? WHERE order_id=?',
                               (payment_status, modified_at_ms, order_id))
                if changed:
                    db.execute('UPDATE alibaba_state.order_candidates SET deal_id=?,claim_state=?,updated_at=? WHERE order_id=?',
                               (deal_id, claim_state, self.store.clock(), order_id))
                    self.deals._event(db, deal_id, 'system',
                        '阿里订单付款状态已变化，请核对原收款记录；系统没有改动已登记款项', who, stamp)
                    self._audit(db, who)
                return dict(ok=True, order_id=order_id, deal_id=deal_id, created=created, changed=changed)
        amount_fen = v.money_to_fen(order.get('amount'))
        if amount_fen <= 0:
            raise v.BadRequest('订单金额须大于零，请核对官方接口字段')
        if order.get('currency') not in CURRENCIES:
            raise v.BadRequest('订单币种不在已核准的两位小数币种范围，请人工核对后再接入')
        buyer_id = _identifier(order.get('buyer_id'), '买家标识')
        paid_at_ms = _milliseconds(order.get('paid_at_ms'), optional=True)
        with self._write() as db:
            stamp = self._stamp()
            old = db.execute('SELECT * FROM alibaba_state.order_candidates WHERE order_id=?', (order_id,)).fetchone()
            claim_state = self._reference(db, order_id, old['deal_id']) if old else None
            if old and modified_at_ms < old['modified_at_ms'] and claim_state == old['claim_state']:
                return dict(ok=True, order_id=order_id, deal_id=old['deal_id'], created=False, changed=False)
            deal_id = old['deal_id'] if old else None
            created = False
            if not claim_state or old and claim_state != old['claim_state']:
                deal_id, created, claimed = self._recover(db, order_id, who, stamp,
                    old['payment_status'] if old and modified_at_ms < old['modified_at_ms'] else payment_status)
                claim_state = 'claimed' if claimed else 'unclaimed'
            values = dict(order_id=order_id, deal_id=deal_id,
                          claim_state=claim_state, amount_fen=amount_fen,
                          currency=order['currency'], buyer_id=buyer_id, payment_status=payment_status,
                          paid_at_ms=paid_at_ms, modified_at_ms=modified_at_ms, updated_at=self.store.clock())
            if old and modified_at_ms < old['modified_at_ms']:
                values = dict(old, deal_id=deal_id, claim_state=claim_state, updated_at=self.store.clock())
            changed = not old or any(old[k] != value for k, value in values.items() if k != 'updated_at')
            if changed:
                columns = ','.join(values)
                db.execute('INSERT OR REPLACE INTO alibaba_state.order_candidates (' + columns + ') VALUES (' +
                           ','.join('?' for _ in values) + ')', tuple(values.values()))
                if old:
                    self.deals._event(db, deal_id, 'system',
                        '阿里订单候选已更新，请核对订单和实际到账；系统没有确认收款', who, stamp)
                self._audit(db, who)
            return dict(ok=True, order_id=order_id, deal_id=deal_id, created=created, changed=changed)

    def list(self):
        with self.store.read() as db:
            rows = [dict(r) for r in db.execute('SELECT * FROM order_candidates ORDER BY updated_at DESC,order_id LIMIT 200')]
        return dict(items=[dict(r, claimed=r['claim_state'] == 'claimed', amount=v.fen_to_money(r['amount_fen'])) for r in rows])

    def needs_recovery(self, order_id):
        with self.store.read() as db:
            row = db.execute('SELECT deal_id,claim_state FROM order_candidates WHERE order_id=?', (order_id,)).fetchone()
        if not row:
            return False
        check_path(self.archive_path, private=False)
        if not self.archive_path.exists():
            return True
        with closing(sqlite3.connect(self.archive_path.as_uri() + '?mode=ro', uri=True)) as db:
            db.execute('PRAGMA query_only=ON')
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            return (not {'deals', 'deal_events'} <= tables or
                    self._reference(db, order_id, row['deal_id']) != row['claim_state'])

    def claim(self, order_id, deal_id, who):
        order_id = _identifier(order_id, '订单号')
        deal_id = v.integer(str(deal_id), maximum=2**63-1)
        with self._write() as db:
            old = db.execute('SELECT * FROM alibaba_state.order_candidates WHERE order_id=?', (order_id,)).fetchone()
            if not old:
                raise v.Missing('未找到这笔订单候选，请先读取订单')
            get(db, 'deals', deal_id)
            if (old['deal_id'] == deal_id and old['claim_state'] == 'claimed' and
                    self._reference(db, order_id, deal_id) == 'claimed'):
                return dict(ok=True, order_id=order_id, deal_id=deal_id)
            db.execute("UPDATE alibaba_state.order_candidates SET deal_id=?,claim_state='claimed',updated_at=? WHERE order_id=?",
                       (deal_id, self.store.clock(), order_id))
            stamp = self._stamp()
            self.deals._event(db, deal_id, 'system',
                '已人工关联阿里订单候选，请核对订单和实际到账，再确认收款', who, stamp)
            self.deals._event(db, deal_id, 'system', marker(order_id, claimed=True), who, stamp)
            self._audit(db, who, 'claim')
            return dict(ok=True, order_id=order_id, deal_id=deal_id)
