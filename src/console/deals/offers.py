"""Read-only supplier comparison and human offer choices in an independent DB."""
from contextlib import closing, contextmanager
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
import unicodedata
from urllib.parse import quote

from console.archive import validation as v
from console.archive.service import get
from console.archive.store import ARCHIVE_PATH, Store, configure, insert, update
from console.archive.validity import current_offer, quote_state
from console.quotes.service import Quotes

_LOCK = threading.RLock()
_CODE = re.compile(r'(?<![A-Za-z0-9])(SUP|CUS)[\s~_\-—–:·、,()（）./]*([0-9]{1,12})(?![0-9])', re.I)
_SPEC = ('wattage', 'product_l_cm', 'product_w_cm', 'product_h_cm', 'description')
_PACKING = ('units_per_carton', 'carton_l_cm', 'carton_w_cm', 'carton_h_cm', 'gross_kg', 'net_kg')
_SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS offer_selections (
       id INTEGER PRIMARY KEY, deal_id INTEGER NOT NULL, deal_item_id INTEGER NOT NULL,
       offer_id INTEGER NOT NULL, product_id INTEGER NOT NULL, supplier_id INTEGER NOT NULL,
       reason TEXT NOT NULL, specs_confirmed INTEGER NOT NULL, selected_at TEXT NOT NULL,
       selected_by TEXT NOT NULL, round_id INTEGER, quote_line_id INTEGER)''',
    'CREATE INDEX IF NOT EXISTS offer_selection_item ON offer_selections(deal_id,deal_item_id,id)',
    '''CREATE TABLE IF NOT EXISTS offer_selection_audit (
       id INTEGER PRIMARY KEY, at TEXT NOT NULL, who TEXT NOT NULL, action TEXT NOT NULL,
       row_id INTEGER NOT NULL, fields TEXT NOT NULL)''',
)


def _safe_path(path):
    if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
        raise v.BadRequest('方案数据库路径不可用')


def _ident(value):
    return v.integer(str(value), maximum=2**63-1)


def _marker(deal_id, item_id, offer_id, round_id, qty, reason, who):
    request = json.dumps([deal_id, item_id, offer_id, round_id, qty, reason, who], ensure_ascii=False)
    return '〔方案选择:' + hashlib.sha256(request.encode('utf8')).hexdigest() + '〕'


class Offers:
    def __init__(self, archive_path=ARCHIVE_PATH, *, path=None, store=None, inbox=None, today=None, clock=None):
        self.store = store or Store(archive_path)
        self.path = Path(path or os.environ.get('IMGW_DEAL_OFFERS_DB',
                                               '/var/lib/imgw/ops-app/deal-offers.sqlite3'))
        self.inbox = inbox
        self.today = today or date.today
        self.clock = clock or time.time
        if (self.path.resolve() == self.store.path.resolve() or
                self.path.exists() and self.store.path.exists() and os.path.samefile(self.path, self.store.path)):
            raise v.BadRequest('方案库须与档案库分开配置')

    def day(self):
        return self.today() if callable(self.today) else self.today

    def _names(self):
        if self.inbox is None:
            return {}
        try:
            return self.inbox.read(self.clock())['conversations']
        except Exception:
            return {}

    @staticmethod
    def _parties(db, conversations):
        identities = [dict(row) for row in db.execute('SELECT * FROM identities ORDER BY id')]
        parties, owners = {}, {}
        for raw in db.execute('SELECT * FROM parties ORDER BY id'):
            party = dict(raw)
            aliases = json.loads(party.get('aliases_json') or '[]')
            sources = [(party.get('name'), 'name', None), (party.get('short_name'), 'short_name', None)]
            sources += [(alias, 'alias', None) for alias in aliases if isinstance(alias, str)]
            links = []
            for identity in identities:
                if identity['party_id'] != party['id']:
                    continue
                key = identity['conversation_key']
                conv = conversations.get(key) or {}
                title = conv.get('buyer_name') or identity.get('label') or '已关联会话'
                links.append(dict(key=key, title=title, channel=identity.get('channel'),
                                  href='/inbox?c=' + quote(key, safe='')))
                sources.extend(((identity.get('label'), 'identity', key), (conv.get('buyer_name'), 'conversation', key)))
            codes, code_sources = set(), []
            for text, source, key in sources:
                for match in _CODE.finditer(unicodedata.normalize('NFKC', text or '')):
                    code = match[1].upper() + match[2]
                    canonical = match[1].upper() + str(int(match[2]))
                    codes.add(code)
                    owners.setdefault(canonical, set()).add(party['id'])
                    code_sources.append(dict(code=code, canonical=canonical, source=source, conversation_key=key))
            parties[party['id']] = dict(id=party['id'], name=party['name'], kind=party['kind'],
                codes=sorted(codes), code_sources=code_sources, conversations=links,
                party_api='/api/v1/archive/parties/' + str(party['id']),
                party_href='/suppliers?party=' + str(party['id']) if party['kind'] == 'supplier' else None,
                warnings=[])
        for party in parties.values():
            if len(party['codes']) > 1:
                party['warnings'].append('这个单位有多个来源编号，请核对各渠道身份；系统不会按编号合并')
            if any(len(owners[source['canonical']]) > 1 for source in party['code_sources']):
                party['warnings'].append('这个编号也出现在其他单位，请核对单位和会话，不能按编号认定是同一家')
            expected = 'CUS' if party['kind'] == 'customer' else 'SUP'
            if any(not code.startswith(expected) for code in party['codes']):
                party['warnings'].append('来源编号与单位类型不一致，请核对原会话和已确认身份')
        return parties

    def _selection_rows(self, deal_id):
        if not self.path.exists() or self.path.stat().st_size == 0:
            return []
        _safe_path(self.path)
        with closing(sqlite3.connect(self.path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute(
                'SELECT * FROM offer_selections WHERE deal_id=? ORDER BY id', (deal_id,))]

    @staticmethod
    def _item(db, deal_id, item_id):
        get(db, 'deals', deal_id)
        item = get(db, 'deal_items', item_id)
        if item['deal_id'] != deal_id:
            raise v.Missing('未找到该案子的产品需求')
        return item

    def _options(self, db, item, parties):
        reference = get(db, 'products', item['product_id']) if item.get('product_id') else None
        all_offers = [dict(row) for row in db.execute('SELECT * FROM offers ORDER BY id')]
        by_product = {}
        for offer in all_offers:
            by_product.setdefault(offer['product_id'], []).append(offer)
        participating = {row[0] for row in db.execute(
            "SELECT party_id FROM deal_participants WHERE deal_id=? AND role='supplier'", (item['deal_id'],))}
        options = []
        for raw in db.execute("SELECT * FROM products WHERE status!='hidden' ORDER BY id"):
            product = dict(raw)
            supplier = parties.get(product['supplier_id'])
            if not supplier or supplier['kind'] != 'supplier' or product['supplier_id'] not in participating:
                continue
            if db.execute('SELECT 1 FROM product_merges WHERE product_id=?', (product['id'],)).fetchone():
                continue
            current = current_offer(by_product.get(product['id'], []))
            if current is None:
                continue
            model_matches = bool(reference and reference.get('model') and product.get('model')
                                 and v.normalize_model(reference['model']) == v.normalize_model(product['model']))
            same_record = product['id'] == item.get('product_id')
            differences = [field for field in _SPEC if reference and reference.get(field) != product.get(field)]
            match = ('same_product' if same_record else 'model_candidate' if model_matches and not differences
                     else 'different_specs' if model_matches else 'unconfirmed')
            state = quote_state(current, self.day())
            sources = [dict(row) for row in db.execute(
                'SELECT kind,ref,title,note FROM product_sources WHERE product_id=? ORDER BY id', (product['id'],))]
            options.append(dict(offer_id=current['id'], product_id=product['id'], supplier_id=product['supplier_id'],
                supplier=supplier, name=product['name'], model=product['model'],
                specification={key: product.get(key) for key in _SPEC}, match=match,
                spec_differences=differences, requires_spec_confirmation=not same_record,
                comparison_notice=('同一档案产品，仍需核对本案要求' if same_record else
                    '型号相同，规格仍须人工核对后才能选用' if match == 'model_candidate' else
                    '规格不同，不能默认当成同款；如需替代请确认并写明理由' if match == 'different_specs' else
                    '尚未确认与本案需求相同，请先核对型号和规格'),
                price=v.fen_to_money(current['price_fen']), currency=current['currency'], unit=current['unit'],
                moq_units=current['moq_units'], lead_days_min=current['lead_days_min'],
                lead_days_max=current['lead_days_max'], price_basis=current['price_basis'], tax_basis=current['tax_basis'],
                packing=dict({key: product.get(key) for key in _PACKING}, includes_flightcase=current['includes_flightcase']),
                quoted_on=current['quoted_on'], valid_until=current['valid_until'], source_ref=current['source_ref'],
                sources=sources, **state))
        return sorted(options, key=lambda option: (option['match'] != 'same_product',
                      option['match'] != 'model_candidate', option['supplier_id'], option['offer_id']))

    def read(self, deal_id, item_id=None):
        deal_id = _ident(deal_id)
        names = self._names()
        selections = self._selection_rows(deal_id)
        with self.store.read() as db:
            deal = get(db, 'deals', deal_id)
            parties = self._parties(db, names)
            participants = [dict(parties[row['party_id']], role=row['role']) for row in db.execute(
                'SELECT party_id,role FROM deal_participants WHERE deal_id=? ORDER BY id', (deal_id,))]
            items = [self._item(db, deal_id, _ident(item_id))] if item_id is not None else [
                dict(row) for row in db.execute('SELECT * FROM deal_items WHERE deal_id=? ORDER BY id', (deal_id,))]
            result = []
            for item in items:
                history = [row for row in selections if row['deal_item_id'] == item['id']]
                result.append(dict(deal_item_id=item['id'], name=item['name'], qty=item['qty'], note=item['note'],
                    reference_product_id=item['product_id'], options=self._options(db, item, parties),
                    selection=history[-1] if history else None, history=history))
            rounds = [dict(row) for row in db.execute(
                "SELECT id,round_no FROM quote_rounds WHERE deal_id=? AND status='draft' ORDER BY round_no", (deal_id,))] if db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='quote_rounds'").fetchone() else []
            return dict(deal_id=deal_id, customer=parties.get(deal['customer_party_id']),
                        participants=participants, items=result, draft_rounds=rounds)

    @contextmanager
    def _write_db(self, writable_archive):
        with _LOCK:
            _safe_path(self.path)
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            try:
                os.fchmod(fd, 0o600)
            finally:
                os.close(fd)
            if self.store.connection is not None or not self.store.path.exists() or self.store.path.is_symlink():
                raise v.BadRequest('请选择已存在的独立档案库文件')
            with closing(sqlite3.connect(self.path, timeout=10, uri=True)) as db:
                configure(db)
                archive_uri = self.store.path.resolve().as_uri() + ('?mode=rw' if writable_archive else '?mode=ro')
                db.execute('ATTACH DATABASE ? AS archive', (archive_uri,))
                db.execute('BEGIN IMMEDIATE')
                try:
                    for statement in _SCHEMA:
                        db.execute(statement)
                    yield db
                    db.commit()
                except BaseException:
                    db.rollback()
                    raise

    def choose(self, deal_id, item_id, fields, who):
        v.whitelist(fields, ('offer_id', 'reason', 'confirm_specs', 'round_id'))
        deal_id, item_id = _ident(deal_id), _ident(item_id)
        offer_id = v.integer(fields.get('offer_id'), maximum=2**63-1)
        reason = v.text(fields.get('reason'), 500, True)
        confirmed = v.choice(fields.get('confirm_specs', '0'), ('0', '1')) == '1'
        round_id = v.integer(fields['round_id'], maximum=2**63-1) if fields.get('round_id') else None
        stamp = datetime.fromtimestamp(self.clock(), timezone.utc).isoformat(timespec='microseconds')
        with self._write_db(round_id is not None) as db:
            item = self._item(db, deal_id, item_id)
            if round_id is not None:
                restored = self._recover(db, deal_id, item, offer_id, round_id, reason, who, confirmed)
                if restored is not None:
                    return restored
            parties = self._parties(db, {})
            option = next((row for row in self._options(db, item, parties) if row['offer_id'] == offer_id), None)
            if option is None:
                raise v.Conflict('这条报价不属于当前案子的供应商方案，请刷新后重新选择')
            if option['requires_spec_confirmation'] and not confirmed:
                raise v.BadRequest('不同档案产品不能默认当成同款，请先人工核对规格并确认可用于这项需求')
            if not option['usable_for_quote']:
                raise v.Conflict('这条报价未生效、已过期或缺少有效期，请先向供应商确认新价')
            old = db.execute('SELECT * FROM offer_selections WHERE deal_id=? AND deal_item_id=? ORDER BY id DESC LIMIT 1',
                             (deal_id, item_id)).fetchone()
            quote_line_id = None
            round_row, existing = None, []
            if round_id is not None:
                if not option['unit']:
                    raise v.BadRequest('这条报价还没写单价单位，请先向供应商确认按台、套还是其他单位计价，再加入报价轮')
                round_row = get(db, 'quote_rounds', round_id)
                if round_row['deal_id'] != deal_id:
                    raise v.Missing('未找到该案子的报价轮')
                if round_row['status'] != 'draft':
                    raise v.Conflict('这一轮已发出，请新建下一轮')
                if item['qty'] is None:
                    raise v.BadRequest('需求数量尚未确认，请先在产品清单填写数量')
                existing = db.execute('SELECT * FROM quote_lines WHERE round_id=? AND deal_item_id=? ORDER BY id',
                                      (round_id, item_id)).fetchall()
                if len(existing) > 1:
                    raise v.Conflict('该需求在报价轮有多条产品行，请先人工核对并整理后再选方案')
            unchanged = round_id is None
            if round_id is not None and existing and old:
                unchanged = (existing[0]['offer_id'] == offer_id and existing[0]['qty'] == item['qty']
                             and existing[0]['id'] == old['quote_line_id'])
            if old and old['offer_id'] == offer_id and old['reason'] == reason and old['round_id'] == round_id and unchanged:
                return dict(ok=True, **dict(old), reused=True)
            recovered = False
            if round_id is not None:
                quotes = Quotes(store=self.store, today=self.day, clock=self.clock)
                marker = _marker(deal_id, item_id, offer_id, round_id, item['qty'], reason, who)
                recorded = db.execute("SELECT * FROM quote_negotiations WHERE round_id=? AND side='supplier' "
                                      'AND instr(note,?)>0 ORDER BY id LIMIT 1', (round_id, marker)).fetchone()
                # ATTACH cannot guarantee a joint crash commit when an existing
                # archive uses WAL. Its own durable receipt lets a later human
                # request repair the sidecar without replaying the quote action.
                matches = bool(existing and existing[0]['offer_id'] == offer_id
                               and existing[0]['product_id'] == option['product_id'] and existing[0]['qty'] == item['qty'])
                if recorded and matches:
                    quote_line_id = existing[0]['id']
                    recovered = True
                else:
                    values = dict(product_id=option['product_id'], offer_id=offer_id, name=item['name'], qty=item['qty'])
                    result = quotes._write(db, 'line_update' if existing else 'line', round_id, values, who, stamp,
                                           self.day(), existing[0]['id'] if existing else None)
                    quote_line_id = result['id']
                    update(db, 'quote_lines', quote_line_id, dict(deal_item_id=item_id), who)
                    if not recorded:
                        insert(db, 'quote_negotiations', dict(round_id=round_id, side='supplier', party_id=option['supplier_id'],
                            note='人工选择产品报价 #' + str(offer_id) + '；理由：' + reason + ' ' + marker,
                            price_minor=None, currency=None, at=stamp, who=who), who)
                        quotes.deals.quote_event(db, deal_id, '报价第 ' + str(round_row['round_no']) + ' 轮选择了产品供应商方案', who, stamp)
                if recorded:
                    stamp = recorded['at']
            if old and old['offer_id'] == offer_id and old['reason'] == reason and old['round_id'] == round_id:
                db.execute('UPDATE offer_selections SET quote_line_id=? WHERE id=?', (quote_line_id, old['id']))
                db.execute('INSERT INTO offer_selection_audit(at,who,action,row_id,fields) VALUES(?,?,?,?,?)',
                    (stamp, who, 'restore', old['id'], json.dumps(['quote_line_id'])))
                return dict(dict(old), ok=True, quote_line_id=quote_line_id, reused=recovered, restored=True)
            cur = db.execute('INSERT INTO offer_selections(deal_id,deal_item_id,offer_id,product_id,supplier_id,reason,'
                'specs_confirmed,selected_at,selected_by,round_id,quote_line_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (deal_id, item_id, offer_id, option['product_id'], option['supplier_id'], reason, int(confirmed),
                 stamp, who, round_id, quote_line_id))
            db.execute('INSERT INTO offer_selection_audit(at,who,action,row_id,fields) VALUES(?,?,?,?,?)',
                (stamp, who, 'choose', cur.lastrowid, json.dumps(sorted(fields))))
            return dict(ok=True, id=cur.lastrowid, deal_id=deal_id, deal_item_id=item_id, offer_id=offer_id,
                        product_id=option['product_id'], supplier_id=option['supplier_id'], reason=reason,
                        round_id=round_id, quote_line_id=quote_line_id, reused=recovered)

    @staticmethod
    def _recover(db, deal_id, item, offer_id, round_id, reason, who, confirmed):
        round_row = get(db, 'quote_rounds', round_id)
        if round_row['deal_id'] != deal_id:
            raise v.Missing('未找到该案子的报价轮')
        marker = _marker(deal_id, item['id'], offer_id, round_id, item['qty'], reason, who)
        receipt = db.execute("SELECT * FROM quote_negotiations WHERE round_id=? AND side='supplier' "
                             'AND instr(note,?)>0 ORDER BY id LIMIT 1', (round_id, marker)).fetchone()
        if receipt is None:
            return None
        lines = db.execute('SELECT * FROM quote_lines WHERE round_id=? AND deal_item_id=?',
                           (round_id, item['id'])).fetchall()
        if len(lines) != 1 or lines[0]['offer_id'] != offer_id or lines[0]['qty'] != item['qty']:
            return None
        line = lines[0]
        offer = get(db, 'offers', offer_id)
        product = get(db, 'products', offer['product_id'])
        if line['product_id'] != product['id'] or receipt['party_id'] != product['supplier_id']:
            return None
        old = db.execute('SELECT * FROM offer_selections WHERE deal_id=? AND deal_item_id=? ORDER BY id DESC LIMIT 1',
                         (deal_id, item['id'])).fetchone()
        if old and old['offer_id'] == offer_id and old['reason'] == reason and old['round_id'] == round_id:
            if old['quote_line_id'] != line['id']:
                db.execute('UPDATE offer_selections SET quote_line_id=? WHERE id=?', (line['id'], old['id']))
                db.execute('INSERT INTO offer_selection_audit(at,who,action,row_id,fields) VALUES(?,?,?,?,?)',
                    (receipt['at'], who, 'restore', old['id'], json.dumps(['quote_line_id'])))
            return dict(dict(old), ok=True, quote_line_id=line['id'], reused=True)
        # This restores a proven earlier choice, even if its frozen quote is
        # now sent or the source offer has expired. It never edits that quote.
        cur = db.execute('INSERT INTO offer_selections(deal_id,deal_item_id,offer_id,product_id,supplier_id,reason,'
            'specs_confirmed,selected_at,selected_by,round_id,quote_line_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
            (deal_id, item['id'], offer_id, product['id'], product['supplier_id'], reason,
             int(confirmed or product['id'] != item.get('product_id')),
             receipt['at'], receipt['who'], round_id, line['id']))
        db.execute('INSERT INTO offer_selection_audit(at,who,action,row_id,fields) VALUES(?,?,?,?,?)',
            (receipt['at'], who, 'restore', cur.lastrowid, json.dumps(['quote_line_id', 'offer_id'])))
        return dict(ok=True, id=cur.lastrowid, deal_id=deal_id, deal_item_id=item['id'], offer_id=offer_id,
                    product_id=product['id'], supplier_id=product['supplier_id'], reason=reason,
                    round_id=round_id, quote_line_id=line['id'], reused=True, restored=True)
