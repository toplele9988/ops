"""Four internal roles, with explicit human acceptance as the only business write."""
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
import time
from zoneinfo import ZoneInfo

from console.archive.service import Archive, get
from console.archive.store import ARCHIVE_PATH, Store, update
from console.archive.validation import BadRequest, Missing, Conflict
from console.channels import shared_inbox
from console.deals.service import Deals
from console.inbox import ai as inbox_ai
from console.kb.drafting import draft_context
from console.kb.hub import Hub, default_hub
from . import ai, brief, dossier_links, patrol, triage, validation
from . import deal_summary as secretary
from .common import (Config, ROLE_INFO, SEVERITY, conversation_data, evidence_for,
                     duration, fingerprint, last_known, message_ref, safe, seconds, stamp, summary)
from .inputs import Inputs, tables
from .storage import (RateLimited, recent, reserve, reused, run_lock, run_view, save_run, save_suggestion,
                      serialized, suggestion_view, sync_alerts)


class TransactionStore:
    """Borrow the caller's transaction through the existing service Store protocol.

    Human acceptance and its decision record commit together, including all
    existing deals/archive validation, events, identity checks and audits.
    """
    def __init__(self, store, db):
        self.path, self.connection = store.path, db

    @contextmanager
    def write(self):
        yield self.connection

    read = write


