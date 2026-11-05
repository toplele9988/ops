"""The public profile contract and isolated side-database schema."""
import json

KINDS = ('customer', 'supplier', 'forwarder', 'vendor', 'peer', 'industry', 'internal', 'personal', 'system', 'unknown')
STAGES = ('potential', 'quoted', 'customer', 'after_sales', 'dormant')
CHANNELS = ('wechat', 'alibaba', 'whatsapp')
JSON_FIELDS = ('people_json', 'products_json', 'evidence_json')
FIELDS = ('conversation_key', 'channel', 'title', 'kind', 'customer_stage', 'org_name', 'people_json',
          'country', 'country_name', 'language', 'products_json', 'summary', 'evidence_json', 'confidence',
          'messages_read', 'messages_total', 'first_at', 'last_at', 'last_inbound_at', 'state',
          'error_code', 'model', 'updated_at')
SCHEMA = '''
CREATE TABLE IF NOT EXISTS cp_profile (
 conversation_key TEXT PRIMARY KEY, channel TEXT, title TEXT, kind TEXT, customer_stage TEXT,
 org_name TEXT, people_json TEXT, country TEXT, country_name TEXT, language TEXT,
 products_json TEXT, summary TEXT, evidence_json TEXT, confidence REAL,
 messages_read INT, messages_total INT, first_at REAL, last_at REAL, last_inbound_at REAL,
 state TEXT, error_code TEXT, model TEXT, updated_at REAL);
CREATE TABLE IF NOT EXISTS cp_cursor (
 conversation_key TEXT PRIMARY KEY, total INT NOT NULL, prefix_hash TEXT NOT NULL, checked_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS cp_window (
 id INTEGER PRIMARY KEY, conversation_key TEXT NOT NULL, ordinal INT NOT NULL,
 source_json TEXT NOT NULL, result_json TEXT, model TEXT, state TEXT NOT NULL DEFAULT 'pending',
 error_code TEXT, updated_at REAL, UNIQUE(conversation_key, ordinal));
CREATE TABLE IF NOT EXISTS cp_run (
 id INTEGER PRIMARY KEY, started_at REAL NOT NULL, finished_at REAL, conversations INT DEFAULT 0,
 windows INT DEFAULT 0, failures INT DEFAULT 0);
CREATE TABLE IF NOT EXISTS cp_call (
 id INTEGER PRIMARY KEY, run_id INT NOT NULL, at REAL NOT NULL, reserved_tokens INT NOT NULL,
 charged_tokens INT NOT NULL, reported_tokens INT, state TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS cp_window_conversation ON cp_window(conversation_key,state,ordinal);
CREATE INDEX IF NOT EXISTS cp_call_time ON cp_call(at);
'''


def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def profile_view(row):
    result = dict(row)
    for field in JSON_FIELDS:
        result[field] = json.loads(result[field] or '[]')
    return result


def save_profile(db, profile):
    values = {k: profile[k] for k in FIELDS}
    for field in JSON_FIELDS:
        values[field] = dumps(values[field])
    db.execute('INSERT INTO cp_profile (' + ','.join(FIELDS) + ') VALUES (' + ','.join('?' for _ in FIELDS) +
               ') ON CONFLICT(conversation_key) DO UPDATE SET ' +
               ','.join(k + '=excluded.' + k for k in FIELDS if k != 'conversation_key'), tuple(values.values()))

SCHEMA += '''
CREATE TABLE IF NOT EXISTS cp_reduce (
 fingerprint TEXT PRIMARY KEY, result_json TEXT NOT NULL, model TEXT, updated_at REAL NOT NULL);
'''
