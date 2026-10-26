"""New example DOM-tail drafts, never customer sends. No I/O or HTTP at import.

Old archive rows are not jobs. Provider fences commit before HTTP; unknown
outcomes are held, and a private durable checkpoint is applied without re-call.
"""
from __future__ import annotations
if __name__ == '__main__':
    raise SystemExit('Reference snapshot cannot be run; dependencies are intentionally incomplete.')
import contextlib, fcntl, hashlib, json, math, os, re, sqlite3, stat, tempfile, time, uuid
from datetime import datetime, timezone
from pathlib import Path
CONTRACT = 'example-live-v1'
POLICY_PATH = os.environ.get('ICBU_EXAMPLE_PATH_06', '')
MODELS = ('deepseek-v4-flash-0731', 'qwen3.8-max', 'qwen3.8-2.4t-a95b', 'deepseek-v4-pro-0813', 'kimi-k3', 'glm-5.3', 'deepseek-v4.1-flash', 'deepseek-v4.1-flash-1017')
THINKING = {m: 'omit' if m in {'qwen3.8-2.4t-a95b', 'kimi-k3', 'glm-5.3'} else 'enable_thinking_false' for m in MODELS}
OUTPUT = 1024
PROVENANCE_FIELDS = {'schema', 'mode', 'epoch_uuid', 'baseline_server_time_utc', 'baseline_watermark_sent_at', 'observed_seq'}
RESPONSE_SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['opening', 'question', 'fact_ids'], 'properties': {'opening': {'type': 'string', 'enum': ['thank_you', 'glad_to_help']}, 'question': {'type': 'string', 'enum': ['application', 'destination', 'none']}, 'fact_ids': {'type': 'array', 'maxItems': 3, 'items': {'type': 'string'}}}}
OPENINGS = {'thank_you': 'Thank you for your enquiry.', 'glad_to_help': 'Thank you for getting in touch. We would be happy to review your requirements.'}
QUESTIONS = {'application': 'Could you confirm the intended application and any essential features?', 'destination': 'Could you confirm the delivery country and city?', 'none': ''}

def canonical(v):
    return json.dumps(v, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)

def sha(v):
    return hashlib.sha256(v if isinstance(v, bytes) else v.encode()).hexdigest()

def need(v, code):
    if not v:
        raise ValueError(code)

def utc(value):
    need(isinstance(value, str) and len(value) <= 64, 'live_timestamp_invalid')
    t = datetime.fromisoformat(value.replace('Z', '+00:00'))
    need(t.tzinfo is not None, 'live_timestamp_invalid')
    n = t.timestamp()
    need(math.isfinite(n), 'live_timestamp_invalid')
    return n

def checked_provenance(value):
    need(type(value) is dict and set(value) == PROVENANCE_FIELDS, 'live_provenance_invalid')
    need(value['schema'] == CONTRACT and value['mode'] in ('dom_tail_append', 'native_poll_after_baseline'), 'live_provenance_invalid')
    need(isinstance(value['epoch_uuid'], str) and str(uuid.UUID(value['epoch_uuid'])) == value['epoch_uuid'], 'live_epoch_invalid')
    need(type(value['observed_seq']) is int and 1 <= value['observed_seq'] <= 1000000, 'live_sequence_invalid')
    utc(value['baseline_server_time_utc'])
    utc(value['baseline_watermark_sent_at'])
    return json.loads(canonical(value))

