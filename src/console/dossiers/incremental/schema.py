"""Additive ledger. No ALTER/UPDATE of the imported dossier tables."""
import json

REVISION = 'dossier-incremental-v1'
DDL = '''
CREATE TABLE IF NOT EXISTS sd_cursor(
 adapter_id TEXT PRIMARY KEY,watermark_json TEXT NOT NULL,last_source_hash TEXT,reconciled_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sd_job(
 job_id TEXT PRIMARY KEY,stage TEXT NOT NULL,input_ref TEXT NOT NULL,input_revision TEXT NOT NULL,
 tool_revision TEXT NOT NULL,state TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,
 not_before TEXT NOT NULL,lease_until TEXT,cursor_json TEXT NOT NULL,error_code TEXT,
 started_at TEXT,finished_at TEXT,
 UNIQUE(stage,input_ref,input_revision,tool_revision));
CREATE TABLE IF NOT EXISTS sd_run(
 run_id TEXT PRIMARY KEY,job_id TEXT NOT NULL REFERENCES sd_job(job_id),session_id TEXT NOT NULL,
 budget_day TEXT NOT NULL,provider TEXT NOT NULL,requested_model TEXT NOT NULL,actual_model TEXT,
 tool_revision TEXT NOT NULL,prompt_sha256 TEXT NOT NULL,state TEXT NOT NULL,failure_code TEXT,
 prompt_tokens INTEGER,completion_tokens INTEGER,total_tokens INTEGER,
 charged_tokens INTEGER NOT NULL,reserved_tokens INTEGER NOT NULL,usage_json TEXT NOT NULL,
 started_at TEXT NOT NULL,finished_at TEXT);
CREATE TABLE IF NOT EXISTS sd_review(
 review_id TEXT PRIMARY KEY,job_id TEXT NOT NULL REFERENCES sd_job(job_id),
 review_kind TEXT NOT NULL CHECK(review_kind IN ('merge_conflict','price_anomaly')),
 supplier_id TEXT NOT NULL,product_id TEXT,offer_id TEXT,details_json TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'open',created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sd_gap_fill(
 gap_id TEXT PRIMARY KEY REFERENCES sd_gap(gap_id),job_id TEXT NOT NULL REFERENCES sd_job(job_id),
 offer_id TEXT NOT NULL REFERENCES sd_offer(offer_id),source_id TEXT NOT NULL REFERENCES sd_source(source_id),
 evidence_ids_json TEXT NOT NULL,filled_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sd_projection(
 table_name TEXT NOT NULL,row_key TEXT NOT NULL,job_id TEXT NOT NULL REFERENCES sd_job(job_id),
 created_at TEXT NOT NULL,PRIMARY KEY(table_name,row_key));
CREATE INDEX IF NOT EXISTS sd_job_ready_idx ON sd_job(state,not_before,lease_until);
CREATE INDEX IF NOT EXISTS sd_run_day_idx ON sd_run(budget_day);
CREATE INDEX IF NOT EXISTS sd_projection_time_idx ON sd_projection(created_at,table_name);
'''


def migrate(db):
    # Caller owns the transaction. executescript would implicitly commit it.
    for statement in DDL.split(';'):
        if statement.strip():
            db.execute(statement)
    required = {'sd_cursor': {'adapter_id', 'watermark_json'},
                'sd_job': {'job_id', 'cursor_json', 'lease_until'},
                'sd_run': {'run_id', 'charged_tokens', 'tool_revision'},
                'sd_review': {'review_kind', 'details_json'},
                'sd_gap_fill': {'gap_id', 'evidence_ids_json'},
                'sd_projection': {'table_name', 'row_key'}}
    for table, columns in required.items():
        if not columns <= {r[1] for r in db.execute('PRAGMA table_info(' + table + ')')}:
            raise ValueError('incompatible_incremental_schema')


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
