"""Private, immutable supplier question snapshots. No outbox or send contract."""
from datetime import datetime, timezone
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3

from .store import ROOT, readonly
from .service import Missing

DDL = '''CREATE TABLE IF NOT EXISTS sd_question_draft(
 draft_id TEXT PRIMARY KEY, supplier_id TEXT NOT NULL,
 basis_sha256 TEXT NOT NULL, gap_ids_json TEXT NOT NULL,
 source_refs_json TEXT NOT NULL, gap_snapshot_json TEXT NOT NULL,
 text TEXT NOT NULL, state TEXT NOT NULL CHECK(state='draft'),
 created_at TEXT NOT NULL, created_by TEXT NOT NULL,
 UNIQUE(supplier_id,basis_sha256)
);'''


def migrate(path=ROOT / 'drafts.sqlite3'):
    path = Path(path)
    if path.is_symlink():
        raise ValueError('unsafe_draft_store')
    old = os.umask(0o077)
    try:
        with closing(sqlite3.connect(path)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('PRAGMA user_version').fetchone()[0] not in (0, 1):
                raise ValueError('unsupported_draft_revision')
            db.execute(DDL)
            columns = [r[1] for r in db.execute('PRAGMA table_info(sd_question_draft)')]
            if columns != ['draft_id','supplier_id','basis_sha256','gap_ids_json','source_refs_json','gap_snapshot_json','text','state','created_at','created_by']:
                raise ValueError('incompatible_draft_schema')
            db.execute('PRAGMA user_version=1')
        path.chmod(0o600)
    finally:
        os.umask(old)


class Drafts:
    def __init__(self, service, path=ROOT / 'drafts.sqlite3'):
        self.service, self.path = service, Path(path)

    def list(self, supplier):
        with readonly(self.path) as db:
            rows = [dict(r) for r in db.execute('SELECT * FROM sd_question_draft WHERE supplier_id=? ORDER BY created_at DESC,draft_id LIMIT 30', (supplier,))]
        return dict(items=rows)

    def create(self, supplier, filters, username):
        with readonly(self.service.path) as db:
            self.service.check(db)
            s = db.execute('SELECT name FROM sd_supplier WHERE supplier_id=?', (supplier,)).fetchone()
            if not s:
                raise Missing
            gaps = self.service._gaps(db, dict(filters, supplier=supplier))
            refs = sorted({ref for g in gaps for ref in g['source_refs']})
            sources = [dict(db.execute('SELECT source_id,content_sha256,file_sha256 FROM sd_source WHERE source_id=?', (ref,)).fetchone()) for ref in refs]
        if not gaps:
            raise Missing
        snapshot = json.dumps(dict(gaps=gaps, sources=sources), ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        basis = hashlib.sha256(snapshot.encode()).hexdigest()
        # Duplicate questions collapse in the prose, while all gap identities remain in the snapshot.
        asks = list(dict.fromkeys(g['ask_text'] for g in gaps))
        text = '您好，想向贵司核对以下资料，请按项目逐项回复，并附对应型号、配置和资料版本：\n\n' + '\n'.join(f'{i}. {ask}' for i, ask in enumerate(asks, 1)) + '\n\n如涉及报价，请注明币种、税制、数量条件、交期和有效期。谢谢。'
        row = dict(draft_id='sdq-' + basis, supplier_id=supplier, basis_sha256=basis,
                   gap_ids_json=json.dumps([g['gap_id'] for g in gaps]), source_refs_json=json.dumps(sources),
                   gap_snapshot_json=snapshot, text=text, state='draft',
                   created_at=datetime.now(timezone.utc).isoformat(), created_by=username)
        if self.path.is_symlink() or not self.path.is_file():
            raise ValueError('draft_store_not_migrated')
        with closing(sqlite3.connect(self.path, timeout=3)) as db, db:
            db.row_factory = sqlite3.Row
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT OR IGNORE INTO sd_question_draft (' + ','.join(row) + ') VALUES (' + ','.join('?' for _ in row) + ')', tuple(row.values()))
            saved = dict(db.execute('SELECT * FROM sd_question_draft WHERE draft_id=?', (row['draft_id'],)).fetchone())
        return saved
