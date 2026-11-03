"""Discover locally, suggest once, and write the directory only after review."""
from contextlib import contextmanager
from datetime import datetime
import json
import math
from pathlib import Path
import time
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from console.archive.service import Archive
from console.archive.store import ARCHIVE_PATH
from console.archive.validation import BadRequest, Conflict, Missing, party_name
from console.inbox.model import decode_key, make_key
from .rules import BUSINESS, IGNORED, LABELS, suggest
from .store import Store
from .policy import Policy

ZONE = ZoneInfo('Asia/Shanghai')


def day_at(stamp):
    return datetime.fromtimestamp(stamp, ZONE).date().isoformat()


def valid_day(value):
    try:
        if datetime.strptime(value, '%Y-%m-%d').date().isoformat() != value:
            raise ValueError
    except (TypeError, ValueError):
        raise BadRequest('日期须为年-月-日，请重新选择日期') from None
    return value


class DirectoryTransaction:
    """Borrow a caller-owned writer transaction for the existing public service."""
    def __init__(self, db):
        self.db = db

    @contextmanager
    def write(self):
        yield self.db


class Classifier:
    def __init__(self, *, inbox, directory=None, path=None, archive_path=ARCHIVE_PATH,
                 lang=None, lang_factory=None, clock=None):
        self.inbox, self.directory, self.lang = inbox, directory, lang
        self.lang_factory = lang_factory
        self.store, self.archive_path = Store(path), Path(archive_path)
        self.clock = clock or time.time
        # A misconfigured output must never become an input/source database.
        protected = [self.archive_path]
        if directory is not None:
            protected += [getattr(directory, name, None) for name in
                          ('discovery_path', 'state_path', 'cache_path', 'config_path')]
        if any(p is not None and Path(p).resolve() == self.store.path.resolve() for p in protected):
            raise ValueError('分类库须独立于档案、通讯录及渠道来源文件')

    def scan(self, who):
        now = self.clock()
        snapshot = self.inbox.read(now)
        candidates = dict(snapshot['conversations'])
        notes = []
        if not snapshot.get('ready'):
            notes.append('会话来源暂不可读，本次只处理可读的通讯录记录')
        if self.directory is not None:
            page = 1
            while True:
                result = self.directory.list(dict(kind='all', page=page, page_size=100))
                notes = list(dict.fromkeys(notes + result.get('notes', [])))
                for row in result['items']:
                    if row['kind'] == 'official':
                        continue
                    key = make_key('conversation', 'wechat', '微信', row['id'])
                    conv = candidates.get(key, {})
                    candidates[key] = dict(conv, key=key, channel='wechat',
                        buyer_name=row['name'], kind=row['kind'],
                        first_seen=row.get('first_seen') or conv.get('first_seen'), messages=conv.get('messages', ()))
                if page * 100 >= result['total']:
                    break
                page += 1
        # P1 validates keys through its inbox. Give its public reader this scan's
        # local snapshot so language hints do not fetch per-chat remote history.
        language_reader = self.lang
        if language_reader is None and self.lang_factory is not None:
            language_reader = self.lang_factory(SimpleNamespace(detail=lambda key: candidates.get(key)))
        added, updated = 0, 0
        with self.store.write() as db:
            known = {r['key']: r for r in db.execute('SELECT * FROM suggestions')}
            for key, conv in candidates.items():
                previous = known.get(key)
                if previous is not None and previous['status'] != 'pending':
                    continue  # A scan never overwrites a human decision.
                name = party_name(conv.get('buyer_name') or '未命名联系人')
                messages = tuple(conv.get('messages', ()))[:20]
                member_roles = []
                for label in dict.fromkeys(m.get('sender_label') for m in messages if m.get('sender_label')):
                    role = suggest(label, 'wechat')['suggested_role']
                    if role in BUSINESS:
                        member_roles.append(role)
                native = None
                if language_reader is not None and conv.get('messages'):
                    # Read the public P1 service only; never create another language table.
                    try:
                        native = language_reader.read('contact', {'conversation_key': key})['contact'].get('native_language')
                    except Exception:
                        pass  # Language is optional evidence; no source exception or text is logged.
                advice = suggest(name, conv['channel'], messages, member_roles, native)
                first = conv.get('first_seen')
                first = first if type(first) in (int, float) and math.isfinite(first) and 0 < first <= now else None
                if previous is not None and previous['first_seen'] is not None:
                    first = min(first, previous['first_seen']) if first is not None else previous['first_seen']
                if first is None:
                    advice['reasons'].append('来源没有首次发现时间，日期按本次首次扫描计算')
                reasons_json = json.dumps(advice['reasons'], ensure_ascii=False)
                day = day_at(first or (previous['discovered_at'] if previous is not None else now))
                if previous is not None:
                    fields = ('name', 'first_seen', 'day', 'suggested_role', 'confidence', 'reasons_json', 'conflict')
                    values = (name, first, day, advice['suggested_role'], advice['confidence'], reasons_json, int(advice['conflict']))
                    if tuple(previous[f] for f in fields) == values:
                        continue
                    revision = previous['revision'] + 1
                    db.execute('''UPDATE suggestions SET name=?,first_seen=?,day=?,suggested_role=?,confidence=?,
                        reasons_json=?,conflict=?,revision=?,updated_at=? WHERE key=?''', (*values, revision, now, key))
                    db.execute('INSERT INTO classification_events(at,who,key,action,revision) VALUES(?,?,?,?,?)',
                               (now, who, key, 'refresh', revision))
                    updated += 1
                    continue
                db.execute('''INSERT INTO suggestions(key,channel,name,kind,first_seen,discovered_at,day,
                    suggested_role,confidence,reasons_json,conflict,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (key, conv['channel'], name, conv.get('kind') or ('group' if conv.get('is_group') else 'private'),
                     first, now, day, advice['suggested_role'], advice['confidence'], reasons_json, int(advice['conflict']), now))
                db.execute('INSERT INTO classification_events(at,who,key,action,revision) VALUES(?,?,?,?,0)',
                           (now, who, key, 'discover'))
                added += 1
        return dict(added=added, updated=updated, generated_at=now, notes=notes,
                    message=f'本次新增 {added} 条建议，更新 {updated} 条待确认线索；人工确认结果已保留')

    def list(self, day=None, status='pending'):
        day = day if day == 'all' else valid_day(day or day_at(self.clock()))
        if status not in ('pending', 'confirmed', 'ignored', 'all'):
            raise BadRequest('分类状态无效，请重新选择')
        with self.store.read() as db:
            rows = list(db.execute('SELECT * FROM suggestions' + ('' if day == 'all' else ' WHERE day=?') +
                                   ' ORDER BY confidence DESC,key', () if day == 'all' else (day,))) if db else []
        items = []
        for row in rows:
            item = dict(row)
            if status != 'all' and item['status'] != status:
                continue
            item['reasons'] = json.loads(item.pop('reasons_json'))
            item['label'] = LABELS[item['suggested_role']]
            item['conflict'] = bool(item['conflict'])
            item.update(Policy.flags_for(item['decision_role']))
            items.append(item)
        return dict(items=items, total=len(items), day=day, timezone='Asia/Shanghai', ai_enabled=False)

    def summary(self, day=None):
        result = self.list(day, 'all')
        rows = result.pop('items')
        return dict(result, pending=sum(r['status'] == 'pending' for r in rows),
                    confirmed=sum(r['status'] == 'confirmed' for r in rows),
                    ignored=sum(r['status'] == 'ignored' for r in rows),
                    conflicts=sum(r['conflict'] for r in rows),
                    by_role={role: sum(r['suggested_role'] == role for r in rows) for role in LABELS},
                    unknown_first_seen=sum(r['first_seen'] is None for r in rows))

    def decide(self, items, action, who):
        if action not in ('confirm', 'ignore', 'undo'):
            raise BadRequest('分类操作无效，请刷新后重试')
        if not isinstance(items, list) or not 1 <= len(items) <= 20:
            raise BadRequest('每次请选择 1 至 20 条建议')
        keys = set()
        for item in items:
            if not isinstance(item, dict) or set(item) - {'key', 'revision', 'role', 'name'}:
                raise BadRequest('分类表单包含不支持的字段')
            try:
                decode_key(item.get('key'), 'conversation')
            except ValueError:
                raise BadRequest('会话标识无效，请刷新列表后重试') from None
            if item['key'] in keys or type(item.get('revision')) is not int or item['revision'] < 0:
                raise BadRequest('建议重复或版本无效，请刷新列表后重试')
            keys.add(item['key'])
            if action == 'confirm' and (not isinstance(item.get('role'), str) or item['role'] not in LABELS.keys() - {'unclassified'}):
                raise BadRequest('请选择分类后再确认')
            if 'name' in item:
                party_name(item['name'])
        # Read first so ignores and undo do not require or attach the business archive.
        business = action == 'confirm' and any(i['role'] in BUSINESS for i in items)
        with self.store.write(self.archive_path if business else None) as db:
            archive = Archive(store=DirectoryTransaction(db)) if business else None
            for item in items:
                row = db.execute('SELECT * FROM suggestions WHERE key=?', (item['key'],)).fetchone()
                if row is None:
                    raise Missing('未找到待分类建议，请先扫描本地记录')
                role = item['role'] if action == 'confirm' else 'ignore' if action == 'ignore' else None
                status = 'pending' if action == 'undo' else 'ignored' if role in IGNORED else 'confirmed'
                if (row['status'] == status and row['decision_role'] == role and
                        (item.get('name', row['name']) == row['name'])):
                    continue  # Identical confirmation/undo is idempotent, even after a lost response.
                if row['revision'] != item['revision']:
                    raise Conflict('这条建议已被修改，请刷新列表后重新核对')
                name = party_name(item.get('name', row['name']))
                party_id = None
                if role in BUSINESS:
                    found = db.execute('SELECT * FROM directory.parties WHERE kind=? AND name=?', (role, name)).fetchone()
                    if found:
                        if found['conversation_ref'] != item['key'] or found['status'] != 'active':
                            raise Conflict('已有同名档案，请先在名录核对，再填写能区分的名称')
                        party_id = found['id']
                    else:
                        party_id = archive.write('party_create', None,
                            dict(kind=role, name=name, conversation_ref=item['key']), who)['id']
                revision = row['revision'] + 1
                db.execute('''UPDATE suggestions SET name=?,status=?,decision_role=?,revision=?,party_id=?,updated_at=?
                    WHERE key=?''', (name, status, role, revision, party_id, self.clock(), item['key']))
                db.execute('INSERT INTO classification_events(at,who,key,action,revision) VALUES(?,?,?,?,?)',
                           (self.clock(), who, item['key'], action, revision))
        return dict(ok=True, message='分类已保存；撤销会保留已有名录档案，请到名录核对不再使用的单位' if action == 'undo'
                    else '已保存；忽略和内部会话不再计入待回复，也不能生成回复草稿')
