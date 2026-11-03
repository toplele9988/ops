"""Customer inquiry candidates; only an explicit owner decision creates a deal item."""
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time

from console.archive import validation as v
from console.archive.service import get
from console.archive.store import ARCHIVE_PATH, Store, configure, insert
from console.channels import shared_inbox
from console.roles.common import fingerprint, message_ref, safe
from console.roles.storage import run_lock
from .service import Deals
from .validation import key as conversation_key

DEFAULT_PATH = '/var/lib/imgw/ops-app/deal-demands.sqlite3'
SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS candidates (
    id INTEGER PRIMARY KEY, deal_id INTEGER NOT NULL, party_id INTEGER NOT NULL,
    conversation_key TEXT NOT NULL, fingerprint TEXT NOT NULL,
    source_hash TEXT NOT NULL, source_json TEXT NOT NULL, values_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','accepted','dismissed')),
    accepted_item_id INTEGER, created_at TEXT NOT NULL, decided_at TEXT, decided_by TEXT,
    UNIQUE(deal_id,fingerprint))''',
    '''CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY, at TEXT NOT NULL,
    who TEXT NOT NULL, action TEXT NOT NULL, fields TEXT NOT NULL)''',
)
FIELDS = ('name', 'qty', 'destination', 'specifications', 'accessories')
PRODUCT = re.compile(r'灯|音箱|扬声器|控制台|屏幕|激光|moving\s*head|\bbeam\b|\bspot\b|\bwash\b|'
                     r'\bl[áa]mparas?\b|\bfocos?\b|\bluminarias?\b|\bLED\b|'
                     r'\b(?=[A-Za-z0-9_-]*[A-Za-z])(?=[A-Za-z0-9_-]*[0-9])[A-Za-z0-9]+[-_][A-Za-z0-9_-]+\b', re.I)
AMBIGUOUS = re.compile(r'大约|左右|或|不确定|可能|\b(?:aproximadamente|quiz[aá]s|tal vez|o)\b|'
                       r'\d\s*[-–~至到]\s*\d', re.I)
QUANTITY = re.compile(r'(?<![0-9A-Za-z.,])([0-9]{1,7})\s*(台|只|套|个|件|盏|unidades?|piezas?|uds?\.?|pcs?)', re.I)


def _text_hash(text):
    return hashlib.sha256(text.encode('utf8')).hexdigest()


def _check_path(path):
    if (any(p.is_symlink() for p in (path.absolute(), *path.absolute().parents))
            or any(Path(str(path) + suffix).is_symlink() for suffix in ('-journal', '-wal', '-shm'))
            or (path.exists() and not path.is_file())):
        raise v.BadRequest('需求候选或档案数据库路径不可用')


def _field_values(fields):
    v.whitelist(fields, FIELDS)
    values = {}
    for name, value in fields.items():
        if name == 'qty':
            values[name] = v.integer(value) if value else None
        else:
            values[name] = v.text(value, {'name': 200, 'destination': 300,
                                        'specifications': 500, 'accessories': 500}[name])
    return values


def _label(text, names):
    match = re.search(r'(?:' + names + r')\s*[:：]\s*([^;；\n]+)', text, re.I)
    return match.group(1).strip(' 。.')[:500] if match else None


def _destination(text):
    value = _label(text, r'目的地|目的国|destino|destinaci[oó]n')
    if not value:
        match = re.search(r'(?:送到|发往|寄到|运到|enviar\s+a|env[ií]o\s+a)\s*([^,，;；。\n]+)', text, re.I)
        value = match.group(1).strip() if match else None
    return value if value and not AMBIGUOUS.search(value) and not re.search(r'待定|未知|不知|por confirmar', value, re.I) else None


def _specifications(text):
    explicit = _label(text, r'规格|電壓|电压|especificaciones?|voltaje')
    tokens = re.findall(r'\b\d{2,4}\s*[VvWw]\b|\bIP\d{2}\b|\bDMX(?:512)?\b|\bRDM\b', text, re.I)
    return '；'.join(dict.fromkeys(([explicit] if explicit else []) + tokens))[:500] or None


def _accessories(text):
    explicit = _label(text, r'配套(?:要求)?|配件|accesorios?')
    tokens = re.findall(r'航空箱|灯钩|安全绳|信号线|电源线|flight\s*cases?|cables?|ganchos?|abrazaderas?', text, re.I)
    return '；'.join(dict.fromkeys(([explicit] if explicit else []) + tokens))[:500] or None


def candidates(text):
    """Keep literal evidence; ambiguous quantities and destinations remain empty."""
    destination = _destination(text)
    fragments = re.split(r'[\n;；]+|(?:以及|和)\s*(?=\d+\s*(?:台|只|套|个|件|盏))|\by\s+(?=\d+\s+(?:unidades|piezas|uds))', text, flags=re.I)
    result = []
    product_fragments = [p for p in fragments if PRODUCT.search(p)]
    for fragment in product_fragments[:20]:
        # A labelled destination/specification alone is not a second product.
        if re.match(r'\s*(?:目的地|目的国|规格|电压|配套|配件|destino|voltaje|accesorios)\s*[:：]', fragment, re.I):
            continue
        first = re.split(r'[,，。]|\s+(?:para|con)\s+', fragment, maxsplit=1, flags=re.I)[0]
        quantity = list(QUANTITY.finditer(first))
        qty = int(quantity[0].group(1)) if len(quantity) == 1 and not AMBIGUOUS.search(first) else None
        if qty is not None and not 1 <= qty <= 1_000_000:
            qty = None
        name = re.sub(r'^\s*(?:产品(?:需求)?|producto)\s*[:：]\s*', '', first, flags=re.I)
        name = re.sub(r'^\s*(?:我们?|我司)?(?:想要|需要|询价|采购|请报(?:价)?|求购)\s*', '', name)
        name = re.sub(r'^\s*(?:necesit(?:o|amos)|quiero|queremos|cotizar|busco)\s+', '', name, flags=re.I)
        if quantity:
            name = QUANTITY.sub('', name, count=1)
        name = re.sub(r'^\s*(?:de\s+|一些|若干|一批)', '', name, flags=re.I).strip(' ：:.-')
        if not name or not PRODUCT.search(name):
            continue
        context = text if len(product_fragments) == 1 else fragment
        result.append(dict(name=name[:200], qty=qty, destination=destination,
                           specifications=_specifications(context), accessories=_accessories(context)))
    return result


class DemandStore:
    def __init__(self, path):
        self.path, self.connection = Path(path), None

    @contextmanager
    def read(self):
        if not self.path.exists():
            yield None
            return
        self.check()
        with closing(sqlite3.connect(self.path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA query_only=ON')
            yield db

    def check(self):
        _check_path(self.path)

    @contextmanager
    def write(self):
        with run_lock(self):
            self.check()
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            try:
                os.fchmod(fd, 0o600)
            finally:
                os.close(fd)
            with closing(sqlite3.connect(self.path, timeout=10)) as db:
                db.row_factory = sqlite3.Row
                db.execute('BEGIN IMMEDIATE')
                try:
                    for statement in SCHEMA:
                        db.execute(statement)
                    yield db
                    db.commit()
                except BaseException:
                    db.rollback()
                    raise

    @staticmethod
    def audit(db, now, who, action, fields):
        db.execute('INSERT INTO audit(at,who,action,fields) VALUES(?,?,?,?)',
                   (now, who, action, json.dumps(sorted(fields), ensure_ascii=False)))


class Demands:
    def __init__(self, archive_path=ARCHIVE_PATH, *, path=None, store=None, inbox=None, clock=None):
        self.archive = store or Store(archive_path)
        self.store = DemandStore(path or os.environ.get('IMGW_DEAL_DEMANDS_DB', DEFAULT_PATH))
        if self.archive.path.resolve() == self.store.path.resolve() or (
                self.archive.path.exists() and self.store.path.exists() and self.archive.path.samefile(self.store.path)):
            raise v.BadRequest('需求候选数据库须与档案库分开')
        self.inbox = inbox if inbox is not None else shared_inbox()
        self.clock = clock or time.time

    def _stamp(self):
        return datetime.fromtimestamp(self.clock(), timezone.utc).isoformat(timespec='microseconds')

    @staticmethod
    def _owner(db, deal_id, key):
        deal = get(db, 'deals', deal_id)
        row = db.execute('SELECT c.party_id,p.kind,i.party_id AS identity_party FROM deal_conversations c '
                         'JOIN identities i ON i.conversation_key=c.conversation_key '
                         'JOIN parties p ON p.id=c.party_id WHERE c.deal_id=? AND c.conversation_key=?',
                         (deal_id, key)).fetchone()
        if (not row or row['kind'] != 'customer' or row['identity_party'] != row['party_id']
                or row['party_id'] != deal['customer_party_id']):
            raise v.Conflict('请先把原询盘会话关联到本案客户，并核对往来单位归属')
        return row['party_id']

    @staticmethod
    def _view(row):
        result = {name: row[name] for name in ('id', 'deal_id', 'party_id', 'conversation_key', 'status',
                  'accepted_item_id', 'created_at', 'decided_at', 'decided_by')}
        result.update(json.loads(row['values_json']))
        result['source'] = json.loads(row['source_json'])
        result['source_message_refs'] = [{k: value for k, value in result['source'].items() if k != 'text'}]
        return result

    def read(self, deal_id):
        with self.archive.read() as db:
            deal = get(db, 'deals', deal_id)
            conversations = [dict(key=r['conversation_key'], title=r['label'] or '客户原询盘会话')
                             for r in db.execute('SELECT c.conversation_key,i.label FROM deal_conversations c '
                             'JOIN identities i ON i.conversation_key=c.conversation_key '
                             'WHERE c.deal_id=? AND c.party_id=? AND i.party_id=c.party_id ORDER BY c.id',
                             (deal_id, deal['customer_party_id']))]
        with self.store.read() as db:
            items = [self._view(r) for r in db.execute('SELECT * FROM candidates WHERE deal_id=? ORDER BY id DESC',
                     (deal_id,))] if db else []
        return dict(items=items, total=len(items), conversations=conversations)

    def _conversation(self, key):
        try:
            conv = self.inbox.detail(key, self.clock())
        except Exception:
            conv = None
        if not conv or conv.get('key') != key or not conv.get('messages'):
            raise v.BadRequest('原询盘会话暂时读不到或没有文字，请先回收件箱核对，再重新提取')
        return conv

    def scan(self, deal_id, fields, who):
        v.whitelist(fields, ('conversation_key',))
        with self.archive.read() as db:
            deal = get(db, 'deals', deal_id)
            key = conversation_key(fields.get('conversation_key') or deal['source_conversation_key'])
            party_id = self._owner(db, deal_id, key)
        conv = self._conversation(key)
        prepared, skipped = [], 0
        for message in list(conv['messages'])[-100:]:
            raw = message.get('text')
            if message.get('role') != 'buyer' or not isinstance(raw, str) or not raw.strip():
                continue
            if len(raw) > 4000:
                skipped += 1
                continue
            text = safe(raw, 4000)
            ref = message_ref(key, message)
            for index, values in enumerate(candidates(text)):
                fp = fingerprint(['deal-demand-v1', deal_id, key, ref['id'], _text_hash(raw), index])
                source = dict(ref, message_id=ref['id'], text=text)
                prepared.append((fp, _text_hash(raw), json.dumps(source, ensure_ascii=False),
                                 json.dumps(values, ensure_ascii=False)))
        now, added = self._stamp(), 0
        with self.store.write() as db:
            for fp, source_hash, source, values in prepared:
                added += db.execute('INSERT OR IGNORE INTO candidates(deal_id,party_id,conversation_key,fingerprint,'
                    'source_hash,source_json,values_json,created_at) VALUES(?,?,?,?,?,?,?,?)',
                    (deal_id, party_id, key, fp, source_hash, source, values, now)).rowcount
            self.store.audit(db, now, who, 'scan', ('conversation_key', 'candidates'))
        return dict(ok=True, added=added, skipped=skipped, items=self.read(deal_id)['items'])

    def _candidate(self, deal_id, candidate_id):
        with self.store.read() as db:
            row = db.execute('SELECT * FROM candidates WHERE id=? AND deal_id=?',
                             (candidate_id, deal_id)).fetchone() if db else None
            if row is None:
                raise v.Missing('未找到本案的需求候选')
            return dict(row)

    def accept(self, deal_id, candidate_id, fields, who):
        overrides = _field_values(fields)
        candidate = self._candidate(deal_id, candidate_id)
        if candidate['status'] == 'dismissed':
            raise v.Conflict('该需求候选已忽略，请重新核对原文')
        if candidate['status'] == 'accepted':
            with self.archive.read() as db:
                exists = db.execute('SELECT id FROM deal_items WHERE id=? AND deal_id=?',
                                    (candidate['accepted_item_id'], deal_id)).fetchone()
            if not exists:
                raise v.Conflict('该需求的确认结果需要核对，请回案子产品清单查看，勿重复确认')
            return dict(ok=True, id=candidate_id, accepted_item_id=candidate['accepted_item_id'], reused=True)
        conv = self._conversation(candidate['conversation_key'])
        if not any(m.get('role') == 'buyer' and isinstance(m.get('text'), str)
                   and _text_hash(m['text']) == candidate['source_hash']
                   and message_ref(candidate['conversation_key'], m)['id'] == json.loads(candidate['source_json'])['id']
                   for m in conv['messages']):
            raise v.Conflict('候选原文已变化或暂时读不到，请重新提取并核对')
        values = dict(json.loads(candidate['values_json']), **overrides)
        values['name'] = v.text(values.get('name') or '', 200, True)
        now = self._stamp()
        # Both decisions and the existing deal item use one connection/transaction.
        # The deterministic note marker also recovers a partial cross-WAL commit
        # after a process/power failure without ever creating a second item.
        with run_lock(self.store):
            self.store.check()
            _check_path(self.archive.path)
            if not self.archive.path.is_file():
                raise v.BadRequest('案子档案库暂时不可写，请稍后核对')
            with closing(sqlite3.connect(self.archive.path.resolve().as_uri() + '?mode=rw', uri=True, timeout=10)) as db:
                configure(db)
                db.execute('ATTACH DATABASE ? AS demand_state', (str(self.store.path.resolve()),))
                db.execute('BEGIN IMMEDIATE')
                try:
                    party_id = self._owner(db, deal_id, candidate['conversation_key'])
                    if party_id != candidate['party_id']:
                        raise v.Conflict('原询盘会话的单位归属已变化，请重新提取并核对')
                    current = db.execute('SELECT * FROM demand_state.candidates WHERE id=? AND deal_id=?',
                                         (candidate_id, deal_id)).fetchone()
                    if current['status'] == 'dismissed':
                        raise v.Conflict('该需求候选已忽略，请重新核对原文')
                    if current['status'] == 'accepted':
                        db.rollback()
                        return dict(ok=True, id=candidate_id, accepted_item_id=current['accepted_item_id'], reused=True)
                    marker = '需求候选：' + candidate['fingerprint']
                    old = db.execute('SELECT id FROM deal_items WHERE deal_id=? AND instr(COALESCE(note,\'\'),?)>0',
                                     (deal_id, marker)).fetchone()
                    reused = bool(old)
                    if old:
                        item_id = old['id']
                    else:
                        note = '\n'.join([marker] + [label + '：' + values[name] for name, label in
                            (('destination', '目的地'), ('specifications', '规格'), ('accessories', '配套要求')) if values.get(name)])
                        item_id = insert(db, 'deal_items', dict(deal_id=deal_id, name=values['name'],
                                         qty=values.get('qty'), note=note), who)
                        Deals._event(db, deal_id, 'item', '老板确认询盘需求，添加产品项', who, now)
                    db.execute("UPDATE demand_state.candidates SET status='accepted',accepted_item_id=?,values_json=?,"
                               'decided_at=?,decided_by=? WHERE id=? AND deal_id=?',
                               (item_id, json.dumps(values, ensure_ascii=False), now, who, candidate_id, deal_id))
                    db.execute('INSERT INTO demand_state.audit(at,who,action,fields) VALUES(?,?,?,?)',
                               (now, who, 'accept', json.dumps(sorted(overrides), ensure_ascii=False)))
                    db.commit()
                    return dict(ok=True, id=candidate_id, accepted_item_id=item_id, reused=reused)
                except BaseException:
                    db.rollback()
                    raise

    def dismiss(self, deal_id, candidate_id, who):
        with self.archive.read() as db:
            get(db, 'deals', deal_id)
        now = self._stamp()
        with self.store.write() as db:
            row = db.execute('SELECT * FROM candidates WHERE id=? AND deal_id=?', (candidate_id, deal_id)).fetchone()
            if row is None:
                raise v.Missing('未找到本案的需求候选')
            if row['status'] == 'accepted':
                raise v.Conflict('该需求已确认进入案子，不能再忽略')
            if row['status'] != 'dismissed':
                db.execute("UPDATE candidates SET status='dismissed',decided_at=?,decided_by=? WHERE id=?", (now, who, candidate_id))
                self.store.audit(db, now, who, 'dismiss', ('status',))
        return dict(ok=True, id=candidate_id)
