"""Resumable map/reduce, bounded daily admissions and failure-isolated projection."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
import uuid

from .model import AiTask, ModelFailure, bounded, clean_answer, estimate, map_prompt, reduce_prompt, stage_for
from .schema import dumps, profile_view, save_profile
from .store import DB_PATH, working_database
from .windows import digest, normalise, priority, rule_kind, windows


class BudgetStop(Exception):
    pass


def base_profile(conv, messages, now):
    times = [m['sent_at'] for m in messages]
    return dict(conversation_key=conv['key'], channel=conv['channel'], title=conv.get('buyer_name') or '',
                kind='unknown', customer_stage=None, org_name=None, people_json=[], country=None,
                country_name=None, language=None, products_json=[], summary='暂未读完，身份待确认', evidence_json=[],
                confidence=0., messages_read=0, messages_total=len(messages), first_at=min(times, default=None),
                last_at=max(times, default=None), last_inbound_at=max((m['sent_at'] for m in messages if m['role'] == 'buyer'), default=None),
                state='partial', error_code=None, model=None, updated_at=now)


def report_directory(path):
    if path is None:
        return None
    path = Path(path).resolve()
    repo = Path(__file__).resolve().parents[3]
    approved = Path('/srv/imgw-dev/ops/private_out/profiles').resolve()
    if (path == repo or repo in path.parents) and path != approved and approved not in path.parents:
        raise ValueError('报告目录必须在仓库外或任务指定的私有目录')
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Preflight before reading sources or making calls; never retain prompts in reports.
    probe = path / ('.profiles-check-' + uuid.uuid4().hex)
    fd = os.open(probe, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    probe.unlink()
    return path


def preserve_events(answer, parts):
    """Keep a representative of each proven milestone through every reduce level."""
    required = {}
    for part in parts:
        for e in part['evidence']:
            if e['claim'] in ('inquiry', 'quoted', 'ordered', 'paid', 'shipped', 'after_sales'):
                old = required.get(e['claim'])
                if old is None or (e['sent_at'] < old['sent_at'] if e['claim'] in ('ordered', 'paid', 'shipped') else e['sent_at'] > old['sent_at']):
                    required[e['claim']] = e
    evidence = list(required.values())
    evidence += [e for e in answer['evidence'] if e not in evidence]
    answer['evidence'] = evidence[:12]
    return answer


OUTSIDE_BUSINESS = frozenset({'peer', 'industry', 'personal', 'system'})


TRADE_CLAIMS = frozenset({'inquiry', 'quoted', 'ordered', 'paid', 'shipped', 'after_sales'})


def no_trade(answer):
    return (answer.get('kind') in OUTSIDE_BUSINESS and (answer.get('confidence') or 0) >= 0.6 or
            answer.get('kind') == 'unknown' and not any(e.get('claim') in TRADE_CLAIMS for e in answer.get('evidence') or []))


def settled_outside_business(old):
    if not old or (old.get('messages_read') or 0) < 200:
        return False
    evidence = old.get('evidence') or old.get('evidence_json') or []
    if isinstance(evidence, str):
        try:
            evidence = json.loads(evidence)
        except ValueError:
            evidence = []
    return no_trade(dict(kind=old.get('kind'), confidence=old.get('confidence'), evidence=evidence))


def dossier_hints(dossiers_path=None, archive_path=None):
    """Known supplier work groups from the supplier dossiers, as a short prompt hint."""
    try:
        from console.archive.store import ARCHIVE_PATH
        from console.roles import dossier_links
        links = dossier_links.load(dossiers_path, archive_path=archive_path or ARCHIVE_PATH)
    except Exception:
        return {}
    label = dict(supplier='供应商', forwarder='货代', vendor='配套服务商')
    hints = {}
    for link in links:
        text = '供应商档案记录：该群是%s「%s」的工作群。' % (label.get(link['kind'], '供应商'), link['name'][:60])
        hints[link['conversation_key']] = hints.get(link['conversation_key'], '') + text
    return hints


class Worker:
    def __init__(self, path=DB_PATH, *, inbox=None, runner=None, clock=None, hints=None):
        self.path, self.inbox, self.clock = Path(path), inbox, clock or time.time
        self.task = AiTask(runner)
        self.conv = None
        # Production reads supplier dossier hints; an injected inbox (tests) gets none unless given.
        self.hints = hints if hints is not None else (None if inbox is None else {})

    def _hints(self):
        if self.hints is None:
            self.hints = dossier_hints()
        return self.hints

    def _call(self, db, run_id, prompt, sources, now, budget, allowed=None):
        reserve = estimate(prompt)
        called_at = self.clock()
        day = datetime.fromtimestamp(called_at, timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        spent = db.execute('SELECT coalesce(sum(charged_tokens),0) FROM cp_call WHERE at>=?', (day,)).fetchone()[0]
        if spent + reserve > budget:
            raise BudgetStop()
        with db:
            call = db.execute('INSERT INTO cp_call(run_id,at,reserved_tokens,charged_tokens,state) VALUES(?,?,?,?,?)',
                              (run_id, called_at, reserve, reserve, 'reserved')).lastrowid
        meta = {}
        try:
            value, meta = self.task.extract(prompt)
            answer = clean_answer(value, sources, allowed_evidence=allowed)
        except ModelFailure as error:
            meta = error.metadata or meta
            with db:
                db.execute('UPDATE cp_call SET state=?,reported_tokens=?,charged_tokens=? WHERE id=?',
                           ('failed', meta.get('total_tokens'), max(reserve, meta.get('total_tokens') or 0), call))
            raise
        else:
            total = meta.get('total_tokens')
            with db:
                db.execute('UPDATE cp_call SET state=?,reported_tokens=?,charged_tokens=? WHERE id=?',
                           ('ready', total, total if total is not None else reserve, call))
            return answer, meta.get('actual_model') or 'auto'

    def _reduce(self, db, run_id, parts, sources, now, budget):
        # Hierarchical bounded reduce; successful intermediate nodes are reusable after budget stops.
        if len(parts) == 1:
            return parts[0], None
        model = None
        while len(parts) > 1:
            batches, batch = [], []
            for part in parts:
                if batch and not bounded(reduce_prompt(batch + [part], self.conv)):
                    batches.append(batch)
                    batch = []
                if not bounded(reduce_prompt([part], self.conv)):
                    raise ModelFailure('reduce_too_large')
                batch.append(part)
            if batch:
                batches.append(batch)
            if len(batches) == len(parts):
                raise ModelFailure('reduce_too_large')
            next_parts = []
            for batch in batches:
                if len(batch) == 1:
                    next_parts.append(batch[0])
                    continue
                fingerprint = digest(batch)
                cached = db.execute('SELECT * FROM cp_reduce WHERE fingerprint=?', (fingerprint,)).fetchone()
                if cached:
                    answer, model = json.loads(cached['result_json']), cached['model']
                else:
                    allowed = [e for part in batch for e in part['evidence']]
                    answer, model = self._call(db, run_id, reduce_prompt(batch, self.conv), sources, now, budget, allowed)
                    answer = preserve_events(answer, batch)
                    with db:
                        db.execute('INSERT OR IGNORE INTO cp_reduce VALUES(?,?,?,?)', (fingerprint, dumps(answer), model, now))
                next_parts.append(answer)
            parts = next_parts
        return parts[0], model

    def run(self, *, dry_run=False, report=None, max_conversations=20, max_windows=30,
            daily_budget_tokens=1000000, channel=None, only=()):
        if min(max_conversations, max_windows, daily_budget_tokens) < 0:
            raise ValueError('数量和预算不能为负数')
        if isinstance(only, str):
            only = (only,)
        report = report_directory(report)
        if self.inbox is None:
            from .sources import build_inbox
            self.inbox = build_inbox(channel=channel)
        now = self.clock()
        snapshot = self.inbox.read(now)
        result = dict(conversations=0, windows=0, failed_windows=0, failed_conversations=0, rules=0,
                      skipped=0, missing=0, source_ready=bool(snapshot['ready']), budget_stopped=False,
                      calls=0, charged_tokens=0, reported_tokens=0, profiles=[])
        convs = [c for c in snapshot['conversations'].values()
                 if c['channel'] in ('wechat', 'alibaba', 'whatsapp') and (not channel or c['channel'] == channel)
                 and (not only or c['key'] in only)]
        result['missing'] = len(set(only) - {c['key'] for c in convs})
        with working_database(self.path, dry_run) as db:
            with db:
                run_id = db.execute('INSERT INTO cp_run(started_at) VALUES(?)', (now,)).lastrowid
            previous = {r['conversation_key']: profile_view(r) for r in db.execute('SELECT * FROM cp_profile')}
            convs.sort(key=lambda c: (previous.get(c['key'], {}).get('updated_at', 0), -c['last_seen'], c['key']))
            for conv in convs:
                if result['conversations'] >= max_conversations:
                    break
                key, old = conv['key'], previous.get(conv['key'])
                cursor = db.execute('SELECT * FROM cp_cursor WHERE conversation_key=?', (key,)).fetchone()
                # A metadata-only listing cannot be treated as the full message history.
                try:
                    conv = self.inbox.detail(key, now)
                    if conv is None or conv.get('history_complete') is False:
                        raise ValueError
                    messages = normalise(conv['messages'])
                    conv = dict(conv, known=self._hints().get(key))
                    self.conv = conv
                except Exception:
                    result['failed_conversations'] += 1
                    result['conversations'] += 1
                    if old is None:
                        failed = base_profile(next(c for c in convs if c['key'] == key), [], now)
                        failed.update(state='failed', error_code='history_unavailable')
                        with db:
                            save_profile(db, failed)
                        result['profiles'].append(failed)
                    else:
                        result['profiles'].append(old)
                    continue
                total = len(messages)
                changed = cursor and (total < cursor['total'] or digest(messages[:cursor['total']]) != cursor['prefix_hash'])
                pending = db.execute("SELECT count(*) FROM cp_window WHERE conversation_key=? AND state!='ready'", (key,)).fetchone()[0]
# A clearly non-business group (AI tools chat, family) is not read to the end:
                # only new messages are checked so its kind can still change later.
                settled = settled_outside_business(old)
                due = (not cursor or changed or (pending and not settled) or not old or old['state'] == 'failed' or
                       (old['state'] == 'partial' and old['messages_read'] < cursor['total'] and not settled) or
                       total - cursor['total'] >= 30 or now - cursor['checked_at'] >= 7 * 86400)
                # Unprocessed sub-threshold appends are visible, without resetting the seven-day clock.
                if not due:
                    result['skipped'] += 1
                    if old and old['messages_total'] != total:
                        metadata = base_profile(conv, messages, now)
                        old.update({k: metadata[k] for k in ('messages_total', 'first_at', 'last_at', 'last_inbound_at', 'title')})
                        old.update(state='partial', updated_at=now)
                        with db:
                            save_profile(db, old)
                    if old:
                        result['profiles'].append(old)
                    continue
                # Do not let partial caused only by <30 fresh messages defeat the increment threshold.
                new_due = (not cursor or changed or total - cursor['total'] >= 30 or now - cursor['checked_at'] >= 7 * 86400)
                result['conversations'] += 1
                kind = rule_kind(conv, messages)
                if kind:
                    profile = base_profile(conv, messages, now)
                    profile.update(kind=kind, state='skipped', messages_read=total, confidence=.95,
                                   summary='系统通知或公众号类会话' if kind == 'system' else '家庭或私人往来，未见业务内容')
                    with db:
                        db.execute('DELETE FROM cp_window WHERE conversation_key=?', (key,))
                        db.execute('INSERT OR REPLACE INTO cp_cursor VALUES(?,?,?,?)', (key, total, digest(messages), now))
                        save_profile(db, profile)
                    result['rules'] += 1
                    result['profiles'].append(profile)
                    continue
                with db:
                    if changed:
                        db.execute('DELETE FROM cp_window WHERE conversation_key=?', (key,))
                        cursor = None
                    if new_due:
                        offset = cursor['total'] if cursor else 0
                        ordinal = db.execute('SELECT coalesce(max(ordinal),0) FROM cp_window WHERE conversation_key=?', (key,)).fetchone()[0]
                        for chunk in windows(messages[offset:]):
                            ordinal += 1
                            db.execute('INSERT INTO cp_window(conversation_key,ordinal,source_json) VALUES(?,?,?)', (key, ordinal, dumps(chunk)))
                        db.execute('INSERT OR REPLACE INTO cp_cursor VALUES(?,?,?,?)', (key, total, digest(messages), now))
                rows = list(db.execute("SELECT * FROM cp_window WHERE conversation_key=? AND state!='ready'", (key,)))
                failed = False
                chosen = sorted(rows, key=lambda r: -r['ordinal'])[:1] if settled else priority(rows)[:max_windows]
                outside = 0
                for row in chosen:
                    if old is None and outside >= 4:
                        # The first four windows agree this is not business; stop spending on it.
                        break
                    try:
                        sources = json.loads(row['source_json'])
                        answer, model = self._call(db, run_id, map_prompt(conv, sources), sources, now, daily_budget_tokens)
                    except BudgetStop:
                        result['budget_stopped'] = True
                        break
                    except ModelFailure as error:
                        with db:
                            db.execute("UPDATE cp_window SET state='failed',error_code=?,updated_at=? WHERE id=?", (error.code, now, row['id']))
                        result['failed_windows'] += 1
                        failed = True
                    else:
                        outside += no_trade(answer)
                        with db:
                            db.execute("UPDATE cp_window SET state='ready',result_json=?,model=?,error_code=NULL,updated_at=? WHERE id=?",
                                       (dumps(answer), model, now, row['id']))
                    result['windows'] += 1
                all_rows = list(db.execute('SELECT * FROM cp_window WHERE conversation_key=? ORDER BY ordinal', (key,)))
                ready = [r for r in all_rows if r['state'] == 'ready']
                read = {m['message_index'] for r in ready for m in json.loads(r['source_json'])}
                unread = {m['message_index'] for r in all_rows if r['state'] != 'ready' for m in json.loads(r['source_json'])}
                read -= unread
                profile = base_profile(conv, messages, now)
                profile['messages_read'] = len(read)
                try:
                    # A failed window never overwrites an earlier profile; a first profile may be partial.
                    if failed and (old or not ready):
                        raise ModelFailure('window_failed')
                    if ready:
                        parts = [json.loads(r['result_json']) for r in ready]
                        answer, reduce_model = self._reduce(db, run_id, parts, messages, now, daily_budget_tokens)
                        profile.update(kind=answer['kind'], org_name=answer['org_name'], people_json=answer['people'],
                                       country=answer['country'], country_name=answer['country_name'], language=answer['language'],
                                       products_json=answer['products'], summary=answer['summary'], evidence_json=answer['evidence'],
                                       confidence=answer['confidence'], customer_stage=stage_for(answer, profile['last_at'], now),
                                       model=reduce_model or ready[-1]['model'], state='ready' if len(read) == total else 'partial')
                    elif not messages:
                        profile.update(state='skipped', summary='暂无消息')
                except BudgetStop:
                    result['budget_stopped'] = True
                    if old and not changed:
                        profile.update({k: old[k] for k in ('kind', 'customer_stage', 'org_name', 'people_json', 'country', 'country_name', 'language', 'products_json', 'summary', 'evidence_json', 'confidence', 'model')})
                        profile.update(messages_read=old['messages_read'], state='partial')
                    else:
                        profile['messages_read'] = 0
                except ModelFailure as error:
                    result['failed_conversations'] += 1
                    # A failed attempt cannot overwrite a previously trustworthy projection.
                    with db:
                        db.execute('UPDATE cp_cursor SET checked_at=0 WHERE conversation_key=?', (key,))
                    if old:
                        result['profiles'].append(old)
                        continue
                    profile.update(state='failed', error_code=error.code, messages_read=0)
                with db:
                    save_profile(db, profile)
                result['profiles'].append(profile)
                if result['budget_stopped']:
                    break
            with db:
                db.execute('UPDATE cp_run SET finished_at=?,conversations=?,windows=?,failures=? WHERE id=?',
                           (self.clock(), result['conversations'], result['windows'], result['failed_conversations'], run_id))
            calls = db.execute('SELECT count(*),coalesce(sum(charged_tokens),0),coalesce(sum(reported_tokens),0) FROM cp_call WHERE run_id=?', (run_id,)).fetchone()
            result.update(calls=calls[0], charged_tokens=calls[1], reported_tokens=calls[2])
        if report:
            temporary = report / ('.profiles-' + uuid.uuid4().hex + '.tmp')
            fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'w') as out:
                out.write(dumps(dict(summary=numeric_summary(result), profiles=result['profiles'])) + '\n')
            os.replace(temporary, report / 'profiles.json')
        return result


def numeric_summary(result):
    return {k: v for k, v in result.items() if type(v) in (int, float, bool)}