def admit(payload, policy, now):
    """Conservative: failed proof remains archived; never upgrade an old row."""
    if not policy:
        return (False, 'live_drafts_not_enabled')
    if payload.get('history') is not False:
        return (False, 'history_import')
    if payload.get('role') != 'buyer' or payload.get('role_assumption'):
        return (False, 'role_not_explicit_buyer')
    if not payload.get('message_id'):
        return (False, 'live_message_id_required')
    if payload.get('probe_version') not in ('0.3.4', '0.3.5'):
        return (False, 'live_probe_version_unverified')
    if payload.get('url') not in ('https://onetalk.alibaba.com/message/messageManager.htm', 'https://onetalk.alibaba.com/message/weblitePWA.htm') or not re.fullmatch('tm:[A-Za-z0-9_-]{1,128}', payload.get('conversation_id', '')) or (not re.fullmatch('\\d{1,30}(?:\\.PNM)?', payload['message_id'])):
        return (False, 'live_platform_identity_unverified')
    p = payload.get('live_provenance')
    if not p:
        return (False, 'live_proof_missing')
    try:
        checked_provenance(p)
        sent = utc(payload.get('sent_at'))
        observed = utc(payload.get('observed_at'))
        baseline = utc(p['baseline_server_time_utc'])
        watermark = utc(p['baseline_watermark_sent_at'])
        cutover = utc(policy['cutover_utc'])
        need(payload['account'] == os.environ.get('ICBU_EXAMPLE_ACCOUNT', 'example-account'), 'live_account_invalid')
        need(cutover <= baseline < sent and watermark < sent, 'live_before_baseline')
        need(0 <= now - sent <= 300 and sent <= observed <= now + 30 and (observed - baseline <= 86400), 'live_receipt_window_invalid')
    except (ValueError, TypeError, KeyError, OverflowError):
        return (False, 'live_proof_stale_or_invalid')
    return (True, None)
SCHEMA = '\nCREATE TABLE IF NOT EXISTS live_draft_jobs(\n inbound_id TEXT PRIMARY KEY REFERENCES inbound(id),state TEXT NOT NULL,\n synthetic INTEGER NOT NULL DEFAULT 0 CHECK(synthetic IN (0,1)),policy_sha256 TEXT NOT NULL,\n created_at REAL NOT NULL,updated_at REAL NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,\n next_attempt_at REAL NOT NULL DEFAULT 0,last_error TEXT,provider TEXT,model TEXT,\n ready_at REAL,checkpoint_sha256 TEXT);\nCREATE TABLE IF NOT EXISTS live_draft_attempts(\n inbound_id TEXT NOT NULL REFERENCES live_draft_jobs(inbound_id),attempt INTEGER NOT NULL,\n status TEXT NOT NULL,model TEXT NOT NULL,started_at REAL NOT NULL,finished_at REAL,\n metadata_json TEXT,error_code TEXT,prompt_sha256 TEXT NOT NULL,response_sha256 TEXT,\n PRIMARY KEY(inbound_id,attempt));\nCREATE TABLE IF NOT EXISTS live_draft_scope(key TEXT PRIMARY KEY,value TEXT NOT NULL);\nCREATE TABLE IF NOT EXISTS live_draft_epochs(epoch_uuid TEXT PRIMARY KEY,baseline TEXT NOT NULL,\n last_seq INTEGER NOT NULL,last_sent_at REAL NOT NULL);\n'

def install_schema(db):
    db.executescript(SCHEMA)

def insert_job(db, inbound_id, payload, policy, now):
    p = payload['live_provenance']
    baseline = canonical({**{k: p[k] for k in ('mode', 'baseline_server_time_utc', 'baseline_watermark_sent_at')}, 'account': payload['account'], 'conversation_id': payload['conversation_id']})
    epoch = db.execute('SELECT * FROM live_draft_epochs WHERE epoch_uuid=?', (p['epoch_uuid'],)).fetchone()
    if epoch:
        need(epoch[1] == baseline and p['observed_seq'] > epoch[2] and (utc(payload['sent_at']) > epoch[3]), 'live_epoch_replayed')
        db.execute('UPDATE live_draft_epochs SET last_seq=?,last_sent_at=? WHERE epoch_uuid=?', (p['observed_seq'], utc(payload['sent_at']), p['epoch_uuid']))
    else:
        db.execute('INSERT INTO live_draft_epochs VALUES(?,?,?,?)', (p['epoch_uuid'], baseline, p['observed_seq'], utc(payload['sent_at'])))
    db.execute("INSERT INTO live_draft_jobs(inbound_id,state,policy_sha256,created_at,updated_at) VALUES(?,'draft_queued',?,?,?)", (inbound_id, sha(canonical(policy)), now, now))
from icbu_knowledge_drafts import RESPONSE_SCHEMA, build_prompt, render_response, parse_response, KnowledgePipeline, review_assessment

