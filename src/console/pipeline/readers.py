"""Bounded SQLite aggregates. No source body, identity or raw error leaves SQL."""
from contextlib import closing, contextmanager
from pathlib import Path
import math
import re
import sqlite3
import time


class MissingSchema(ValueError):
    pass


@contextmanager
def read_db(path, seconds=1):
    path = Path(path).resolve()
    if not path.is_file():
        raise FileNotFoundError
    wal, shm, journal = (Path(str(path) + suffix) for suffix in ('-wal', '-shm', '-journal'))
    has_wal = wal.exists() and wal.stat().st_size > 0
    if has_wal and not shm.exists():
        raise OSError('读取快照尚未就绪')
    if journal.exists() and journal.stat().st_size:
        raise OSError('来源正在写入，请稍后刷新')
    before = path.stat()
    uri = path.as_uri() + '?mode=ro' + ('' if has_wal else '&immutable=1')
    deadline = time.monotonic() + seconds
    with closing(sqlite3.connect(uri, uri=True, timeout=.02)) as db:
        db.execute('PRAGMA query_only=ON')
        db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
        db.execute('BEGIN')
        yield db
    after = path.stat()
    if not has_wal and ((wal.exists() and wal.stat().st_size > 0)
            or (journal.exists() and journal.stat().st_size > 0)
            or (before.st_mtime_ns, before.st_size, before.st_ino) !=
               (after.st_mtime_ns, after.st_size, after.st_ino)):
        raise OSError('来源快照已变化')


def require(db, table, columns):
    # Identifiers come only from the fixed readers below, never HTTP/config.
    actual = {row[1] for row in db.execute('PRAGMA table_info("' + table + '")')}
    if not set(columns) <= actual:
        raise MissingSchema


