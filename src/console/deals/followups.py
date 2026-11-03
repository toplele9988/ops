"""Human-confirmed waiting items and native-language drafts; never sends messages."""
from contextlib import closing, contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time

from console.archive import validation as v
from console.archive.store import Store as ArchiveStore, ARCHIVE_PATH
from console.inbox import ai
from console.roles.common import duration
from console.roles.storage import run_lock
from .validation import key as conversation_key, identity
from .waiting import evaluate, inbox_lookup, seconds

DEFAULT_RUNNER = object()
LABELS = dict(customer='客户', supplier='产品供应商', forwarder='货代', vendor='配套供应商')
AVAILABILITY = dict(unlinked='尚未关联会话，请先关联具体对象的会话',
                    unreadable='已关联会话暂时无法读取，请到收件箱核对后重新读取',
                    unknown='最新消息方向未知，请核对原会话；已有等待起点继续保留',
                    readable='会话可读取')
FIELDS = dict(create={'party_id', 'conversation_key', 'title', 'waiting_on', 'since', 'request_id', 'item_id'},
              resolve=set(), remind={'at', 'request_id'}, draft={'supplement'})


def default_path(archive_path=ARCHIVE_PATH):
    configured = os.environ.get('IMGW_DEAL_FOLLOWUPS_DB')
    if configured:
        return Path(configured)
    return Path(archive_path).parent / 'deal-followups.sqlite3'


