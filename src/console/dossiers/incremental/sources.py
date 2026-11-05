"""Read-only rowid discovery and reuse of already parsed attachment text."""
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
import sqlite3

from .schema import dumps

WECHAT_PATH = Path('/var/lib/imgw/wechat/api_state.sqlite3')
LOCAL_TZ = timezone(timedelta(hours=8))
SUCCESS = {'ready', 'ok', 'success', 'completed'}


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def identity(prefix, value):
    return prefix + digest(dumps(value))[:32]


@contextmanager
def readonly(path):
    # Same mode=ro/query_only/WAL contract as operator.sources.readonly;
    # intentionally independent from that package.
    path = Path(path).resolve(strict=True)
    wal = Path(str(path) + '-wal')
    live = wal.exists() and wal.stat().st_size > 0
    if live and not Path(str(path) + '-shm').exists():
        raise ValueError('source_shm_unavailable')
    db = sqlite3.connect(path.as_uri() + '?mode=ro' + ('' if live else '&immutable=1'), uri=True, timeout=3)
    try:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('PRAGMA temp_store=MEMORY')
        db.execute('BEGIN')
        yield db
    finally:
        db.close()


def timestamp(value):
    try:
        return datetime.fromtimestamp(float(value), timezone.utc).isoformat(timespec='seconds')
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def supplier_bindings(db):
    groups, private, names = {}, {}, {}
    def bind(mapping, key, sid):
        mapping.setdefault(key, set()).add(sid)
    for row in db.execute('SELECT supplier_id,name,aliases_json,contacts_json FROM sd_supplier'):
        sid = row['supplier_id']
        for name in [row['name']] + json.loads(row['aliases_json']):
            if isinstance(name, str):
                bind(names, name, sid)
        for c in json.loads(row['contacts_json']):
            if c.get('channel') == 'wechat_group' and c.get('group_id'):
                bind(groups, c['group_id'], sid)
            if c.get('channel') == 'wechat_private' and all(c.get(k) for k in ('app_id', 'own_wxid', 'peer_id')):
                bind(private, (c['app_id'], c['own_wxid'], c['peer_id']), sid)
    return groups, private, names


def source_row(db, *, kind, key, content_hash, text, at, now, direction, parent=None, media=None):
    external = dumps(key)
    # First-batch JSON used whitespace; compare parsed keys as well.
    existing = db.execute('SELECT source_id,external_key FROM sd_source WHERE kind=? AND content_sha256=?',
                          (kind, content_hash)).fetchall()
    sid = None
    for old in existing:
        try:
            if json.loads(old['external_key']) == key:
                sid = old['source_id']
                external = old['external_key']
                break
        except ValueError:
            continue
    media = media or {}
    coverage = dict(name=media.get('name'), format=media.get('format'), status=media.get('status'),
                    truncated=bool(media.get('truncated')), text_chars=len(text),
                    no_text=not bool(text.strip()), denominator=1)
    return dict(source_id=sid or identity('IS', [kind, key, content_hash]), kind=kind,
                external_key=external, content_sha256=content_hash,
                file_sha256=media.get('archive_sha256') or media.get('sha256'), parent_source_id=parent,
                locator='text' if kind == 'message' else 'attachment:text', source_at=at, observed_at=now,
                scope='supplier', direction=direction, parse_state='read' if text.strip() else 'no_text',
                extract_state='observed' if direction == 'incoming' and text.strip() else 'no_text' if not text.strip() else 'ignored_outgoing',
                failure_reason=None, coverage_json=dumps(coverage), private_ref=None, text=text)


