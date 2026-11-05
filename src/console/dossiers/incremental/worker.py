"""Durable discovery, leased jobs, per-call budget reservations and atomic projection."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
import uuid

from ..service import Dossiers
from ..store import DB_PATH, readonly as dossier_readonly
from .model import AiTask, FLASH, PRO, ModelFailure, prompt_for, prompt_hash
from .ocr import ARCHIVE_ROOT, OCR
from .project import project
from .schema import REVISION, dumps, migrate
from .sources import LOCAL_TZ, WECHAT_PATH, digest, enrich_ocr, identity, scan, windows


def utcnow():
    return datetime.now(timezone.utc)


def iso(value):
    return value.astimezone(timezone.utc).isoformat(timespec='seconds')


@contextmanager
def transaction(db):
    db.execute('BEGIN IMMEDIATE')
    try:
        yield
        db.execute('COMMIT')
    except BaseException:
        db.execute('ROLLBACK')
        raise


def changeset():
    return dict(added=[], sources=[], products=[], suppliers=[], offers=[], gap_fills=[], reviews=[])


def coverage(sources):
    unique = {s['source_id']: s for s in sources}
    return dict(sources=len(unique), no_text=sum(s['parse_state'] == 'no_text' for s in unique.values()),
                ocr=sum(json.loads(s['coverage_json']).get('method') == 'ocr' for s in unique.values()),
                attachments=sum(s['kind'] != 'message' for s in unique.values()),
                truncated=sum(bool(json.loads(s['coverage_json']).get('truncated')) for s in unique.values()))


class Incremental:
    def __init__(self, database=DB_PATH, wechat=WECHAT_PATH, *, runner=None, now=utcnow,
                 archive_root=ARCHIVE_ROOT, ocr_engine_factory=None):
        self.database, self.wechat = Path(database), Path(wechat)
        self.model, self.now = AiTask(runner=runner), now
        self.archive_root, self.ocr_engine_factory = archive_root, ocr_engine_factory

    def run(self, *, dry_run=False, report=None, max_windows=None, daily_budget_tokens=300000, since_rowid=None,
            ocr=True, max_ocr=200):
        if since_rowid is not None and (not dry_run or since_rowid < 0):
            raise ValueError('since_rowid_requires_dry_run')
        if daily_budget_tokens < 0 or max_ocr < 0 or (max_windows is not None and max_windows < 0):
            raise ValueError('invalid_limit')
        if report is not None:
            # Fail before spending model quota if a report cannot be saved.
            report_path = Path(report)
            report_path.mkdir(mode=0o700, parents=True, exist_ok=True)
            probe = report_path / ('.incremental-probe-' + uuid.uuid4().hex)
            fd = os.open(probe, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
            probe.unlink()
        session = uuid.uuid4().hex
        started = self.now()
        result = dict(session_id=session, dry_run=dry_run, started_at=iso(started),
                      initialized=False, processed_windows=0, succeeded_windows=0, failed_windows=0,
                      calls=0, tokens=0, reported_tokens=0, unmetered_calls=0, budget_exhausted=False,
                      discovered_windows=0, skipped_messages=0, changes=changeset(), failures=[], coverage={})
        # Production inputs are NEVER opened for write in dry-run. The entire
        # simulation (including cursor, budget and subsequent-window state) lives in RAM.
        with dossier_readonly(self.database) as source:
            Dossiers.check(source)
            if dry_run:
                db = sqlite3.connect(':memory:', isolation_level=None)
                source.backup(db)
        if not dry_run:
            db = sqlite3.connect(self.database.resolve(strict=True).as_uri() + '?mode=rw', uri=True, isolation_level=None, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        adapter = 'wechat:messages:' + digest(str(self.wechat.resolve(strict=True)))[:16]
        try:
            recognizer = OCR(db, root=self.archive_root, engine_factory=self.ocr_engine_factory,
                             max_ocr=max_ocr, persist=not dry_run)
            with transaction(db):
                migrate(db)
                cursor = db.execute('SELECT watermark_json FROM sd_cursor WHERE adapter_id=?', (adapter,)).fetchone()
                previous = json.loads(cursor[0]) if cursor else None
                after = since_rowid if since_rowid is not None else previous['rowid'] if previous else None
                found = scan(db, self.wechat, after=after, now=iso(started), expected=previous if since_rowid is None else None)
                result['initialized'] = previous is None and since_rowid is None
                result['skipped_messages'] = found['skipped']
                if ocr:
                    enrich_ocr(found['records'], recognizer, iso(started))
                result['ocr'] = recognizer.stats
                result['discovered_coverage'] = coverage([s for r in found['records'] for s in r['sources']])
                for window in windows(found['records']):
                    ref = identity('IW', [window['supplier_id'], window['day'], window['rowids']])
                    revision = digest(dumps([(s['source_id'], s['text']) for s in window['sources']]))
                    job = identity('IJ', [ref, revision, REVISION])
                    added = db.execute('INSERT OR IGNORE INTO sd_job(job_id,stage,input_ref,input_revision,tool_revision,state,not_before,cursor_json) VALUES(?,?,?,?,?,?,?,?)',
                        (job, 'window', ref, revision, REVISION, 'pending', iso(started), dumps(window))).rowcount
                    result['discovered_windows'] += added
                watermark = dumps(found['watermark'])
                db.execute('INSERT INTO sd_cursor VALUES(?,?,?,?) ON CONFLICT(adapter_id) DO UPDATE SET watermark_json=excluded.watermark_json,last_source_hash=excluded.last_source_hash,reconciled_at=excluded.reconciled_at',
                           (adapter, watermark, found['watermark']['anchor'], iso(started)))
            ready = db.execute("SELECT job_id FROM sd_job WHERE tool_revision=? AND ((state IN ('pending','failed','budget_wait') AND not_before<=?) OR (state='running' AND lease_until<=?)) ORDER BY not_before, rowid",
                               (REVISION, iso(self.now()), iso(self.now()))).fetchall()
            for job_ref in ready:
                if max_windows is not None and result['processed_windows'] >= max_windows:
                    break
                jobid = job_ref['job_id']
                with transaction(db):
                    now = self.now()
                    claimed = db.execute("UPDATE sd_job SET state='running',attempts=attempts+1,started_at=?,lease_until=?,error_code=NULL WHERE job_id=? AND ((state IN ('pending','failed','budget_wait') AND not_before<=?) OR (state='running' AND lease_until<=?))",
                        (iso(now), iso(now + timedelta(minutes=10)), jobid, iso(now), iso(now))).rowcount
                    job = db.execute('SELECT * FROM sd_job WHERE job_id=?', (jobid,)).fetchone()
                    if claimed:
                        # A previous worker may have died after dispatch. Keep
                        # its reservation charged and make the unknown outcome explicit.
                        db.execute("UPDATE sd_run SET state='failed',failure_code='lease_expired_outcome_unknown',finished_at=? WHERE job_id=? AND state='running'", (iso(now), jobid))
                if not claimed:
                    continue
                result['processed_windows'] += 1
                window = json.loads(job['cursor_json'])
                prompt = prompt_for(window['sources'])
                active = any(s['text'].strip() and s['direction'] == 'incoming' for s in window['sources']) and not window['merge_conflict']
                runid, meta, extraction = None, {}, dict(offers=[])
                try:
                    if active:
                        for model in (FLASH, PRO):
                            runid = self.reserve(db, jobid, session, model, prompt, daily_budget_tokens)
                            if runid is None:
                                result['budget_exhausted'] = True
                                break
                            result['calls'] += 1
                            try:
                                extraction, meta = self.model.extract(prompt, model)
                            except ModelFailure as exc:
                                meta = exc.metadata
                                raise
                            if model == FLASH and extraction.get('difficult'):
                                with transaction(db):
                                    self.finish(db, runid, meta, 'completed', None)
                                runid = None
                                continue
                            if model == PRO and extraction.get('difficult'):
                                raise ModelFailure('extraction_unresolved', meta)
                            break
                        if result['budget_exhausted']:
                            break
                    local = changeset()
                    with transaction(db):
                        project(db, jobid, window, extraction['offers'], iso(self.now()), local)
                        if runid:
                            self.finish(db, runid, meta, 'completed', None)
                        db.execute("UPDATE sd_job SET state='completed',lease_until=NULL,finished_at=?,error_code=NULL WHERE job_id=?", (iso(self.now()), jobid))
                    for key in local:
                        result['changes'][key].extend(local[key])
                    result['succeeded_windows'] += 1
                except (ModelFailure, sqlite3.Error, ValueError, TypeError, KeyError, ArithmeticError) as exc:
                    # Fixed failure codes only. Never copy business text, traceback or stderr.
                    code = exc.code if isinstance(exc, ModelFailure) else 'projection_invalid'
                    with transaction(db):
                        if runid:
                            self.finish(db, runid, meta, 'failed', code)
                        retry = self.now() + timedelta(seconds=min(86400, 60 * 2 ** min(job['attempts'] - 1, 10)))
                        db.execute("UPDATE sd_job SET state='failed',error_code=?,not_before=?,lease_until=NULL,finished_at=? WHERE job_id=?",
                                   (code, iso(retry), iso(self.now()), jobid))
                    result['failed_windows'] += 1
                    result['failures'].append(dict(job_id=jobid, failure_code=code))
            rows = db.execute('SELECT * FROM sd_run WHERE session_id=? ORDER BY started_at,rowid', (session,)).fetchall()
            result['tokens'] = sum(r['charged_tokens'] for r in rows)
            result['reported_tokens'] = sum(r['total_tokens'] or 0 for r in rows)
            result['unmetered_calls'] = sum(r['total_tokens'] is None for r in rows)
            result['runs'] = [dict(r) for r in rows]
            result['pending_windows'] = db.execute("SELECT count(*) FROM sd_job WHERE state<>'completed'").fetchone()[0]
            result['daily_charged_tokens'] = db.execute('SELECT coalesce(sum(charged_tokens),0) FROM sd_run WHERE budget_day=?',
                (self.now().astimezone(LOCAL_TZ).date().isoformat(),)).fetchone()[0]
            source_ids = {v['key'][0] for v in result['changes']['added'] if v['table'] == 'sd_source'}
            source_rows = [db.execute('SELECT source_id,kind,parse_state,coverage_json FROM sd_source WHERE source_id=?', (sid,)).fetchone() for sid in source_ids]
            result['coverage'] = coverage(source_rows)
            result['finished_at'] = iso(self.now())
        finally:
            db.close()
        if report is not None:
            write_report(report, result)
        return result

    def reserve(self, db, jobid, session, model, prompt, limit):
        # UTF-8 bytes conservatively cover input tokens; allowance covers output.
        # ai-task has no max-output-token option. Actual overages remain charged.
        reservation = len(prompt.encode()) + 8192
        now = self.now()
        day = now.astimezone(LOCAL_TZ).date().isoformat()
        with transaction(db):
            spent = db.execute('SELECT coalesce(sum(charged_tokens),0) FROM sd_run WHERE budget_day=?', (day,)).fetchone()[0]
            if spent + reservation > limit:
                tomorrow = (now.astimezone(LOCAL_TZ) + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
                db.execute("UPDATE sd_job SET state='budget_wait',error_code='budget_exhausted',not_before=?,lease_until=NULL WHERE job_id=?", (iso(tomorrow), jobid))
                return None
            runid = uuid.uuid4().hex
            db.execute('INSERT INTO sd_run(run_id,job_id,session_id,budget_day,provider,requested_model,tool_revision,prompt_sha256,state,charged_tokens,reserved_tokens,usage_json,started_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (runid, jobid, session, day, 'bailian_free', model, REVISION, prompt_hash(prompt), 'running', reservation, reservation, '{}', iso(now)))
            return runid

    def finish(self, db, runid, meta, state, code):
        reserved = db.execute('SELECT reserved_tokens FROM sd_run WHERE run_id=?', (runid,)).fetchone()[0]
        total = meta.get('total_tokens')
        db.execute('UPDATE sd_run SET actual_model=?,state=?,failure_code=?,prompt_tokens=?,completion_tokens=?,total_tokens=?,charged_tokens=?,usage_json=?,finished_at=? WHERE run_id=?',
            (meta.get('actual_model'), state, code, meta.get('prompt_tokens'), meta.get('completion_tokens'), total,
             total if total is not None else reserved, meta.get('usage_json', '{}'), iso(self.now()), runid))


def write_report(directory, result):
    path = Path(directory)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    summary = numeric_summary(result)
    text = ('# 供应商档案增量演练\n\n' + ('只读演练，线上库未写入。\n\n' if result['dry_run'] else '增量执行报告。\n\n') +
            '\n'.join(f'- {k}：{v}' for k, v in [
                ('处理窗口', summary['processed_windows']), ('成功窗口', result['succeeded_windows']),
                ('失败窗口', result['failed_windows']), ('模型调用', result['calls']),
                ('已报告 token', result['reported_tokens']), ('记账 token（含未知用量预留）', result['tokens']),
                ('未知用量调用', result['unmetered_calls']), ('拟新增报价' if result['dry_run'] else '新增报价', summary['new_offers']),
                ('补齐缺口', summary['gap_fills']), ('人工异常', summary['reviews']),
                ('OCR 识别张数', result['ocr']['attempted']), ('OCR 缓存命中', result['ocr']['cache_hits']),
                ('OCR 耗时秒', result['ocr']['seconds']), ('OCR 文本来源', result['coverage']['ocr']),
                ('无文本来源', result['coverage']['no_text']), ('预算停止', result['budget_exhausted'])]) +
            '\n\n完整新增行、替代关系、缺口补齐、异常和模型账单见同名 JSON。未知税口径使用旧表要求的 unknown，税率为 NULL。\n')
    for suffix, content in [('.json', json.dumps(result, ensure_ascii=False, indent=2)), ('.md', text)]:
        target = path / (result['session_id'] + suffix)
        # Do not follow or overwrite an existing report/symlink.
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(content)


def numeric_summary(result):
    return {**{k: result[k] for k in ('session_id', 'dry_run', 'processed_windows', 'failed_windows', 'calls', 'tokens', 'reported_tokens', 'unmetered_calls', 'budget_exhausted')},
            'new_offers': len(result['changes']['offers']), 'gap_fills': len(result['changes']['gap_fills']), 'reviews': len(result['changes']['reviews']),
            'ocr': result['ocr'], 'coverage': result['coverage'], 'discovered_coverage': result['discovered_coverage']}