def aggregate(db, rows, now):
    start = int(now // 3600) * 3600 - 47 * 3600
    groups = db.execute('''WITH source AS (''' + rows + '''), valid AS (
        SELECT CASE WHEN typeof(at) IN ('integer','real') AND at>0 AND at<=:now
                    THEN at END AS at,kind FROM source
        ) SELECT CASE WHEN at IS NULL THEN -2 WHEN at<:start THEN -1
                      ELSE CAST((at-:start)/3600 AS INTEGER) END AS bucket,
          kind,COUNT(*),COALESCE(SUM(at>=:hour),0),COALESCE(SUM(at>=:day),0),MAX(at)
        FROM valid GROUP BY bucket,kind''',
        dict(now=now, start=start, hour=now - 3600, day=now - 86400)).fetchall()
    result = dict(total=0, last_seen_at=None, count_1h=0, count_24h=0, invalid_timestamps=0,
                  hourly=[dict(start_at=start + i * 3600, count=0) for i in range(48)],
                  kinds={key: dict(count_24h=0, last_seen_at=None, total=0) for key in ('live', 'history', 'unknown')})
    for bucket, kind, count, hour, day, last in groups:
        result['total'] += count
        if bucket == -2:
            result['invalid_timestamps'] += count
            continue
        result['count_1h'] += hour
        result['count_24h'] += day
        result['last_seen_at'] = max(result['last_seen_at'] or 0, last)
        if bucket >= 0:
            result['hourly'][bucket]['count'] += count
        if kind in result['kinds']:
            item = result['kinds'][kind]
            item['total'] += count
            item['count_24h'] += day
            item['last_seen_at'] = max(item['last_seen_at'] or 0, last)
    return result


def inbound(db, now):
    require(db, 'inbound', ('created_at', 'payload', 'archive_reason'))
    return aggregate(db, '''SELECT created_at AS at,
        CASE WHEN archive_reason='history_import' THEN 'history'
             WHEN json_valid(payload) THEN CASE json_type(payload,'$.history')
                  WHEN 'true' THEN 'history'
                  WHEN 'false' THEN CASE WHEN json_type(payload,'$.live_provenance')='object' THEN 'live' ELSE 'unknown' END
                  ELSE 'unknown' END
             ELSE 'unknown' END AS kind FROM inbound''', now)


HEARTBEAT_SLOT = 120
HEARTBEAT_TOLERANCE = 30
HEARTBEAT_WINDOW = 86400


def heartbeat_continuity(db, now, maintenance=()):
    """Occupied 120s slots over the trailing 24h (or since the first logged beat); None without a log.

    Each slot counts at most once, so duplicate or bursty arrivals cannot fill empty slots. Empty slots
    inside a registered maintenance window are deducted from the denominator instead of counted missing.
    """
    try:
        require(db, 'heartbeat_log', ('received_at',))
    except MissingSchema:
        return None
    first = db.execute("SELECT MIN(received_at) FROM heartbeat_log "
                       "WHERE typeof(received_at) IN ('integer','real') AND received_at>0 AND received_at<=?",
                       (now,)).fetchone()[0]
    if first is None:
        return dict(window_seconds=0, slots=0, present=0, rate=None, max_consecutive_missing=0, maintenance_slots=0)
    start = max(now - HEARTBEAT_WINDOW, first)
    total = max(1, math.ceil((now - start) / HEARTBEAT_SLOT))
    # At most total+1 aggregate rows leave SQL, even if a sender floods the log.
    occupied = {min(row[0], total - 1) for row in db.execute(
        "SELECT CAST((received_at-?+?)/? AS INTEGER) FROM heartbeat_log "
        "WHERE typeof(received_at) IN ('integer','real') AND received_at>=? AND received_at<=? GROUP BY 1",
        (start, HEARTBEAT_SLOT / 2, HEARTBEAT_SLOT, start, now))}
    missed = worst = run = deducted = 0
    for slot in range(total):
        if slot in occupied:
            run = 0
            continue
        centre = start + slot * HEARTBEAT_SLOT
        if any(low <= centre < high for low, high in maintenance):
            deducted += 1
            run = 0
        else:
            missed += 1
            run += 1
            worst = max(worst, run)
    present = len(occupied)
    slots = present + missed
    return dict(window_seconds=now - start, slots=slots, present=present,
                rate=present / slots if slots else None, max_consecutive_missing=worst, maintenance_slots=deducted)


def probe(db, now):
    require(db, 'probe_state', ('received_at', 'snapshot_json'))
    last, total, invalid = db.execute('''SELECT MAX(CASE WHEN typeof(received_at) IN ('integer','real')
        AND received_at>0 AND received_at<=? THEN received_at END),COUNT(*),
        COALESCE(SUM(CASE WHEN typeof(received_at) IN ('integer','real') AND received_at>0
        AND received_at<=? THEN 0 ELSE 1 END),0) FROM probe_state''', (now, now)).fetchone()
    row = db.execute('''SELECT CASE WHEN json_valid(snapshot_json) THEN json_extract(snapshot_json,'$.version') END,
        CASE WHEN json_valid(snapshot_json) THEN CASE json_extract(snapshot_json,'$.state')
        WHEN 'starting' THEN 'starting' WHEN 'running' THEN 'running' WHEN 'idle' THEN 'idle'
        WHEN 'paused' THEN 'paused' END END,
        CASE WHEN json_valid(snapshot_json) THEN json_extract(snapshot_json,'$.last_error') END
        FROM probe_state WHERE received_at=? LIMIT 1''', (last,)).fetchone()
    version = row[0] if row and isinstance(row[0], str) and re.fullmatch(r'\d{1,3}(?:\.\d{1,3}){1,5}', row[0]) else None
    # The error is a lowercase snake_case code by the receiver contract; anything else is ignored.
    error = row[2] if row and isinstance(row[2], str) and re.fullmatch(r'[a-z][a-z0-9_]{0,95}', row[2]) else None
    return dict(total=total, last_seen_at=last, count_1h=None, count_24h=None, hourly=None,
                invalid_timestamps=invalid, script_version=version, probe_state=row[1] if row else None,
                probe_error=error, continuity=heartbeat_continuity(db, now))


def table_counts(db, table, column, now):
    require(db, table, (column,))
    return aggregate(db, 'SELECT "' + column + '" AS at,\'unknown\' AS kind FROM "' + table + '"', now)


def whatsapp_messages(db, now):
    result = table_counts(db, 'messages', 'received', now)
    try:
        require(db, 'recipients', ('chat_id',))
        result['no_contacts'] = db.execute('SELECT NOT EXISTS(SELECT 1 FROM recipients)').fetchone()[0] == 1
    except MissingSchema:
        result['no_contacts'] = None
    return result


def wechat_messages(db, now):
    result = table_counts(db, 'messages', 'created_at', now)
    result['pending_ready'] = False
    try:
        require(db, 'messages', ('status', 'updated_at'))
        count, oldest, stalled, invalid = db.execute('''WITH pending AS (
            SELECT CASE WHEN typeof(created_at) IN ('integer','real') AND created_at>0
                AND created_at<=:now THEN created_at END AS created,
                CASE WHEN typeof(updated_at) IN ('integer','real') AND updated_at>0
                AND updated_at<=:now THEN updated_at END AS updated
            FROM messages WHERE status='pending')
            SELECT COUNT(*),MIN(created),MIN(COALESCE(updated,created)),
                COALESCE(SUM(COALESCE(updated,created) IS NULL),0) FROM pending''', dict(now=now)).fetchone()
        result.update(pending_ready=True, pending_count=count, pending_oldest_at=oldest,
                      pending_oldest_age_seconds=now - oldest if oldest is not None else None,
                      pending_stalled_age_seconds=now - stalled if stalled is not None else None,
                      pending_invalid_timestamps=invalid)
    except MissingSchema:
        pass
    return result


def whatsapp_session(db, now, limit=50000):
    require(db, 'events', ('event_type', 'received', 'raw_json'))
    # Only status and the presence of me leave SQL; never return the JSON or identity.
    rows = db.execute('''SELECT received,
        CASE WHEN json_valid(raw_json) THEN CASE json_extract(raw_json,'$.payload.status')
            WHEN 'WORKING' THEN 'WORKING' WHEN 'FAILED' THEN 'FAILED'
            WHEN 'STARTING' THEN 'STARTING' WHEN 'STOPPED' THEN 'STOPPED'
            WHEN 'SCAN_QR_CODE' THEN 'SCAN_QR_CODE' WHEN 'PASSKEY_REQUIRED' THEN 'PASSKEY_REQUIRED'
            WHEN 'PASSKEY_CONFIRMATION_REQUIRED' THEN 'PASSKEY_CONFIRMATION_REQUIRED' END END,
        CASE WHEN json_valid(raw_json) THEN CASE
            WHEN json_extract(raw_json,'$.payload.source')='poll' THEN NULL
            WHEN json_type(raw_json,'$.me')='object' THEN EXISTS(SELECT 1 FROM json_each(raw_json,'$.me'))
            WHEN json_type(raw_json,'$.me') IN ('null') OR json_type(raw_json,'$.me') IS NULL THEN 0
            END END,
        CASE WHEN json_valid(raw_json) AND json_extract(raw_json,'$.payload.source')='poll'
            THEN 'poll' ELSE 'event' END
        FROM (SELECT received,raw_json FROM events WHERE event_type='session.status'
            AND typeof(received) IN ('integer','real') AND received>0 AND received<=?
            ORDER BY received DESC,rowid DESC LIMIT ?)''', (now, limit + 1)).fetchall()
    truncated = len(rows) > limit
    rows = rows[:limit]
    result = dict(total=len(rows), last_seen_at=rows[0][0] if rows else None,
                  count_1h=None, count_24h=None, hourly=None, invalid_timestamps=0,
                  session_status=rows[0][1] if rows else None,
                  status_source=rows[0][3] if rows else None,
                  has_me=bool(rows[0][2]) if rows and rows[0][2] is not None else None,
                  last_working_at=next((at for at, state, _, source in rows if state == 'WORKING'), None),
                  non_working_since=None, sample_limit=limit, sampled_events=len(rows),
                  sample_truncated=truncated, restart_storm=None)

    def complete(seconds):
        return not truncated or rows[-1][0] < now - seconds

    for name, seconds in (('1h', 3600), ('24h', 86400)):
        count = sum(state == 'FAILED' and source == 'event' and at >= now - seconds for at, state, _, source in rows)
        valid = not any(state is None and at >= now - seconds for at, state, _, source in rows)
        result['failed_count_' + name] = count if rows and complete(seconds) and valid else None
        result['failed_count_' + name + '_lower_bound'] = count
    if rows and complete(600) and not any(state is None and at >= now - 600 for at, state, _, source in rows):
        result['restart_storm'] = (sum(state == 'FAILED' and source == 'event' and at >= now - 600 for at, state, _, source in rows) >= 20
                                   and not any(state == 'WORKING' and source == 'event' and at >= now - 600 for at, state, _, source in rows))
    if rows and rows[0][1] not in (None, 'WORKING'):
        since = rows[0][0]
        for at, state, _, source in rows:
            if state in (None, 'WORKING'):
                break
            since = at
        result['non_working_since'] = since
    return result


def job_counts(db, table, column, successful, states):
    require(db, table, (column,))
    placeholders = ','.join('?' for _ in states)
    rows = db.execute('SELECT CASE WHEN "' + column + '" IN (' + placeholders + ') THEN "' +
                      column + '" ELSE \'other\' END AS state,COUNT(*) FROM "' + table +
                      '" GROUP BY 1', states).fetchall()
    counts = dict.fromkeys(states, 0)
    counts.update(rows)
    total = sum(counts.values())
    return dict(total=total, completed=counts.get(successful, 0), states=counts,
                ratio=counts.get(successful, 0) / total if total else None)


JOB_READERS = {
    'wechat_video': ('wechat', 'video_card_jobs', 'state', 'indexed',
                     ('indexed', 'blocked', 'download_unknown', 'queued', 'submitting',
                      'downloading', 'downloaded', 'download_failed', 'archive_verified')),
    'wechat_collection': ('wechat', 'receive_collection_jobs', 'state', 'complete',
                          ('complete', 'processing', 'retry_wait', 'RAW_ARCHIVED_NO_TEXT',
                           'saved_original_needs_review', 'interrupted', 'source_changed')),
    'wechat_notes': ('wechat', 'company_note_assets', 'status', 'ready', ('ready',)),
    'whatsapp_media': ('whatsapp', 'media_jobs', 'status', 'archived',
                       ('archived', 'none', 'pending_media_url', 'deferred')),
}


READERS = {
    'alibaba_inbound': ('inbound', inbound),
    'alibaba_probe': ('probe', probe),
    'wechat_messages': ('wechat', wechat_messages),
    'wechat_callbacks': ('wechat', lambda db, now: table_counts(db, 'received_callback_archive', 'received_at', now)),
    'whatsapp_events': ('whatsapp', lambda db, now: table_counts(db, 'events', 'received', now)),
    'whatsapp_session': ('whatsapp', whatsapp_session),
    'whatsapp_messages': ('whatsapp', whatsapp_messages),
}
