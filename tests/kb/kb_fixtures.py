"""Fictional records; column names/types mirror the read-only catalog inspection."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import struct
from types import SimpleNamespace

from console.kb.index import MODEL_VERSION

SCHEMA = '''
CREATE TABLE knowledge_chunks(chunk_id TEXT PRIMARY KEY, asset_id TEXT NOT NULL, equipment_id TEXT,
 owner TEXT NOT NULL, source_scope TEXT NOT NULL, parent_chunk INTEGER NOT NULL, child_chunk INTEGER NOT NULL,
 text TEXT NOT NULL, dense_vector BLOB NOT NULL, sparse_json TEXT NOT NULL, model_version TEXT NOT NULL, evidence_json TEXT NOT NULL);
CREATE TABLE multimedia_assets(asset_id TEXT PRIMARY KEY,logical_asset_id TEXT NOT NULL,source_id TEXT NOT NULL,
 owner TEXT NOT NULL,source_scope TEXT NOT NULL,filename TEXT NOT NULL,nas_rel_path TEXT NOT NULL,source_type TEXT NOT NULL,
 created_at TEXT NOT NULL,source_file_mtime_ns INTEGER,source_sha256 TEXT NOT NULL,source_bytes INTEGER,
 summary_text TEXT NOT NULL,key_takeaways_json TEXT NOT NULL,domain_category TEXT NOT NULL,
 extraction_snapshot_json TEXT NOT NULL,extraction_status TEXT NOT NULL,verification_status TEXT NOT NULL DEFAULT 'candidate',indexed_at TEXT NOT NULL);
CREATE TABLE catalog_access_labels(label_id TEXT PRIMARY KEY,source_sha256 TEXT NOT NULL,owner TEXT NOT NULL,
 source_scope TEXT NOT NULL,reason TEXT NOT NULL,confidentiality TEXT NOT NULL,is_cost_price INTEGER NOT NULL,created_at REAL NOT NULL);
CREATE TABLE channel_role_registry(classification_id TEXT PRIMARY KEY,account_sha256 TEXT NOT NULL,channel_kind TEXT NOT NULL,
 channel_id_sha256 TEXT NOT NULL,owner TEXT NOT NULL,source_scope TEXT NOT NULL,display_name TEXT NOT NULL,channel_role TEXT NOT NULL,
 snapshot_sha256 TEXT NOT NULL,observed_at REAL NOT NULL,name_provenance TEXT NOT NULL);
'''


def vector(axis=0):
    values = [0.] * 1024
    values[axis] = 1.
    return values


def tm_scope(account='fictional-account', cid='tm:fictional'):
    digest = hashlib.sha256(json.dumps([account, cid], ensure_ascii=False, sort_keys=True,
        separators=(',', ':')).encode()).hexdigest()
    return 'icbu-tm:fictional-binding:' + digest


def row(i, **extra):
    return dict(i=i, aid='asset-' + str(i), scope='internal_supplier', owner='fictional-owner',
        kind='supplier', text='虚构灯具 外箱尺寸 200W gross weight LED carton',
        title='虚构产品资料.txt', parent=0, child=0, vector=vector(), **extra)


def seed(path, rows=None):
    path = Path(path)
    with closing(sqlite3.connect(path)) as db, db:
        if not db.execute("select 1 from sqlite_master where name='knowledge_chunks'").fetchone():
            db.executescript(SCHEMA)
        for record in rows if rows is not None else [row(1)]:
            r = dict(record)
            sha = r.get('sha', hashlib.sha256(r['aid'].encode()).hexdigest())
            db.execute('INSERT OR IGNORE INTO multimedia_assets VALUES('+','.join('?' for _ in range(19))+')',
                (r['aid'], 'logical-'+r['aid'], 'fictional-source', r['owner'], r['scope'], r['title'],
                 'fictional/private/file.txt', r['kind'], r.get('date', '2026-01-01T00:00:00+00:00'),
                 r.get('mtime'), sha, 100, '', '[]', 'fictional', '{}', 'ready', 'candidate', '2026-01-01'))
            db.execute('INSERT INTO knowledge_chunks VALUES('+','.join('?' for _ in range(12))+')',
                ('chunk-'+str(r['i']), r['aid'], None, r['owner'], r['scope'], r['parent'], r['child'], r['text'],
                 struct.pack('<1024f', *r['vector']), '{}', r.get('version', MODEL_VERSION), '{}'))
            if 'confidentiality' in r or 'cost' in r:
                db.execute('INSERT INTO catalog_access_labels VALUES(?,?,?,?,?,?,?,?)',
                    ('label-'+str(r['i']), sha, r['owner'], r.get('label_scope', r['scope']), 'fictional-reason',
                     r.get('confidentiality', 'public'), r.get('cost', 0), 1800000000))
            if 'role' in r:
                db.execute('INSERT INTO channel_role_registry VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                    ('role-'+str(r['i']), 'fictional-account-hash', 'group', 'fictional-group-hash', r['owner'],
                     r['scope'], r.get('name', '虚构群'), r['role'], 'fictional-snapshot-hash', 1800000000, 'fictional'))


def record(i, **overrides):
    return {**row(i), **overrides}


def request(query=None, form=None, body=b'', authenticated=True, principal=True, recheck=True, key=''):
    async def read_body():
        return body
    return SimpleNamespace(state=SimpleNamespace(form=form, password_principal=SimpleNamespace(username='虚构保存人') if principal else None,
        session=SimpleNamespace(authenticated=authenticated), password_recheck=lambda: recheck),
        query_params=query or {}, path_params={'key': key}, body=read_body)