class Store:
    def __init__(self, path):
        self.path, self.connection = Path(path), None

    def _check_path(self):
        if any(part.is_symlink() for part in (self.path, *self.path.parents)) or any(
                Path(str(self.path) + suffix).is_symlink() for suffix in ('-journal', '-wal', '-shm')):
            raise ValueError('等待事项库路径不可用')

    @contextmanager
    def read(self):
        self._check_path()
        if not self.path.exists():
            yield None
            return
        with closing(sqlite3.connect(self.path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA query_only=ON')
            yield db

    @contextmanager
    def write(self):
        with run_lock(self):
            self._check_path()
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
                    db.execute('CREATE TABLE IF NOT EXISTS waiting_items ('
                               'id INTEGER PRIMARY KEY, deal_id INTEGER NOT NULL, party_id INTEGER NOT NULL, '
                               'conversation_key TEXT NOT NULL, title TEXT NOT NULL, waiting_on TEXT NOT NULL, '
                               'since REAL NOT NULL, last_reminded_at REAL, status TEXT NOT NULL, '
                               'request_id TEXT NOT NULL, created_by TEXT NOT NULL, resolved_at REAL, item_id INTEGER, UNIQUE(deal_id,request_id))')
                    if 'item_id' not in {row[1] for row in db.execute('PRAGMA table_info(waiting_items)')}:
                        db.execute('ALTER TABLE waiting_items ADD COLUMN item_id INTEGER')
                    db.execute('CREATE TABLE IF NOT EXISTS reminders ('
                               'id INTEGER PRIMARY KEY, item_id INTEGER NOT NULL, request_id TEXT NOT NULL, '
                               'at REAL NOT NULL, UNIQUE(item_id,request_id))')
                    db.execute('CREATE TABLE IF NOT EXISTS draft_reservations (id INTEGER PRIMARY KEY, at REAL NOT NULL)')
                    db.execute('CREATE TABLE IF NOT EXISTS audit ('
                               'id INTEGER PRIMARY KEY, at REAL NOT NULL, who TEXT NOT NULL, action TEXT NOT NULL, fields TEXT NOT NULL)')
                    yield db
                    db.commit()
                except BaseException:
                    db.rollback()
                    raise

    @staticmethod
    def audit(db, now, who, operation, fields):
        db.execute('INSERT INTO audit(at,who,action,fields) VALUES(?,?,?,?)',
                   (now, who, operation, json.dumps(sorted(fields), ensure_ascii=False)))


def records(path, deal_id=None):
    """Read-only helper for internal roles; absence is an empty independent store."""
    with Store(path).read() as db:
        if db is None:
            return []
        query, args = 'SELECT * FROM waiting_items', ()
        if deal_id is not None:
            query, args = query + ' WHERE deal_id=?', (deal_id,)
        return [dict(row) for row in db.execute(query + ' ORDER BY id', args)]


def view(item, now):
    elapsed, hours = duration(item['since'], now)
    return dict(item, elapsed=elapsed, waited_hours=hours, draft_only=True)


class Followups:
    def __init__(self, path=None, *, store=None, archive_path=None, inbox, lang=None,
                 runner=DEFAULT_RUNNER, clock=None, demands=None):
        self.archive = store or ArchiveStore(archive_path or ARCHIVE_PATH)
        self.store = Store(path if path is not None else default_path(self.archive.path))
        if self.store.path.resolve() == self.archive.path.resolve() or (
                self.store.path.exists() and self.archive.path.exists() and self.store.path.samefile(self.archive.path)):
            raise ValueError('等待事项库必须使用独立文件，不能使用档案库路径')
        self.inbox, self.lang = inbox, lang
        self.demands = demands
        self.runner = ai.official_runner if runner is DEFAULT_RUNNER else runner
        self.clock = clock or time.time

    def _context(self, deal_id):
        if type(deal_id) is not int or deal_id < 1:
            raise v.BadRequest('案子编号无效，请重新打开案子')
        with self.archive.read() as db:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'deals' not in tables or not db.execute('SELECT id FROM deals WHERE id=?', (deal_id,)).fetchone():
                raise v.Missing('未找到该案子')
            participants = [dict(row) for row in db.execute('SELECT d.*,p.name FROM deal_participants d '
                             'JOIN parties p ON p.id=d.party_id WHERE d.deal_id=? ORDER BY d.id', (deal_id,))]
            links = [dict(row) for row in db.execute('SELECT * FROM deal_conversations WHERE deal_id=? ORDER BY id', (deal_id,))]
            products = [dict(row) for row in db.execute('SELECT id,name,qty FROM deal_items WHERE deal_id=? ORDER BY id', (deal_id,))]
        return participants, links, products

    def read(self, deal_id):
        participants, links, products = self._context(deal_id)
        now = self.clock()
        projected = evaluate(participants, links, (), inbox_lookup(self.inbox, now), now)
        for party in projected['participants']:
            party['availability_text'] = AVAILABILITY[party['availability']]
            party['role_label'] = LABELS[party['role']]
            for conv in party['conversations']:
                conv['availability_text'] = AVAILABILITY[conv['availability']]
        parties = {p['party_id']: p for p in participants}
        items = []
        for item in records(self.store.path, deal_id):
            party = parties.get(item['party_id'])
            linked = any(c['conversation_key'] == item['conversation_key'] and c['party_id'] == item['party_id'] for c in links)
            availability = next((c['availability'] for c in projected['conversations'] if c['key'] == item['conversation_key']), 'unlinked') if linked else 'unlinked'
            items.append(dict(view(item, now), party_name=party['name'] if party else '原参与方已移除',
                              role=party['role'] if party else None, availability=availability,
                              availability_text=AVAILABILITY[availability], linked=bool(party and linked)))
        return dict(items=items, participants=projected['participants'], products=products)

    @staticmethod
    def _time(value, now):
        at = seconds(value)
        # Form values may contain Unix seconds or an explicit zoned date.
        if at is None and isinstance(value, str):
            try:
                at = float(value)
            except ValueError:
                pass
        if at is None or not 0 <= at <= now:
            raise v.BadRequest('等待或催问时间须为已经发生的有效时间')
        return at

    def write(self, operation, deal_id, fields, who, child_id=None):
        if operation not in FIELDS or not isinstance(fields, dict):
            raise v.BadRequest('等待事项操作无效')
        v.whitelist(fields, FIELDS[operation])
        if any(not isinstance(value, str) for value in fields.values()):
            raise v.BadRequest('等待事项字段须为文本')
        participants, links, products = self._context(deal_id)
        now = self.clock()
        if operation == 'create':
            party_id = identity(fields.get('party_id', ''))
            key = conversation_key(fields.get('conversation_key', ''))
            if not any(p['party_id'] == party_id for p in participants) or not any(
                    c['party_id'] == party_id and c['conversation_key'] == key for c in links):
                raise v.Conflict('请选择本案已关联到该单位的会话，核对归属后再登记等待事项')
            title = v.text(fields.get('title', ''), 500, True)
            waiting = v.choice(fields.get('waiting_on', 'them'), ('us', 'them'))
            item_id = identity(fields['item_id']) if fields.get('item_id') else None
            if item_id is not None and not any(p['id'] == item_id for p in products):
                raise v.Conflict('请选择本案的产品项，重新读取后再登记等待事项')
            since = self._time(fields['since'], now) if fields.get('since') else now
            request = v.text(fields.get('request_id', ''), 128) or hashlib.sha256(
                json.dumps([party_id, key, title, waiting, fields.get('since'), item_id], ensure_ascii=False).encode()).hexdigest()
            with self.store.write() as db:
                old = db.execute('SELECT * FROM waiting_items WHERE deal_id=? AND request_id=?', (deal_id, request)).fetchone()
                if old:
                    if any(old[field] != value for field, value in dict(party_id=party_id, conversation_key=key,
                            title=title, waiting_on=waiting, item_id=item_id).items()) or (fields.get('since') and old['since'] != since):
                        raise v.Conflict('这次登记标识已用于另一项内容，请重新读取并核对已有事项')
                    return dict(ok=True, id=old['id'], reused=True)
                cur = db.execute('INSERT INTO waiting_items(deal_id,party_id,conversation_key,title,waiting_on,since,status,request_id,created_by,item_id) '
                                 "VALUES(?,?,?,?,?,?,'open',?,?,?)", (deal_id, party_id, key, title, waiting, since, request, who, item_id))
                self.store.audit(db, now, who, operation, fields)
                return dict(ok=True, id=cur.lastrowid, reused=False)
        if type(child_id) is not int or child_id < 1:
            raise v.BadRequest('请选择本案的具体等待事项')
        with run_lock(self.store):
            item = next((i for i in self.read(deal_id)['items'] if i['id'] == child_id), None)
            if item is None:
                raise v.Missing('未找到该案子内的等待事项')
            if operation in ('draft', 'remind') and item['waiting_on'] != 'them':
                raise v.BadRequest('这项等待由我方处理，请先办完或到收件箱回复对方')
            if operation == 'draft':
                if item['status'] != 'open' or not item['linked']:
                    raise v.Conflict('该事项已结束或归属已变，请核对关联和进度后再起草')
                return self._draft(item, products, fields, who)
            with self.store.write() as db:
                if operation == 'resolve':
                    if item['status'] != 'open':
                        return dict(ok=True, id=child_id, reused=True)
                    db.execute("UPDATE waiting_items SET status='done',resolved_at=? WHERE id=?", (now, child_id))
                else:
                    if item['status'] != 'open':
                        raise v.Conflict('该等待事项已结束，请核对进度后再登记催问')
                    at = self._time(fields.get('at'), now) if fields.get('at') else now
                    if at < item['since']:
                        raise v.BadRequest('上次催问时间不能早于原事项的等待起点')
                    request = v.text(fields.get('request_id', ''), 128, True)
                    old = db.execute('SELECT at FROM reminders WHERE item_id=? AND request_id=?', (child_id, request)).fetchone()
                    if old:
                        if fields.get('at') and old['at'] != at:
                            raise v.Conflict('这次催问标识已登记了其他时间，请重新读取后核对')
                        return dict(ok=True, id=child_id, reused=True)
                    db.execute('INSERT INTO reminders(item_id,request_id,at) VALUES(?,?,?)', (child_id, request, at))
                    db.execute('UPDATE waiting_items SET last_reminded_at=max(coalesce(last_reminded_at,?),?) WHERE id=?', (at, at, child_id))
                self.store.audit(db, now, who, operation, fields)
                return dict(ok=True, id=child_id, reused=False)

    def _draft(self, item, products, fields, who):
        if self.lang is None or self.runner is None:
            raise v.BadRequest('催问起草服务尚未配置，请先由管理员接入语言服务和受控起草')
        contact = self.lang.read('contact', dict(conversation_key=item['conversation_key']))['contact']
        if not contact.get('native_language') or (contact.get('source') != 'manual' and contact.get('confidence', 0) < .8):
            raise v.BadRequest('对方母语尚未确认，请先到收件箱识别或手动确认语言')
        supplement = v.text(fields.get('supplement', ''), 1000) or ''
        products = [p for p in products if p['id'] == item.get('item_id')]
        if item.get('item_id') and not products:
            raise v.Conflict('该事项关联的产品项已移除，请重新核对事项后再起草')
        if products and self.demands is not None:
            confirmed = next((candidate for candidate in self.demands.read(item['deal_id'])['items']
                              if candidate['status'] == 'accepted' and candidate.get('accepted_item_id') == item['item_id']), None)
            if confirmed:
                products = [dict(products[0], **{name: confirmed.get(name) for name in
                            ('destination', 'specifications', 'accessories')})]
        prompt = ai.make_followup_prompt(item['role'], item['title'], products, supplement)
        with self.store.write() as db:
            used = db.execute('SELECT count(*) FROM draft_reservations WHERE at>?', (self.clock() - 3600,)).fetchone()[0]
            if used >= 20:
                raise v.BadRequest('本小时催问起草次数已用完，请稍后再试')
            db.execute('DELETE FROM draft_reservations WHERE at<=?', (self.clock() - 3600,))
            db.execute('INSERT INTO draft_reservations(at) VALUES(?)', (self.clock(),))
        try:
            parsed = ai.parse_output(self.runner(prompt))
            from console.lang.protection import facts
            allowed = facts(item['title'] + '\n' + supplement + '\n' + '\n'.join(
                p['name'] + (' ' + str(p['qty']) if p.get('qty') is not None else '') + ' ' + ' '.join(
                    p.get(name) or '' for name in ('destination', 'specifications', 'accessories')) for p in products))
            if facts(parsed['text']) - allowed:
                raise ValueError('草稿增加了未确认的数字、型号或链接')
        except Exception:
            raise v.BadRequest('催问草稿暂时没有生成，请稍后手动再试；没有发送任何消息') from None
        result = self.lang.write('compose', dict(conversation_key=item['conversation_key'], text=parsed['text']), who)
        with self.store.write() as db:
            self.store.audit(db, self.clock(), who, 'draft', ('item_id', 'native_language', 'validation'))
        return dict(result, draft_only=True, item_id=item['id'], source_text=parsed['text'],
                    needs_confirmation=parsed['needs_confirmation'], message='这里只生成草稿，请人工核对后自行发送；生成草稿不会登记已催问')