def checkpoint_write(directory, inbound_id, attempt, value):
    directory = Path(directory)
    directory.mkdir(mode=448, parents=True, exist_ok=True)
    need(directory.is_dir() and (not directory.is_symlink()) and (directory.stat().st_mode & 63 == 0), 'draft_checkpoint_not_private')
    target = directory / (inbound_id + '.' + str(attempt) + '.json')
    raw = canonical(value).encode()
    tmp = target.with_suffix('.tmp.' + uuid.uuid4().hex)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 384)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
        d = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(d)
        finally:
            os.close(d)
    finally:
        if tmp.exists():
            tmp.unlink()
    return sha(raw)

def checkpoint_read(directory, inbound_id, attempt):
    target = Path(directory) / (inbound_id + '.' + str(attempt) + '.json')
    try:
        fd = os.open(target, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, 'rb') as f:
        st = os.fstat(f.fileno())
        need(stat.S_ISREG(st.st_mode) and st.st_uid == os.geteuid() and (st.st_mode & 63 == 0) and (st.st_size <= 65536), 'draft_checkpoint_unsafe')
        raw = f.read(65537)
    value = json.loads(raw)
    need(canonical(value).encode() == raw, 'draft_checkpoint_corrupt')
    return (value, sha(raw))

class LiveWorker:

    def __init__(self, store, pipeline, policy, adapter=None):
        (self.store, self.pipeline, self.policy) = (store, pipeline, policy)
        self.adapter = adapter or DomesticAdapter(policy)

    def set_state(self, db, inbound_id, state, code=None):
        now = time.time()
        db.execute('UPDATE live_draft_jobs SET state=?,last_error=?,updated_at=? WHERE inbound_id=?', (state, code, now, inbound_id))
        db.execute('UPDATE inbound SET state=?,error_code=?,updated_at=? WHERE id=?', (state, code, now, inbound_id))

    def hold(self, db, inbound_id, code):
        self.set_state(db, inbound_id, 'draft_hold', code)
        db.execute("INSERT OR REPLACE INTO live_draft_scope VALUES('provider_hold',?)", (code,))

    def apply_checkpoint(self, db, j, a, cp, digest):
        need(cp['inbound_id'] == j['inbound_id'] and cp['attempt'] == a['attempt'] and (cp['policy_sha256'] == j['policy_sha256']) and (cp['prompt_sha256'] == a['prompt_sha256']) and (cp['model'] == a['model']), 'draft_checkpoint_binding_invalid')
        result = cp['result']
        need(cp['result_sha256'] == sha(canonical(result)), 'draft_checkpoint_binding_invalid')
        now = time.time()
        db.execute("UPDATE inbound SET state='draft_ready',draft=?,search_result=?,public_sources=?,timings=?,ready_at=?,updated_at=?,error_code=NULL,attempts=? WHERE id=? AND state='draft_processing'", (result['draft'], canonical(result['search_result']), canonical(result['public_sources']), canonical(result['timings']), now, now, a['attempt'], j['inbound_id']))
        db.execute("UPDATE live_draft_jobs SET state='draft_ready',ready_at=?,updated_at=?,last_error=NULL,checkpoint_sha256=? WHERE inbound_id=?", (now, now, digest, j['inbound_id']))
        db.execute("UPDATE live_draft_attempts SET status='complete',finished_at=?,metadata_json=?,response_sha256=? WHERE inbound_id=? AND attempt=?", (now, canonical(cp['metadata']), cp['response_sha256'], j['inbound_id'], a['attempt']))

    def recover(self):
        with self.store.connect() as db:
            rows = db.execute("SELECT * FROM live_draft_jobs WHERE state='draft_processing'").fetchall()
            for j in rows:
                a = db.execute('SELECT * FROM live_draft_attempts WHERE inbound_id=? ORDER BY attempt DESC LIMIT 1', (j['inbound_id'],)).fetchone()
                db.execute('BEGIN IMMEDIATE')
                try:
                    try:
                        cp = checkpoint_read(self.policy['checkpoint_dir'], j['inbound_id'], a['attempt']) if a else None
                    except (ValueError, OSError):
                        self.hold(db, j['inbound_id'], 'draft_checkpoint_invalid')
                        db.execute('COMMIT')
                        continue
                    if cp:
                        try:
                            self.apply_checkpoint(db, j, a, *cp)
                        except ValueError:
                            self.hold(db, j['inbound_id'], 'draft_checkpoint_invalid')
                    else:
                        self.hold(db, j['inbound_id'], 'draft_provider_outcome_unknown')
                    db.execute('COMMIT')
                except BaseException:
                    db.execute('ROLLBACK')
                    raise

    def once(self):
        root = Path(self.policy['checkpoint_dir']).parent
        root.mkdir(parents=True, exist_ok=True, mode=448)
        fd = os.open(root / 'worker.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 384)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return False
            self.recover()
            with self.store.connect() as db:
                if db.execute("SELECT 1 FROM live_draft_scope WHERE key='provider_hold'").fetchone():
                    return False
                row = db.execute("SELECT j.*,i.payload FROM live_draft_jobs j JOIN inbound i ON i.id=j.inbound_id WHERE j.state IN ('draft_queued','draft_waiting') AND j.next_attempt_at<=? ORDER BY j.created_at LIMIT 1", (time.time(),)).fetchone()
            if not row:
                return False
            payload = json.loads(row['payload'])
            try:
                context = self.pipeline.prepare(payload)
                (prompt, _) = build_prompt(payload, context['query'], context['approved'])
            except Exception:
                with self.store.connect() as db:
                    self.set_state(db, row['inbound_id'], 'draft_hold', 'draft_retrieval_unavailable')
                return True
            try:
                selected = self.adapter.select(prompt)
            except Exception:
                with self.store.connect() as db:
                    self.hold(db, row['inbound_id'], 'draft_model_selection_hold')
                return True
            if not selected or not selected.get('model'):
                return False
            now = time.time()
            attempt = row['attempts'] + 1
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                calls = db.execute('SELECT count(*) FROM live_draft_attempts WHERE started_at>=?', (int(now // 86400) * 86400,)).fetchone()[0]
                if calls >= self.policy['calls_per_day']:
                    db.execute('COMMIT')
                    return False
                need(row['policy_sha256'] == sha(canonical(self.policy)), 'draft_job_policy_changed')
                db.execute("INSERT INTO live_draft_attempts(inbound_id,attempt,status,model,started_at,prompt_sha256) VALUES(?,?,'provider_started',?,?,?)", (row['inbound_id'], attempt, selected['model'], now, sha(prompt)))
                self.set_state(db, row['inbound_id'], 'draft_processing')
                db.execute('UPDATE live_draft_jobs SET attempts=?,provider=?,model=? WHERE inbound_id=?', (attempt, 'dashscope', selected['model'], row['inbound_id']))
                db.execute('UPDATE inbound SET attempts=? WHERE id=?', (attempt, row['inbound_id']))
                db.execute('COMMIT')
            try:
                (raw, metadata) = self.adapter.complete(prompt, selected['model'])
                checkpoint_write(self.policy['checkpoint_dir'], row['inbound_id'] + '.response', attempt, {'raw': raw, 'metadata': metadata, 'prompt_sha256': sha(prompt), 'policy_sha256': row['policy_sha256']})
                parsed = parse_response(raw, context['approved'])
                draft = parsed['draft']
                context['search_result']['draft_review'] = parsed['review_notes']
                context['search_result']['selected_source_ids'] = parsed['source_ids']
                context['search_result']['review_required'] = review_assessment(parsed, context['approved'])
                result = {'draft': draft, 'search_result': context['search_result'], 'public_sources': context['public_sources'], 'timings': {'worker_elapsed_ms': round((time.time() - now) * 1000, 2)}}
                cp = {'inbound_id': row['inbound_id'], 'attempt': attempt, 'policy_sha256': row['policy_sha256'], 'prompt_sha256': sha(prompt), 'model': selected['model'], 'response_sha256': sha(raw), 'metadata': metadata, 'result': result, 'result_sha256': sha(canonical(result))}
                digest = checkpoint_write(self.policy['checkpoint_dir'], row['inbound_id'], attempt, cp)
                with self.store.connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    j = db.execute('SELECT * FROM live_draft_jobs WHERE inbound_id=?', (row['inbound_id'],)).fetchone()
                    a = db.execute('SELECT * FROM live_draft_attempts WHERE inbound_id=? AND attempt=?', (row['inbound_id'], attempt)).fetchone()
                    self.apply_checkpoint(db, j, a, cp, digest)
                    db.execute('COMMIT')
            except BaseException as exc:
                code = getattr(exc, 'code', str(exc))
                free = code == 'qwen_free_tier_exhausted'
                budget = code == 'assistant_daily_budget_exhausted'
                safe = 'draft_free_quota_exhausted' if free else 'draft_shared_budget_wait' if budget else 'draft_provider_or_validation_hold'
                with self.store.connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    if not checkpoint_read(self.policy['checkpoint_dir'], row['inbound_id'], attempt):
                        if free or budget:
                            self.set_state(db, row['inbound_id'], 'draft_waiting', safe)
                            db.execute('UPDATE live_draft_jobs SET next_attempt_at=? WHERE inbound_id=?', (time.time() + (60 if free else 3600), row['inbound_id']))
                        elif isinstance(exc, ValueError) and str(exc).startswith(('draft_schema_', 'draft_text_', 'draft_sources_', 'draft_notes_')):
                            self.set_state(db, row['inbound_id'], 'draft_hold', str(exc))
                        else:
                            self.hold(db, row['inbound_id'], safe)
                        db.execute('UPDATE live_draft_attempts SET status=?,finished_at=?,error_code=? WHERE inbound_id=? AND attempt=?', ('quota_rejected' if free else 'not_called_budget' if budget else 'failed_hold', time.time(), safe, row['inbound_id'], attempt))
                    db.execute('COMMIT')
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
            return True
        finally:
            os.close(fd)

class DomesticAdapter:
    """Existing China-only transport + unchanged shared reservation, independent pool."""

    def __init__(self, policy):
        import sys
        for p in (os.environ.get('ICBU_EXAMPLE_PATH_07', ''), os.environ.get('ICBU_EXAMPLE_PATH_08', '')):
            if p not in sys.path:
                sys.path.insert(0, p)
        from domestic_digest import client
        client.INVENTORY_THINKING = {**client.INVENTORY_THINKING, **THINKING}
        from supplier_knowledge.icbu_quota_only_models_v1 import QwenFreeMatrix
        self.policy = policy
        self.matrix = QwenFreeMatrix(policy['matrix_policy'])

    def boundary(self):
        for (path, digest) in self.policy['dependency_pins'].items():
            need(sha(Path(path).read_bytes()) == digest, 'draft_dependency_changed')

    def select(self, prompt):
        self.boundary()
        return self.matrix.select_model_readonly(prompt, RESPONSE_SCHEMA, max_output_tokens=OUTPUT)

    def complete(self, prompt, model):
        self.boundary()
        from domestic_cli import get_key
        from video_knowledge.assistant_models import complete_structured
        (key, _) = get_key('qwen')
        os.environ['DASHSCOPE_API_KEY'] = key
        cfg = json.loads(Path(os.environ.get('ICBU_EXAMPLE_PATH_09', '')).read_bytes())
        from video_knowledge.analysis_budget_policy import effective_policy
        cfg = effective_policy(cfg, 'icbu_live_drafts')
        need(cfg['model_ledger'] == self.policy['shared_usage_path'], 'draft_shared_ledger_mismatch')
        if cfg.get('dashscope_tls_preferred_ip'):
            os.environ['DASHSCOPE_TLS_PREFERRED_IP'] = cfg['dashscope_tls_preferred_ip']
        else:
            os.environ.pop('DASHSCOPE_TLS_PREFERRED_IP', None)
        os.environ['NO_PROXY'] = 'localhost,127.0.0.1,api.deepseek.com,dashscope.aliyuncs.com'
        os.environ['no_proxy'] = os.environ['NO_PROXY']
        captured = {}

        def completion(p, s, **kw):
            result = self.matrix.completion(p, s, provider='dashscope', preferred_model=model, max_output_tokens=OUTPUT, timeout_seconds=45, role='structured')
            captured.update(result['metadata'])
            return result
        db = sqlite3.connect(self.policy['shared_usage_path'], timeout=5)
        try:
            (content, meta) = complete_structured(db, cfg, prompt, RESPONSE_SCHEMA, lambda s: s, provider='dashscope', completion=completion)
            need(meta['provider'] == 'dashscope' and meta['actual_model'] == model, 'draft_model_mismatch')
            usage = meta['usage']
            need(type(usage) is dict and set(usage) == {'prompt_tokens', 'completion_tokens', 'total_tokens'} and all((type(v) is int and v >= 0 for v in usage.values())) and (usage['total_tokens'] == usage['prompt_tokens'] + usage['completion_tokens']) and (usage['completion_tokens'] <= OUTPUT), 'draft_usage_invalid')
            need(captured.get('provider') == 'dashscope' and captured.get('requested_model') == captured.get('actual_model') == model and (captured.get('json_mode') == 'json_object') and (captured.get('requested_output_tokens') == OUTPUT) and (captured.get('thinking_disabled_requested') is (THINKING[model] != 'omit')), 'draft_protocol_invalid')
            meta.update({k: captured[k] for k in ('requested_model', 'json_mode', 'requested_output_tokens', 'reservation_tokens', 'thinking_disabled_requested')})
            meta['thinking_protocol'] = THINKING[model]
            return (content, meta)
        finally:
            db.close()

def load_policy(path, read_restricted):
    if path is None or not Path(path).exists():
        return None
    value = json.loads(read_restricted(path, max_bytes=65536))
    need(type(value) is dict and set(value) == {'schema', 'account', 'cutover_utc', 'model_codes', 'max_output_tokens', 'calls_per_day', 'checkpoint_dir', 'shared_usage_path', 'matrix_policy', 'technical_compatibility', 'dependency_pins', 'auto_send', 'operations_mode', 'auto_send_conversations', 'pricing_rule', 'knowledge_sources'}, 'draft_policy_invalid')
    need(value['schema'] == 'icbu-live-drafts-v1' and value['account'] == os.environ.get('ICBU_EXAMPLE_ACCOUNT', 'example-account') and (value['model_codes'] == list(MODELS)) and (value['max_output_tokens'] == OUTPUT) and (type(value['calls_per_day']) is int) and (1 <= value['calls_per_day'] <= 1000000), 'draft_policy_invalid')
    need(type(value['auto_send']) is bool and value['operations_mode'] in ('review', 'test_auto', 'auto'), 'draft_operations_policy_invalid')
    need(type(value['auto_send_conversations']) is list and all((isinstance(x, str) and x.startswith('tm:') for x in value['auto_send_conversations'])), 'draft_auto_scope_invalid')
    need(value['knowledge_sources']['authorization'] == 'P8-OPS-20261025-r1' and isinstance(value['knowledge_sources']['candidate_spec_grants'], list), 'draft_knowledge_grant_invalid')
    need(value['pricing_rule'].get('mode') in ('historical_only', 'gross_margin', 'markup'), 'draft_pricing_invalid')
    utc(value['cutover_utc'])
    need(value['checkpoint_dir'] == os.environ.get('ICBU_EXAMPLE_PATH_10', '') and value['shared_usage_path'] == os.environ.get('ICBU_EXAMPLE_PATH_11', ''), 'draft_policy_path_invalid')
    m = value['matrix_policy']
    need(m['rotation'] == 'quota_only' and m['quota_only_order'] == list(MODELS) and (m['max_output_tokens'] == OUTPUT) and (m['ledger_path'] == os.environ.get('ICBU_EXAMPLE_PATH_12', '')) and (m['inventory_path'] == os.environ.get('ICBU_EXAMPLE_PATH_13', '')) and all((v == 0 for v in m['quota_baseline_tokens'].values())), 'draft_matrix_invalid')
    need(set(value['technical_compatibility']) == set(MODELS), 'draft_compatibility_missing')
    for (model, proof) in value['technical_compatibility'].items():
        r = proof['receipt']
        need(sha(canonical(r)) == proof['receipt_sha256'] and r['actual_model'] == r['requested_model'] == model and (r['requested_thinking_contract'] == THINKING[model]) and (r['source'] == 'fixed_synthetic_no_customer') and (r['physical_requests'] == 1) and (r['output_exact_ok'] is True), 'draft_compatibility_invalid')
    return value

def safe_status(database, policy):
    """No schema creation, content reads, credential access, or provider calls."""
    db = sqlite3.connect('file:' + str(Path(database).resolve()) + '?mode=ro', uri=True)
    try:
        db.execute('PRAGMA query_only=ON')
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        jobs = [] if 'live_draft_jobs' not in tables else db.execute('SELECT state,synthetic,count(*) FROM live_draft_jobs GROUP BY state,synthetic').fetchall()
        attempts = [] if 'live_draft_attempts' not in tables else db.execute('SELECT status,count(*) FROM live_draft_attempts GROUP BY status').fetchall()
        hold = None if 'live_draft_scope' not in tables else db.execute("SELECT value FROM live_draft_scope WHERE key='provider_hold'").fetchone()
        return {'schema': 'icbu-live-draft-status-v1', 'jobs_by_state': jobs, 'attempts_by_state': attempts, 'provider_hold': hold[0] if hold else None, 'cutover_utc': policy['cutover_utc'] if policy else None, 'models': list(MODELS), 'calls_per_day': policy['calls_per_day'], 'max_output_tokens': OUTPUT, 'auto_send': policy['auto_send'], 'provider_calls': 0}
    finally:
        db.close()
PRIORITY_PATH = Path(os.environ.get('ICBU_EXAMPLE_PATH_14', ''))

def publish_priority(store, policy, *, path=PRIORITY_PATH, clock=time.time):
    """Publish only a fresh count; media never opens the live inbound WAL database."""
    need(os.geteuid() == 995, 'draft_priority_wrong_uid')
    path = Path(path)
    need(not any((p.is_symlink() for p in (path, *path.parents))), 'draft_priority_path_unsafe')
    if path.exists():
        st = path.lstat()
        need(stat.S_ISREG(st.st_mode) and st.st_uid == 995 and (st.st_nlink == 1) and (stat.S_IMODE(st.st_mode) == 384), 'draft_priority_path_unsafe')
    with store.connect() as db:
        pending = db.execute("SELECT count(*) FROM live_draft_jobs WHERE synthetic=0 AND state IN ('draft_queued','draft_processing','draft_waiting')").fetchone()[0]
    stamp = clock()
    need(type(pending) is int and pending >= 0 and (type(stamp) in (int, float)) and math.isfinite(stamp) and (stamp > 0), 'draft_priority_invalid')
    raw = canonical({'schema': 'icbu-live-draft-priority-v1', 'generated_at': stamp, 'pending_real_drafts': pending, 'policy_sha256': sha(canonical(policy))}).encode()
    (fd, tmp) = tempfile.mkstemp(prefix='.draft-priority-', dir=path.parent)
    try:
        os.fchmod(fd, 384)
        with os.fdopen(fd, 'wb') as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if Path(tmp).exists():
            Path(tmp).unlink()

def main(argv=None):
    import argparse, signal, threading
    from icbu_inbound_receiver import Store, Pipeline, read_restricted, load_public_facts
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--status', action='store_true')
    modes.add_argument('--once', action='store_true')
    modes.add_argument('--daemon', action='store_true')
    parser.add_argument('--db', default=os.environ.get('ICBU_EXAMPLE_PATH_15', ''))
    parser.add_argument('--policy', default=POLICY_PATH)
    parser.add_argument('--public-facts', default=os.environ.get('ICBU_EXAMPLE_PATH_16', ''))
    parser.add_argument('--interval', type=float, default=5)
    args = parser.parse_args(argv)
    need(1 <= args.interval <= 60, 'draft_interval_invalid')
    os.umask(63)
    policy = load_policy(args.policy, read_restricted)
    need(policy is not None, 'draft_policy_missing')
    if args.status:
        print(canonical(safe_status(args.db, policy)))
        return 0
    adapter = DomesticAdapter(policy)
    adapter.boundary()
    store = Store(args.db, live_policy=policy)
    pipeline = KnowledgePipeline(policy)
    worker = LiveWorker(store, pipeline, policy, adapter)
    if args.once:
        worker.once()
        print(canonical(safe_status(args.db, policy)))
        return 0
    stopping = threading.Event()

    def stop(*_):
        stopping.set()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while not stopping.is_set():
        try:
            publish_priority(store, policy)
        except Exception:
            print('{"event":"draft_priority_projection_failed","auto_send":false}', flush=True)
        try:
            worker.once()
            from icbu_operations import Operations
            Operations().enqueue_automatic(policy)
        except Exception:
            print('{"event":"draft_worker_tick_failed","auto_send":false}', flush=True)
        stopping.wait(args.interval)
    return 0
if __name__ == '__main__':
    raise SystemExit(main())
