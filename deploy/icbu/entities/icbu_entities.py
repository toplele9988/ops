#!/usr/bin/env python3
"""Business snapshots only: no chat writes, downloads, publishing or sending."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
from urllib.parse import unquote, urlsplit

ENTITY_PATH = '/api/inbound/alibaba/entities'
DEFAULT_DB = '/var/lib/imgw/icbu/entities.sqlite3'
ENTITY_TYPES = ('order', 'order_logistics', 'inquiry', 'rfq', 'opportunity', 'customer',
                'customer_followup', 'product_listing', 'ad_campaign_report',
                'ad_keyword_report', 'shop_report', 'other')
IDENTIFIER = re.compile(r'[A-Za-z0-9._:/@+\-=]{1,256}\Z')
BATCH_ID = re.compile(r'[A-Za-z0-9._:-]{1,128}\Z')
UTC_TIME = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)\Z')
APPLICATION_ID = 0x49434245
MAX_BATCH = 100
SCHEMA = '''
CREATE TABLE entity_batches (
 batch_id TEXT PRIMARY KEY, request_sha256 TEXT NOT NULL, received_at TEXT NOT NULL,
 entity_count INTEGER NOT NULL, versions_added INTEGER NOT NULL,
 current_updated INTEGER NOT NULL, earliest_observed_at TEXT NOT NULL,
 latest_observed_at TEXT NOT NULL, response TEXT NOT NULL
);
CREATE TABLE entity_versions (
 version_id INTEGER PRIMARY KEY, entity_type TEXT NOT NULL, entity_id TEXT NOT NULL,
 fields_sha256 TEXT NOT NULL, fields_json TEXT NOT NULL, snapshot_json TEXT NOT NULL,
 observed_at TEXT NOT NULL, received_at TEXT NOT NULL,
 batch_id TEXT NOT NULL REFERENCES entity_batches(batch_id) DEFERRABLE INITIALLY DEFERRED,
 UNIQUE(entity_type, entity_id, fields_sha256)
);
CREATE TABLE entity_current (
 entity_type TEXT NOT NULL, entity_id TEXT NOT NULL, version_id INTEGER NOT NULL
 REFERENCES entity_versions(version_id), fields_sha256 TEXT NOT NULL,
 snapshot_json TEXT NOT NULL, observed_at TEXT NOT NULL,
 first_observed_at TEXT NOT NULL, last_observed_at TEXT NOT NULL,
 batch_id TEXT NOT NULL REFERENCES entity_batches(batch_id) DEFERRABLE INITIALLY DEFERRED,
 PRIMARY KEY(entity_type, entity_id)
);
CREATE INDEX entity_batches_recent ON entity_batches(received_at DESC, batch_id);
CREATE INDEX entity_current_type_time ON entity_current(entity_type, observed_at);
PRAGMA application_id=1229144645;
PRAGMA user_version=1;
'''


class EntityRejected(Exception):
    def __init__(self, code, status=400):
        self.code, self.status = code, status
        super().__init__(code)


def require(condition, code, status=400):
    if not condition:
        raise EntityRejected(code, status)


def compact(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def identifier(value, name='entity_id'):
    require(isinstance(value, str) and IDENTIFIER.fullmatch(value), 'invalid_' + name)
    return value


def utc_timestamp(value):
    require(isinstance(value, str) and UTC_TIME.fullmatch(value), 'invalid_entity_observed_at')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        require(parsed.utcoffset() == timedelta(0), 'invalid_entity_observed_at')
        return parsed.isoformat(timespec='microseconds')
    except ValueError:
        raise EntityRejected('invalid_entity_observed_at') from None


def validate_batch(payload):
    require(isinstance(payload, dict) and set(payload) == {'batch_id', 'entities'}, 'invalid_entity_batch')
    batch = payload['batch_id']
    require(isinstance(batch, str) and BATCH_ID.fullmatch(batch), 'invalid_entity_batch_id')
    values = payload['entities']
    require(isinstance(values, list) and 1 <= len(values) <= MAX_BATCH, 'invalid_entity_batch_size')
    result, seen = [], set()
    required = {'entity_type', 'entity_id', 'observed_at', 'source_page', 'collector_version', 'fields'}
    for value in values:
        require(isinstance(value, dict) and required <= value.keys()
                and not (value.keys() - required - {'related', 'attachments'}), 'invalid_entity_fields')
        kind = value['entity_type']
        require(isinstance(kind, str) and kind in ENTITY_TYPES, 'invalid_entity_type')
        key = (kind, identifier(value['entity_id']))
        require(key not in seen, 'duplicate_entity_in_batch')
        seen.add(key)
        item = dict(value, observed_at=utc_timestamp(value['observed_at']))
        page = value['source_page']
        require(isinstance(page, str) and 1 <= len(page) <= 2048 and page.startswith('/')
                and not page.startswith('//') and '\\' not in page
                and not any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in page), 'invalid_entity_source_page')
        parsed = urlsplit(page)
        require(not parsed.scheme and not parsed.netloc, 'invalid_entity_source_page')
        item['source_page'] = parsed.path  # never retain a query/fragment or fetch a URL
        version = value['collector_version']
        require(isinstance(version, str) and re.fullmatch(r'[A-Za-z0-9._+\-]{1,64}', version),
                'invalid_collector_version')
        require(isinstance(value['fields'], dict), 'invalid_entity_data')
        if 'related' in value:
            require(isinstance(value['related'], dict), 'invalid_entity_related')
        if 'attachments' in value:
            refs = value['attachments']
            require(isinstance(refs, list) and len(refs) <= 20, 'invalid_entity_attachments')
            ref_keys = set()
            for ref in refs:
                require(isinstance(ref, dict) and set(ref) == {'event_id', 'attachment_id'},
                        'invalid_entity_attachments')
                event = identifier(ref['event_id'], 'entity_attachment_event_id')
                require(not event.startswith('e1_'), 'invalid_entity_attachment_event_id')
                aid = ref['attachment_id']
                require(isinstance(aid, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,128}', aid),
                        'invalid_entity_attachments')
                require((event, aid) not in ref_keys, 'invalid_entity_attachments')
                ref_keys.add((event, aid))
        result.append(item)
    try:
        compact(result).encode('utf-8')  # rejects non-finite numbers and lone surrogates, at any depth
    except (ValueError, UnicodeError, RecursionError, TypeError):
        raise EntityRejected('invalid_entity_json') from None
    return {'batch_id': batch, 'entities': result}


def route(path):
    if path in (ENTITY_PATH, ENTITY_PATH + '/stats'):
        return path, None
    match = re.fullmatch(re.escape(ENTITY_PATH) + r'/([^/]+)/([^/?#]+)', path)
    require(match is not None and match[1] in ENTITY_TYPES, 'not_found', 404)
    encoded = match[2]
    require(not re.search(r'%(?![0-9A-Fa-f]{2})', encoded), 'invalid_entity_id')
    try:
        value = unquote(encoded, encoding='utf-8', errors='strict')
    except UnicodeError:
        raise EntityRejected('invalid_entity_id') from None
    return match[1], identifier(value)


def _private(info, directory=False):
    require((stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
            and info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == (0o700 if directory else 0o600)
            and (directory or info.st_nlink == 1), 'entity_storage_unsafe', 503)


class EntityStore:
    def __init__(self, path=DEFAULT_DB, *, initialize=True):
        self.path = Path(path).absolute()
        require('..' not in self.path.parts, 'entity_storage_unsafe', 503)
        self._parent_identity = self._db_identity = None
        if initialize:
            with self.connect(create=True) as db:
                version = db.execute('PRAGMA user_version').fetchone()[0]
                tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if version == 0 and not tables:
                    db.execute('PRAGMA journal_mode=WAL')
                    db.executescript('BEGIN IMMEDIATE;\n' + SCHEMA + '\nCOMMIT;')
                self._check_schema(db)

    @staticmethod
    def _check_schema(db):
        require(db.execute('PRAGMA application_id').fetchone()[0] == APPLICATION_ID
                and db.execute('PRAGMA user_version').fetchone()[0] == 1,
                'entity_database_version_invalid', 503)

    @contextmanager
    def connect(self, *, create=False, readonly=False):
        parent = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
        db = None
        try:
            for part in self.path.parent.parts[1:]:
                if create:
                    try:
                        os.mkdir(part, 0o700, dir_fd=parent)
                    except FileExistsError:
                        pass
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                os.close(parent)
                parent = child
            info = os.fstat(parent)
            _private(info, True)
            identity = (info.st_dev, info.st_ino)
            require(self._parent_identity in (None, identity), 'entity_storage_unsafe', 503)
            self._parent_identity = identity
            fd = os.open(self.path.name, os.O_NOFOLLOW | os.O_NONBLOCK |
                         (os.O_RDWR | os.O_CREAT if create else os.O_RDONLY), 0o600, dir_fd=parent)
            try:
                info = os.fstat(fd)
                _private(info)
                identity = (info.st_dev, info.st_ino)
                require(self._db_identity in (None, identity), 'entity_storage_unsafe', 503)
                self._db_identity = identity
            finally:
                os.close(fd)
            for suffix in ('-wal', '-shm', '-journal'):
                try:
                    info = os.stat(self.path.name + suffix, dir_fd=parent, follow_symlinks=False)
                except FileNotFoundError:
                    continue
                _private(info)
            db = sqlite3.connect(f'file:/proc/self/fd/{parent}/{self.path.name}?mode={"ro" if readonly else "rw"}',
                                 uri=True, timeout=5, isolation_level=None)
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('PRAGMA synchronous=FULL')
            if readonly:
                db.execute('PRAGMA query_only=ON')
            with db:
                yield db
        except OSError as exc:
            if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                raise EntityRejected('entity_storage_unsafe', 503) from None
            full = exc.errno in (errno.ENOSPC, errno.EDQUOT)
            raise EntityRejected('entity_disk_full' if full else 'entity_storage_unavailable', 507 if full else 503) from None
        except sqlite3.Error as exc:
            full = getattr(exc, 'sqlite_errorcode', None) == sqlite3.SQLITE_FULL
            raise EntityRejected('entity_disk_full' if full else 'entity_storage_unavailable', 507 if full else 503) from None
        finally:
            if db is not None:
                db.close()  # an unfinished transaction is rolled back, including all batch rows
            os.close(parent)

    def receive(self, payload, *, now=None):
        payload = validate_batch(payload)
        request_hash = hashlib.sha256(compact(payload).encode('utf-8')).hexdigest()
        stamp = (now or datetime.now(timezone.utc)).isoformat(timespec='microseconds')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            previous = db.execute('SELECT * FROM entity_batches WHERE batch_id=?', (payload['batch_id'],)).fetchone()
            if previous:
                require(previous['request_sha256'] == request_hash, 'entity_batch_conflict', 409)
                db.execute('COMMIT')
                return dict(json.loads(previous['response']), duplicate=True)
            added = updated = 0
            results = []
            for item in payload['entities']:
                kind, eid, observed = item['entity_type'], item['entity_id'], item['observed_at']
                fields = compact(item['fields'])
                digest = hashlib.sha256(fields.encode('utf-8')).hexdigest()
                snapshot = compact(item)
                current = db.execute('SELECT * FROM entity_current WHERE entity_type=? AND entity_id=?', (kind, eid)).fetchone()
                require(not current or current['observed_at'] != observed or current['fields_sha256'] == digest,
                        'entity_observation_conflict', 409)
                old_version = db.execute('SELECT version_id FROM entity_versions WHERE entity_type=? AND entity_id=? AND fields_sha256=?',
                                         (kind, eid, digest)).fetchone()
                if old_version:
                    vid = old_version['version_id']
                else:
                    vid = db.execute('''INSERT INTO entity_versions(entity_type,entity_id,fields_sha256,fields_json,
                      snapshot_json,observed_at,received_at,batch_id) VALUES(?,?,?,?,?,?,?,?)''',
                                     (kind, eid, digest, fields, snapshot, observed, stamp, payload['batch_id'])).lastrowid
                    added += 1
                newer = current is None or observed > current['observed_at']
                first = min(observed, current['first_observed_at']) if current else observed
                last = max(observed, current['last_observed_at']) if current else observed
                if newer:
                    db.execute('''INSERT INTO entity_current VALUES(?,?,?,?,?,?,?,?,?)
                      ON CONFLICT(entity_type,entity_id) DO UPDATE SET version_id=excluded.version_id,
                      fields_sha256=excluded.fields_sha256,snapshot_json=excluded.snapshot_json,
                      observed_at=excluded.observed_at,first_observed_at=excluded.first_observed_at,
                      last_observed_at=excluded.last_observed_at,batch_id=excluded.batch_id''',
                               (kind, eid, vid, digest, snapshot, observed, first, last, payload['batch_id']))
                    updated += 1
                else:
                    db.execute('UPDATE entity_current SET first_observed_at=?,last_observed_at=? WHERE entity_type=? AND entity_id=?',
                               (first, last, kind, eid))
                results.append(dict(entity_type=kind, entity_id=eid, version_id=vid,
                                    version_created=old_version is None, current_updated=newer))
            response = dict(ok=True, persisted=True, batch_id=payload['batch_id'], duplicate=False,
                            entity_count=len(results), versions_added=added, current_updated=updated, results=results)
            times = [item['observed_at'] for item in payload['entities']]
            db.execute('INSERT INTO entity_batches VALUES(?,?,?,?,?,?,?,?,?)',
                       (payload['batch_id'], request_hash, stamp, len(results), added, updated, min(times), max(times), compact(response)))
            db.execute('COMMIT')
            return response

    def stats(self):
        with self.connect(readonly=True) as db:
            db.execute('BEGIN')
            self._check_schema(db)
            counts = {r['entity_type']: dict(r) for r in db.execute('''SELECT entity_type,COUNT(*) AS count,
              MIN(first_observed_at) AS earliest_observed_at,MAX(last_observed_at) AS latest_observed_at
              FROM entity_current GROUP BY entity_type''')}
            versions = {r[0]: r[1] for r in db.execute('SELECT entity_type,COUNT(*) FROM entity_versions GROUP BY entity_type')}
            types = [dict(counts.get(kind, dict(entity_type=kind, count=0, earliest_observed_at=None,
                                               latest_observed_at=None)), versions=versions.get(kind, 0)) for kind in ENTITY_TYPES]
            batches = [dict(r) for r in db.execute('''SELECT batch_id,received_at,entity_count,versions_added,
              current_updated,earliest_observed_at,latest_observed_at FROM entity_batches
              ORDER BY received_at DESC,batch_id DESC LIMIT 20''')]
            total_batches = db.execute('SELECT COUNT(*) FROM entity_batches').fetchone()[0]
        return dict(ok=True, types=types, total_entities=sum(r['count'] for r in types),
                    total_versions=sum(versions.values()), total_batches=total_batches, recent_batches=batches)

    def get(self, kind, eid):
        require(isinstance(kind, str) and kind in ENTITY_TYPES, 'invalid_entity_type')
        identifier(eid)
        with self.connect(readonly=True) as db:
            row = db.execute('SELECT * FROM entity_current WHERE entity_type=? AND entity_id=?', (kind, eid)).fetchone()
        require(row is not None, 'not_found', 404)
        return dict(ok=True, entity=json.loads(row['snapshot_json']), version_id=row['version_id'],
                    fields_sha256=row['fields_sha256'], first_observed_at=row['first_observed_at'],
                    last_observed_at=row['last_observed_at'], batch_id=row['batch_id'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', required=True)
    parser.add_argument('--initialize', action='store_true', help='Explicitly create/migrate the entities database')
    args = parser.parse_args()
    os.umask(0o077)
    try:
        store = EntityStore(args.db, initialize=args.initialize)
        result = store.stats()
        # Operational self-check prints counts only, never entity IDs, snapshots or batches.
        print(compact({k: result[k] for k in ('ok', 'total_entities', 'total_versions', 'total_batches')}))
    except EntityRejected as exc:
        print(compact(dict(ok=False, error=exc.code)))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
