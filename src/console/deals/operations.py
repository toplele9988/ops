"""Local transaction receipts and draft-only outbox. No transport dependencies.

Extension tables are created only inside an authorised write transaction. They
do not alter the deployed archive migration versions or the channel outbox.
"""
import hashlib
import json
import re

from console.archive.validation import BadRequest, Conflict


def split(fields):
    raw = dict(fields)
    key = raw.pop('operation_key', None)
    if key is not None and not re.fullmatch(r'[A-Za-z0-9-]{8,100}', key):
        raise BadRequest('操作编号无效，请重新打开表单')
    return raw, key


def once(db, scope, key, payload, action):
    if key is None:  # Existing callers retain their published form contract.
        return action()
    db.execute('CREATE TABLE IF NOT EXISTS deal_operation_receipts ('
               'scope TEXT NOT NULL, operation_key TEXT NOT NULL, digest TEXT NOT NULL, '
               'result TEXT NOT NULL, PRIMARY KEY(scope,operation_key))')
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    old = db.execute('SELECT digest,result FROM deal_operation_receipts WHERE scope=? AND operation_key=?',
                     (scope, key)).fetchone()
    if old:
        if old['digest'] != digest:
            raise Conflict('同一次操作的内容已改变，请先重新读取核对结果')
        return json.loads(old['result'])
    result = action()
    db.execute('INSERT INTO deal_operation_receipts VALUES(?,?,?,?)',
               (scope, key, digest, json.dumps(result, ensure_ascii=False)))
    return result


def context(db, deal_id, country=None):
    exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='deal_operation_context'").fetchone()
    if country is not None:
        db.execute('CREATE TABLE IF NOT EXISTS deal_operation_context ('
                   'deal_id INTEGER PRIMARY KEY REFERENCES deals(id), destination_country TEXT NOT NULL)')
        db.execute('INSERT INTO deal_operation_context VALUES(?,?)', (deal_id, country))
        return country
    row = db.execute('SELECT destination_country FROM deal_operation_context WHERE deal_id=?',
                     (deal_id,)).fetchone() if exists else None
    return row[0] if row else None


def save_draft(db, deal_id, kind, text, who, stamp, *, quote_id=None, conversation_key=None):
    db.execute("CREATE TABLE IF NOT EXISTS deal_draft_outbox (id INTEGER PRIMARY KEY, "
               "deal_id INTEGER NOT NULL REFERENCES deals(id), quote_id INTEGER, "
               "kind TEXT NOT NULL, text TEXT NOT NULL, conversation_key TEXT, "
               "state TEXT NOT NULL CHECK(state='draft'), mode TEXT NOT NULL CHECK(mode='dry-run'), "
               "created_by TEXT NOT NULL, created_at TEXT NOT NULL)")
    row = db.execute("INSERT INTO deal_draft_outbox(deal_id,quote_id,kind,text,conversation_key,state,mode,created_by,created_at) "
                     "VALUES(?,?,?,?,?,'draft','dry-run',?,?)", (deal_id, quote_id, kind, text, conversation_key, who, stamp))
    return dict(ok=True, id=row.lastrowid, state='draft', mode='dry-run', text=text)


def drafts(db, deal_id):
    exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='deal_draft_outbox'").fetchone()
    return [dict(row) for row in db.execute('SELECT * FROM deal_draft_outbox WHERE deal_id=? ORDER BY id DESC',
                                          (deal_id,))] if exists else []