def scan(db, path, *, after, now, expected=None):
    """Take a short read snapshot; close it before invoking any model.

    Cursor anchor prevents rowid resets or database replacement from silently
    skipping data. Such a change requires an explicit reconciliation.
    """
    groups, private, names = supplier_bindings(db)
    records, skips = [], 0
    with readonly(path) as wx:
        tables = {r[0] for r in wx.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        high = wx.execute('SELECT coalesce(max(rowid),0) FROM messages').fetchone()[0]
        stat = Path(path).stat()
        generation = [stat.st_dev, stat.st_ino]
        def anchor(position):
            r = wx.execute('SELECT app_id,message_id FROM messages WHERE rowid=?', (position,)).fetchone()
            return digest(dumps(list(r))) if r else None
        if expected and (expected.get('generation') != generation or after > high or
                         (after and expected.get('anchor') != anchor(after))):
            raise ValueError('source_cursor_reconciliation_required')
        watermark = dict(rowid=high, generation=generation, anchor=anchor(high))
        if after is None:
            return dict(watermark=watermark, records=[], skipped=0)
        incoming = {r['intake_rowid']: r for r in wx.execute('SELECT rowid AS intake_rowid,* FROM messages WHERE rowid>? ORDER BY rowid', (after,))}
        # Parsing can complete after the message row was first received. Reobserve
        # only this worker's recent messages, never the first-batch history.
        cutoff = (datetime.fromisoformat(now) - timedelta(days=7)).isoformat(timespec='seconds')
        recent = db.execute("SELECT s.external_key FROM sd_source s JOIN sd_projection p ON p.table_name='sd_source' AND p.row_key=json_array(s.source_id) WHERE s.kind='message' AND p.created_at>=?", (cutoff,)).fetchall()
        for old in recent:
            key = json.loads(old['external_key'])
            found = wx.execute('SELECT rowid AS intake_rowid,* FROM messages WHERE app_id=? AND message_id=?', key).fetchone()
            if found:
                incoming[found['intake_rowid']] = found
        for _, raw in sorted(incoming.items()):
            row = dict(raw)
            app, mid, group = row['app_id'], row['message_id'], row['group_id']
            sids, candidate = groups.get(group, set()), None
            direction = 'incoming'
            try:
                payload = json.loads(row['payload'])
                if not isinstance(payload, dict):
                    raise ValueError
            except (ValueError, TypeError):
                payload = {}
            if group and group.endswith('@chatroom'):
                if payload.get('own_wxid') and row.get('sender_id') == payload['own_wxid']:
                    direction = 'outgoing'
            elif {'wechat_private_messages', 'wechat_private_contacts'} <= tables:
                p = wx.execute('''SELECT p.*,c.display_name FROM wechat_private_messages p
                    JOIN wechat_private_contacts c ON c.app_id=p.app_id AND c.own_wxid=p.own_wxid AND c.peer_id=p.peer_id
                    WHERE p.app_id=? AND p.message_id=? AND c.business_role='supplier' ''', (app, mid)).fetchone()
                if p and payload.get('own_wxid') == p['own_wxid'] and group == p['peer_id']:
                    binding = (app, p['own_wxid'], p['peer_id'])
                    # A name is not an identity: never auto-merge a private account by display name.
                    sids = private.get(binding, set())
                    if not sids:
                        sid = identity('IP', binding)
                        sids = {sid}
                        candidate = dict(supplier_id=sid, name=p['display_name'] or '供应商私聊（待核）',
                            supplier_type='产品', categories_json='[]',
                            contacts_json=dumps([dict(channel='wechat_private', app_id=app, own_wxid=p['own_wxid'], peer_id=p['peer_id'])]),
                            reliability_notes='待核：新增供应商私聊，未按显示名合并身份', aliases_json='[]', field_sources_json='{}')
                    direction = 'outgoing' if p['direction'] == 'outbound' else 'incoming'
            if not sids:
                skips += 1
                continue
            at = timestamp(row['created_at'])
            day = datetime.fromisoformat(at).astimezone(LOCAL_TZ).date().isoformat() if at else 'unknown'
            text = payload.get('text', '')
            text = text if isinstance(text, str) else ''
            parent = source_row(db, kind='message', key=[app, mid], content_hash=digest(row['payload']),
                                text=text, at=at, now=now, direction=direction)
            sources, ordinals = [parent], set()
            for table, kind in [('company_media', 'message_attachment'), ('company_note_assets', 'note_asset')]:
                if table not in tables:
                    continue
                # Never select original/blob columns.
                columns = {r[1] for r in wx.execute('PRAGMA table_info(' + table + ')')}
                wanted = ['ordinal', 'parent_ordinal', 'name', 'status', 'text', 'format', 'truncated', 'sha256', 'archive_sha256']
                cols = [c for c in wanted if c in columns]
                for media_row in wx.execute('SELECT ' + ','.join(cols) + ' FROM ' + table + ' WHERE app_id=? AND message_id=? ORDER BY ordinal', (app, mid)):
                    media = dict(media_row)
                    if table == 'company_media':
                        ordinals.add(media['ordinal'])
                    cached = media.get('text')
                    cached = cached if media.get('status') in SUCCESS and isinstance(cached, str) else ''
                    key = [app, mid, media.get('parent_ordinal'), media['ordinal']]
                    h = digest(dumps([media.get('status'), cached, media.get('archive_sha256') or media.get('sha256'), bool(media.get('truncated'))]))
                    sources.append(source_row(db, kind=kind, key=key, content_hash=h, text=cached, at=at, now=now,
                                              direction=direction, parent=parent['source_id'], media=media))
            attachments = payload.get('attachments')
            for ordinal, item in enumerate(attachments if isinstance(attachments, list) else []):
                if ordinal in ordinals:
                    continue
                media = dict(name=item.get('name'), format=item.get('format'), status='not_parsed') if isinstance(item, dict) else dict(status='not_parsed')
                sources.append(source_row(db, kind='message_attachment', key=[app, mid, None, ordinal],
                    content_hash=digest(dumps(item)), text='', at=at, now=now, direction=direction,
                    parent=parent['source_id'], media=media))
            records.append(dict(rowid=row['intake_rowid'], supplier_ids=sorted(sids), candidate=candidate, day=day, sources=sources))
    return dict(watermark=watermark, records=records, skipped=skips)


def windows(records):
    """Supplier/day windows bounded by UTF-8 bytes, with overlap for long tables."""
    grouped = {}
    for record in records:
        for sid in record['supplier_ids']:
            grouped.setdefault((sid, record['day']), []).append(record)
    output = []
    for (sid, day), rows in sorted(grouped.items()):
        parts, rowids, candidates, size = [], set(), [], 0
        def flush():
            if parts:
                output.append(dict(supplier_id=sid, day=day, rowids=sorted(rowids), sources=list(parts),
                    parents=[r['sources'][0] for r in rows if r['rowid'] in rowids],
                    candidates=list(candidates), merge_conflict=any(len(r['supplier_ids']) > 1 for r in rows)))
        for record in rows:
            for source in record['sources']:
                text = source['text']
                # 1600 Unicode characters fit comfortably into the 32KiB stdin envelope.
                chunks = [text[i:i + 1600] for i in range(0, len(text), 1400)] if len(text) > 1600 else [text]
                for chunk in chunks:
                    part = dict(source, text=chunk)
                    part_size = len(dumps(part).encode())
                    if size + part_size > 16000 and parts:
                        flush()
                        parts, rowids, candidates, size = [], set(), [], 0
                    parts.append(part)
                    size += part_size
                    rowids.add(record['rowid'])
                    if record['candidate'] and record['candidate'] not in candidates:
                        candidates.append(record['candidate'])
        flush()
    return output