class Roles:
    def __init__(self, path=ARCHIVE_PATH, *, store=None, inbox=None, runner=ai.DEFAULT_RUNNER,
                 hub=None, clock=None, config=None, today=None, followups_path=None, dossiers_path=None):
        self.store = Store(store.path, connection=store.connection, target_version=4) if store else Store(path, target_version=4)
        self.clock = clock or time.time
        self.config = config or Config()
        self.today = today
        self.followups_path = followups_path
        self.dossiers_path = dossiers_path
        self.inbox = inbox if inbox is not None else shared_inbox()
        self.runner = inbox_ai.official_runner if runner is ai.DEFAULT_RUNNER else runner
        if hub is None:
            root = self.store.path.parent
            hub = default_hub() if self.store.path == Path(ARCHIVE_PATH) else Hub(
                root / 'role-catalog.sqlite3', root / 'role-brief.md', root / 'role-kb.sqlite3', embedder=None)
        self.hub = hub

    def day(self, now=None):
        if self.today is not None:
            return self.today() if callable(self.today) else self.today
        return datetime.fromtimestamp(self.clock() if now is None else now, ZoneInfo(self.config.timezone)).date()

    def inputs(self, now, **kw):
        return Inputs(self.store, self.inbox, now, self.day(now), self.config, followups_path=self.followups_path, **kw)

    def configured_model(self, *, attempts=False):
        model = getattr(self.runner, 'model', None)
        if isinstance(model, str) and model.strip():
            return safe(model, 200)
        if self.runner is inbox_ai.official_runner:
            return ' → '.join(inbox_ai.OFFICIAL_ATTEMPTS) + '（尝试序列）' if attempts else inbox_ai.OFFICIAL_ATTEMPTS[0]
        return '未返回模型标识'

    def _run(self, role, kind, key, data, output, evidence, render, who, now, *,
             ai_data=None, parse=None, skip_reason=None, effect=None, refresh_effect=False, apply=True):
        # Invalidate suggestions generated before dossier evidence was available.
        fp = fingerprint(dict(version=4 if role == 'triage' else 2, role=role, data=data))
        previous = reused(self.store, role, key, fp)
        if previous:
            if effect and refresh_effect and apply:
                with self.store.write() as db:
                    effect(db, previous)
            return dict(previous, reused=True, **({'dry_run': True} if not apply else {}))
        error = skip_reason
        source, status, model, success, limited = 'rules', 'fallback' if error else 'ok', None, None, None
        if apply and ai_data is not None and self.runner is not None and not skip_reason:
            try:
                reserve(self.store, now, self.config)
                model = self.configured_model(attempts=True)
                parsed = ai.invoke(self.runner, ai.prompt(role, ai_data), self.config.ai_timeout)
                model = safe(parsed['model'], 200) if parsed['model'] != inbox_ai.FALLBACK_MODEL else '未返回模型标识'
                model = model or '未返回模型标识'
                output = parse(parsed)
                source, success = 'ai', True
            except RateLimited as exc:
                error, limited, status = str(exc), exc, 'fallback'
            except TimeoutError:
                error, status, success = '岗位 AI 超时，已回退规则建议', 'fallback', False
            except ValueError:
                error, status, success = '岗位 AI 返回空内容或格式不符，已回退规则建议', 'fallback', False
            except Exception:
                error, status, success = '岗位 AI 调用失败，已回退规则建议', 'fallback', False
        text = render(output)
        if not apply:
            return dict(id=None, role=role, subject_kind=kind, subject_key=str(key), input_fingerprint=fp,
                        status='ok', source='rules', model=None, ai_success=None, output_json=output,
                        output_text=text, evidence_json=evidence, error=None, created_at=stamp(now),
                        created_by=who, dry_run=True, reused=False)
        with self.store.write() as db:
            result = save_run(db, role, kind, key, fp, output, text, evidence, who, now,
                              source=source, status=status, model=model, error=error, success=success)
            if effect:
                effect(db, result)
        result['reused'] = False
        if limited and role != 'brief':
            limited.result = result
            raise limited
        return result

    @serialized
    def run_secretary(self, deal_id, who, *, apply=True):
        now = self.clock()
        inputs = self.inputs(now, histories=True, deal_id=deal_id)
        deal, quote = inputs.deals[deal_id], inputs.latest_quote(deal_id)
        convs = [conversation_data(inputs.conversations[c['key']]) for c in deal['conversations']
                 if c['key'] in inputs.conversations]
        output, actions = secretary.rules(deal, quote, now, inputs.unavailable, self.config.reply_hours)
        contexts = []
        # Retrieval uses the same customer_reply policy/scope proof as inbox
        # drafting. No internal product_facts context becomes customer wording.
        customers = {p['party_id'] for p in deal['participants'] if p['role'] == 'customer'}
        customer_keys = {c['key'] for c in deal['conversations'] if c['party_id'] in customers}
        if apply and self.runner is not None:
            for conv in convs:
                if conv['key'] in customer_keys:
                    contexts.append(draft_context(self.hub, dict(conv, counterpart_role='customer'), self.config.knowledge_timeout))
        evidence = evidence_for(deal, [quote] if quote else [], convs)
        evidence += [dict(kind='knowledge', id=s['source_id']) for ctx in contexts for s in ctx['sources']]
        # Full inputs determine reuse; only bounded, redacted context reaches AI.
        data = dict(deal=deal, quote=quote, conversations=convs, contexts=contexts,
                    day=inputs.today.isoformat(), output=output)
        ai_data = dict(stage=deal['stage_label'], status=output['headline'],
                       waiting_on=[{k: value for k, value in item.items() if k != 'conversation_key'}
                                   for item in output['waiting_on']],
                       quote=dict(status=quote['status'], margin=quote['totals']['margin'],
                                  blocking=quote['blocking'], warnings=quote['warnings']) if quote else None,
                       conversations=[dict(channel=c['channel'], counterpart_role=c['counterpart_role'],
                           messages=[{k: m[k] for k in ('role', 'text', 'sent_at')} for m in c['messages']]) for c in convs],
                       knowledge=[safe(ctx['text'], 3000) for ctx in contexts],
                       actions=[dict(number=i, **a) for i, a in enumerate(actions, 1)], risks=output['risks'])
        return self._run('secretary', 'deal', deal_id, data, output, evidence, secretary.render, who, now,
                         ai_data=ai_data, parse=lambda parsed: ai.secretary(parsed, output, actions), apply=apply)

    def _suggestion_rows(self, status=None):
        with self.store.read() as db:
            if 'triage_suggestions' not in tables(db):
                return []
            query = 'SELECT * FROM triage_suggestions'
            args = ()
            if status and status != 'all':
                query += ' WHERE status=?'
                args = (status,)
            return [suggestion_view(r) for r in db.execute(query + ' ORDER BY updated_at DESC,id DESC', args)]

    @serialized
    def scan_triage(self, who, *, apply=True, use_ai=True):
        now = self.clock()
        inputs = self.inputs(now)
        inputs.dossier_links = dossier_links.index(dossier_links.load(self.dossiers_path, archive_path=self.store.path))
        existing = {s['conversation_key']: s for s in self._suggestion_rows()}
        linked = {c['conversation_key'] for c in inputs.rows['deal_conversations']}
        # Existing customer identities still use the original deal-suggestion rules.
        linked.update(i['conversation_key'] for i in inputs.rows['identities']
                      if i['conversation_key'] in inputs.dossier_links)
        eligible = [c for c in inputs.conversations.values() if c['key'] not in linked
                    and existing.get(c['key'], {}).get('status', 'pending') == 'pending'
                    and triage.eligible(c, now, c['key'] in inputs.dossier_links)]
        eligible.sort(key=lambda c: (-c['last_seen'], c['key']))
        runs, suggestions, attempted, limited = [], [], False, None
        choices = triage.candidates(inputs)
        prepared = []
        for conv in eligible[:self.config.scan_limit]:
            try:
                detailed = self.inbox.detail(conv['key'], now)
            except Exception:
                detailed = None
            conv = detailed or conv
            if not triage.eligible(conv, now, conv['key'] in inputs.dossier_links):
                continue
            prepared.append((conv, triage.rules(conv, inputs)))
        prepared.sort(key=lambda pair: (-(seconds(pair[0].get('last_seen')) or 0), pair[0]['key']))
        triage.combine_buyers(prepared, existing.values(), inputs)
        for conv, rule in prepared:
            facts = conversation_data(conv, 12)
            latest = triage.latest_message(conv) if conv['key'] in inputs.dossier_links else last_known(conv)
            certain = rule.pop('certain')
            if rule['suggested_deal_id'] is not None and not rule.get('duplicate_of'):
                title = safe(inputs.deals[rule['suggested_deal_id']]['title'], 150)
                rule['reason'] = '建议关联“' + title + '”；' + rule['reason']
            if rule['action'] != 'ignore' and not rule.get('duplicate_of'):
                rule['reason'] += ('；最近消息：' if conv['key'] in inputs.dossier_links else '；对方消息：') + duration(latest['sent_at'] if latest else None, now)[0]
            evidence = [dict(kind='conversation', id=conv['key'])] + [m['reference'] for m in facts['messages']]
            if latest:
                latest_ref = message_ref(conv['key'], latest)
                if latest_ref not in evidence:
                    evidence.append(latest_ref)
            evidence += [dict(kind='deal', id=c['id']) for c in choices]
            evidence += [dict(kind='identity', id=i['id']) for i in inputs.rows['identities'] if i['conversation_key'] == conv['key']]
            if rule['suggested_party_id']:
                evidence.append(dict(kind='party', id=rule['suggested_party_id']))
            if rule.get('duplicate_of'):
                evidence.append(dict(kind='conversation', id=rule['duplicate_of']))
            links = inputs.dossier_links.get(conv['key'], [])
            evidence += [dict(kind='dossier_source', id=link['source_id'], supplier_id=link['supplier_id']) for link in links]
            data = dict(conversation=facts, candidates=choices, rule=rule, dossier_links=links, use_ai=use_ai)
            ai_data = dict(conversation=dict(channel=facts['channel'], title=facts['buyer_name'],
                messages=[dict(role=m['role'], text=m['text']) for m in facts['messages']]), candidates=choices)
            def save(db, run, conv=conv):
                save_suggestion(db, run, summary(conv), who, now)
            try:
                run = self._run('triage', 'conversation', conv['key'], data, rule, evidence, triage.render, who, now,
                                ai_data=None if certain or not use_ai else ai_data,
                                parse=lambda parsed, rule=rule: triage.preserve_conflict(ai.triage(parsed, rule, choices), rule), effect=save,
                                skip_reason='本次扫描的 AI 尝试额度已用完，请按规则建议人工核对' if use_ai and attempted and not certain else None,
                                apply=apply)
            except RateLimited as exc:
                run, limited = exc.result, exc
            attempted |= use_ai and not run.get('reused', False) and not certain and self.runner is not None
            runs.append(run)
            if not apply:
                suggestions.append(dict(run['output_json'], conversation=summary(conv), conversation_key=conv['key']))
        if apply:
            keys = {r['subject_key'] for r in runs}
            suggestions = [s for s in self._suggestion_rows('pending') if s['conversation_key'] in keys]
        suggestions.sort(key=triage.suggestion_order)
        result = dict(items=suggestions, runs=runs, scanned=len(runs), remaining=max(0, len(eligible) - len(runs)),
                      source_ready=inputs.ready, dry_run=not apply,
                      message=f'已整理 {len(runs)} 个会话，请确认建议归属' if inputs.ready else '收件箱暂时无法读取，请稍后再扫描')
        if limited:
            limited.result = result
            raise limited
        return result

    @serialized
    def run_patrol(self, who, *, apply=True):
        now = self.clock()
        inputs = self.inputs(now, histories=True)
        alerts, protected = patrol.rules(inputs, self.config)
        output = dict(headline=f'发现 {len(alerts)} 项需处理事项' if alerts else '本次未发现到期或拖延事项，请继续按案子待办跟进',
                      alerts=alerts, incomplete_deals=sorted(protected),
                      next_step='请优先核对高优先级提醒；渠道暂不可读的案子保留原提醒' if protected else '请按提醒核对，处理完成后再运行巡检')
        data = dict(day=inputs.today.isoformat(), output=output, deals=list(inputs.deals.values()),
                    quotes=dict(inputs.quotes), thresholds=dict(expiring_days=self.config.expiring_days,
                    reply_hours=self.config.reply_hours, payment_days=self.config.payment_days, stale_days=self.config.stale_days,
                    delivery_due_days=self.config.delivery_due_days, issue_open_days=self.config.issue_open_days,
                    pending_payment_days=self.config.pending_payment_days))
        evidence = []
        for deal in inputs.deals.values():
            convs = [conversation_data(inputs.conversations[c['key']], 1) for c in deal['conversations'] if c['key'] in inputs.conversations]
            evidence.extend(evidence_for(deal, inputs.quotes[deal['id']], convs))
        evidence.extend(dict(kind=a['ref_kind'], id=a['ref_id']) for a in alerts
                        if a['ref_kind'] in ('fulfilment_node', 'issue', 'payment'))
        return self._run('patrol', 'day', inputs.today.isoformat(), data, output, evidence,
            lambda out: '\n'.join([out['headline'], *[a['message'] for a in out['alerts']], out['next_step']]), who, now,
            effect=lambda db, run: sync_alerts(db, alerts, protected, now, who), refresh_effect=True, apply=apply)

    @serialized
    def run_brief(self, who, *, apply=True):
        now = self.clock()
        inputs = self.inputs(now)
        alerts = self.alerts()
        # One latest output per deal, independent of history size.
        secretaries = self.latest_secretaries()
        pending = self._suggestion_rows('pending')
        output, evidence = brief.rules(inputs, alerts, secretaries, len(pending), self.config)
        evidence += [dict(kind='triage_suggestion', id=s['id']) for s in pending]
        return self._run('brief', 'day', inputs.today.isoformat(), dict(output=output, evidence=evidence), output,
            evidence, brief.render, who, now, ai_data=output, parse=lambda parsed: ai.brief(parsed, output), apply=apply)

    def latest_secretaries(self):
        with self.store.read() as db:
            if 'role_runs' not in tables(db):
                return []
            return [run_view(r) for r in db.execute("SELECT * FROM role_runs WHERE id IN (SELECT max(id) FROM role_runs WHERE role='secretary' GROUP BY subject_key)")]

    def alerts(self, deal_id=None, include_dismissed=False):
        with self.store.read() as db:
            if deal_id is not None:
                if 'deals' not in tables(db):
                    raise Missing('未找到该案子')
                get(db, 'deals', deal_id)
            if 'role_alerts' not in tables(db):
                return []
            rows = [dict(r) for r in db.execute('SELECT * FROM role_alerts WHERE resolved_at IS NULL')]
        return sorted((r for r in rows if (deal_id is None or r['deal_id'] == deal_id) and
                       (include_dismissed or not r['dismissed_at'])), key=lambda r: (SEVERITY[r['severity']], r['id']))

    def _link_duplicates(self, db, primary, deal_id, party_id, who, now):
        if deal_id is None or party_id is None:
            return
        party = get(db, 'parties', party_id)
        if party['kind'] != 'customer':
            return
        pending = db.execute("SELECT * FROM triage_suggestions WHERE status='pending'").fetchall()
        for row in pending:
            child = suggestion_view(row)
            if child['duplicate_of'] != primary['conversation_key'] or child['action'] == 'ignore':
                continue
            previous = run_view(get(db, 'role_runs', child['run_id']))
            output = triage.link_duplicate(previous['output_json'], primary, party, deal_id)
            evidence = previous['evidence_json'] + [dict(kind='triage_suggestion', id=primary['id']),
                                                   dict(kind='deal', id=deal_id), dict(kind='party', id=party_id)]
            # Keep the new recommendation and its evidence in the acceptance
            # transaction. No sibling conversation or identity is bound here.
            fp = fingerprint(dict(version=3, accepted_suggestion=primary['id'], output=output))
            run = save_run(db, 'triage', 'conversation', child['conversation_key'], fp, output,
                           triage.render(output), evidence, who, now)
            save_suggestion(db, run, child['conversation'], who, now)

    def decide(self, operation, row_id, values, who):
        now = self.clock()
        with self.store.write() as db:
            table = 'role_alerts' if operation == 'alert_dismiss' else 'triage_suggestions'
            row = get(db, table, row_id)
            if operation == 'alert_dismiss':
                if not row['dismissed_at']:
                    update(db, table, row_id, dict(dismissed_at=stamp(now), dismissed_by=who), who)
                return dict(ok=True, id=row_id)
            if operation == 'triage_dismiss':
                if row['status'] == 'accepted':
                    raise Conflict('该建议已经接受，不能再忽略')
                if row['status'] != 'dismissed':
                    update(db, table, row_id, dict(status='dismissed', decided_at=stamp(now), decided_by=who), who)
                return dict(ok=True, id=row_id)
            if row['status'] == 'dismissed':
                raise Conflict('该建议已经忽略，请先在案子页核对归属')
            if row['status'] == 'accepted':
                return dict(ok=True, id=row_id, deal_id=row['decided_deal_id'], party_id=row['decided_party_id'], reused=True)
            action = values.get('action', row['action'])
            if action == 'ignore':
                update(db, table, row_id, dict(status='dismissed', decided_at=stamp(now), decided_by=who), who)
                return dict(ok=True, id=row_id)
            suggestion = suggestion_view(row)
            if suggestion['duplicate_of'] and (action == 'create' or (row['action'] == 'identity' and action == 'identity')):
                raise Conflict('请先处理同名买家的主建议，再确认加入同一个案子')
            did = values.get('deal_id', row['suggested_deal_id'])
            pid = values.get('party_id', row['suggested_party_id'])
            kind = values.get('party_kind', row['suggested_party_kind'])
            name = values.get('party_name') or suggestion_view(row)['suggested_party_name']
            key = row['conversation_key']
            linked = db.execute('SELECT deal_id FROM deal_conversations WHERE conversation_key=?', (key,)).fetchall()
            if linked and (action != 'link' or any(link['deal_id'] != did for link in linked)):
                raise Conflict('该会话已有关联案子，请先核对最新归属')
            borrowed = TransactionStore(self.store, db)
            deals = Deals(store=borrowed, lookup=lambda key: None, clock=lambda: now)
            owner = deals.read('identity_lookup', {'key': key})
            if owner:
                if pid and pid != owner['party']['id']:
                    raise Conflict('会话归属已变化，请先核对往来单位')
                pid = owner['party']['id']
            if pid is None and name:
                existing = db.execute('SELECT id FROM parties WHERE kind=? AND name=?', (kind, name)).fetchone()
                pid = existing[0] if existing else Archive(store=borrowed).write('party_create', None,
                      dict(kind=kind, name=name), who)['id']
            if action == 'create':
                fields = dict(conversation_key=key, title=values.get('title') or '')
                if pid is not None:
                    deals.write('identity', None, dict(conversation_key=key, party_id=str(pid)), who)
                else:
                    fields.update(party_kind=kind, party_name=name or suggestion_view(row)['conversation']['counterpart_name'] or '待确认往来单位')
                did = deals.write('create', None, fields, who)['id']
            elif action == 'link':
                if did is None:
                    raise BadRequest('请指定要关联的案子')
                fields = dict(conversation_key=key)
                if pid is not None:
                    fields['party_id'] = str(pid)
                deals.write('conversation', did, fields, who)
            elif action == 'identity':
                if pid is None:
                    raise BadRequest('请指定往来单位或填写单位名称')
                deals.write('identity', None, dict(conversation_key=key, party_id=str(pid)), who)
                did = None
            else:
                raise BadRequest('接受方式无效')
            owner = deals.read('identity_lookup', {'key': key})
            pid = owner['party']['id'] if owner else None
            update(db, table, row_id, dict(status='accepted', action=action, decided_at=stamp(now), decided_by=who,
                                         decided_deal_id=did, decided_party_id=pid), who)
            self._link_duplicates(db, dict(row, action=action), did, pid, who, now)
            return dict(ok=True, id=row_id, deal_id=did, party_id=pid, reused=False)

    def write(self, operation, row_id, fields, who, child_id=None):
        values = validation.fields(operation, fields)
        with run_lock(self.store):
            if operation == 'secretary':
                return self.run_secretary(row_id, who)
            if operation == 'triage':
                return self.scan_triage(who)
            if operation == 'patrol':
                return self.run_patrol(who)
            if operation == 'brief':
                return self.run_brief(who)
            return self.decide(operation, row_id, values, who)

    def read(self, resource, params=None, row_id=None):
        params = params or {}
        if resource == 'runs':
            return dict(items=recent(self.store, params.get('role'), params.get('limit', 50)))
        if resource == 'triage':
            items = self._suggestion_rows(params.get('status', 'pending'))
            try:
                conversations = self.inbox.read(self.clock())['conversations'] if items else {}
            except Exception:
                conversations = {}
            for item in items:
                conv = conversations.get(item['conversation_key'])
                item['summary_current'] = conv is not None
                if conv:
                    item['conversation'] = summary(conv)
            return dict(items=sorted(items, key=triage.suggestion_order))
        if resource == 'alerts':
            return dict(items=self.alerts(params.get('deal_id'), params.get('include_dismissed', False)))
        if resource in ('secretary', 'brief'):
            if resource == 'secretary':
                # No inbox access is necessary to read an existing output.
                with self.store.read() as db:
                    if 'deals' not in tables(db):
                        raise Missing('未找到该案子')
                    get(db, 'deals', row_id)
            runs = recent(self.store, resource, 1, subject=row_id if resource == 'secretary' else self.day().isoformat())
            if not runs:
                raise Missing('还没有分析' if resource == 'secretary' else '今天还没有成交简报，请先运行简报')
            return runs[0]
        if resource != 'roles':
            raise BadRequest('岗位资源无效')
        now = self.clock()
        with self.store.read() as db:
            available = 'role_runs' in tables(db)
            counts = {r['role']: dict(r) for r in db.execute("SELECT role,count(*) AS runs,sum(status IN ('fallback','failed')) AS failures FROM role_runs WHERE created_at>=? AND created_at<=? GROUP BY role", (stamp(now - 86400), stamp(now)))} if available else {}
        items = []
        for key, (name, duty, level) in ROLE_INFO.items():
            last = recent(self.store, key, 1)
            latest = last[0] if last else {}
            items.append(dict(key=key, name=name, responsibility=duty, level=level,
                last_run_at=latest.get('created_at'), last_status=latest.get('status'), last_source=latest.get('source'),
                runs_24h=counts.get(key, {}).get('runs', 0), failures_24h=counts.get(key, {}).get('failures', 0),
                model='纯规则' if key in ('patrol', 'audit') or self.runner is None else self.configured_model(),
                last_model=latest.get('model')))
        return dict(items=items)
