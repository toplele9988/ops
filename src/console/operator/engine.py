"""Durable event -> parallel proposals -> gated segmented delivery.

No network or daemon starts on import. Caller supplies source, model and bridge.
A claimed event is never evaluated twice, including a lost model response.
"""
from datetime import datetime, timezone
import json
import re
import time
from console.archive.validation import BadRequest, Conflict, Missing
from .store import sha
from .bridge import FakeBridge
from .context import model_context
from .facts import compose, guard, locale, usable, fingerprint
from .model import AiTask
from .policy import LEVELS, INTERNAL, TERMINAL, evaluate, pace
from .quote import calculate_quote
from .store import Store, TYPES, dumps

EVENTS = frozenset(('inbound', 'supplier_reply', 'forwarder_reply', 'task_due', 'reply_timeout', 'cost_expired', 'payment_confirmed'))


class Operator:
    def __init__(self, path, *, source, model=None, bridge=None, clock=None):
        self.store = Store(path)
        self.source, self.model = source, model or AiTask()
        self.bridge = bridge or FakeBridge()
        self.clock = clock or time.time

    def day(self):
        return datetime.fromtimestamp(self.clock(), timezone.utc).date().isoformat()

    def profiles(self):
        with self.store.read() as db:
            return self.profiles_in(db) if db is not None else {}

    def _record(self, db, action, state, reason, due=None):
        now = self.clock()
        db.execute('UPDATE operator_actions SET state=?,reason=?,due_at=?,updated_at=? WHERE id=?',
                   (state, reason, due, now, action['id']))
        self.store.decision(db, now, 'policy', dict(state=state, reason=reason, due_at=due,
                            settings=self.store.settings(db)), action['event_id'], action['id'])

    def handle(self, event):
        if (set(event)-{'id','kind','conversation_key','deal_id','message_id'} or event.get('kind') not in EVENTS
                or not all(isinstance(event.get(k), str) and 0 < len(event[k]) <= 8192 for k in ('id','conversation_key'))
                or ('deal_id' in event and (type(event['deal_id']) is not int or event['deal_id'] <= 0))):
            raise BadRequest('运营事件无效')
        digest = sha(dumps(event))
        with self.store.write() as db:
            old = db.execute('SELECT * FROM operator_events WHERE id=?', (event['id'],)).fetchone()
            if old:
                if old['digest'] != digest:
                    raise Conflict('同一事件内容改变，请核对来源')
                return self._event_actions(db, event['id'])
            db.execute('INSERT INTO operator_events VALUES(?,?,?,?,?)',
                       (event['id'], digest, dumps(event), 'evaluating', self.clock()))
        # The durable claim precedes model invocation. Crash/timeout never causes automatic reinvocation.
        context, prompt, output, missing = None, None, None, None
        try:
            try:
                context = self.source.snapshot(event, self.clock())
            except ValueError as error:
                # Unlinked conversations go to the ownership queue, not the model.
                if str(error) == 'identity_required':
                    missing = 'identity_required'
                raise
            prompt = model_context(context, getattr(self.source, 'hub', None), self.day())
            if event['kind'] == 'inbound':
                output = self.model.decide(prompt)
                plan = output['plan']
                if (set(plan)-{'intent','fact_ids'} or plan.get('intent') not in
                    ('inquiry','routine','accepted','payment_report','profile_update','uncertain')
                    or not isinstance(plan.get('fact_ids', []), list)
                    or len(plan.get('fact_ids', [])) > 10
                    or any(not isinstance(i, str) for i in plan.get('fact_ids', []))):
                    raise ValueError('plan_invalid')
            else:
                plan = dict(intent='inquiry', fact_ids=[])
                output = dict(plan=plan, model=dict(actual_model='rules:event'))
        except Exception:
            plan = dict(intent='uncertain', fact_ids=[])
            output = dict(plan=plan, model=dict(actual_model='rules:fallback'), error='context_or_model_unavailable')
        proposals = []
        if context and context.get('contacts', {}).get(context.get('contact_key')):
            try:
                proposals = self._plan(context, event, plan)
            except Exception:
                output['planning_error'] = 'planning_failed'
        if not proposals:
            proposals = [dict(kind='ask_owner', contact_key=event['conversation_key'], parts=[], fact_ids=[],
                              payload=dict(reason=missing or 'identity_context_or_plan_required'))]
        with self.store.write() as db:
            settings = self.store.settings(db)
            self.store.decision(db, self.clock(), 'decision', dict(trigger=event, context=context,
                model_context=prompt, output=output, proposals=proposals), event['id'])
            merged = []
            for proposal in proposals:
                if proposal['kind'] == 'ask_owner':
                    # One open owner question per conversation and reason; later
                    # messages only bump its counter instead of adding new cards.
                    open_id = self._merge_owner_question(db, proposal, event, context)
                    if open_id is not None:
                        merged.append(open_id)
                        continue
                profile = (context or {}).get('contacts', {}).get(proposal['contact_key'], {})
                errors = guard(proposal, context or {}, self.day())
                state, reason, due = evaluate(settings, proposal, profile, self.clock())
                if errors:
                    state, reason, due = 'blocked', errors[0], None
                proposal['context'] = context
                row_id = db.execute('INSERT INTO operator_actions(event_id,kind,contact_key,level,state,reason,due_at,'
                    'dry_run,data,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                    (event['id'], proposal['kind'], proposal['contact_key'], LEVELS[proposal['kind']], state, reason,
                     due, int(not settings['enabled'] or settings['dry_run']), dumps(proposal), self.clock(), self.clock())).lastrowid
                self.store.decision(db, self.clock(), 'policy', dict(state=state, reason=reason, settings=settings), event['id'], row_id)
            db.execute("UPDATE operator_events SET state='complete' WHERE id=?", (event['id'],))
            actions = self._event_actions(db, event['id'])
            for row_id in merged:
                actions.append(Store.action(db.execute('SELECT * FROM operator_actions WHERE id=?', (row_id,)).fetchone()))
            return actions

    def _merge_owner_question(self, db, proposal, event, context):
        reason = (proposal.get('payload') or {}).get('reason')
        rows = db.execute("SELECT * FROM operator_actions WHERE kind='ask_owner' AND contact_key=? AND state NOT IN "
                          "('sent','done','rejected','unknown','failed','duplicate') ORDER BY id", (proposal['contact_key'],))
        for row in rows.fetchall():
            data = json.loads(row['data'])
            if (data.get('payload') or {}).get('reason') != reason:
                continue
            data['repeats'] = int(data.get('repeats') or 1) + 1
            data['last_event_id'] = event['id']
            if context is not None:
                data['context'] = context
            db.execute('UPDATE operator_actions SET data=?,updated_at=? WHERE id=?', (dumps(data), self.clock(), row['id']))
            self.store.decision(db, self.clock(), 'policy', dict(state='merged', reason='same_open_owner_question',
                                repeats=data['repeats']), event['id'], row['id'])
            return row['id']
        return None

    def _plan(self, ctx, event, plan):
        origin = ctx['contacts'][ctx['contact_key']]
        proposals = []
        def add(kind, key, facts=None, **extra):
            facts = facts or []
            if kind in INTERNAL or kind == 'confirm_payment':
                parts, error = [], None
            else:
                parts, error = compose(kind, ctx['contacts'][key], facts)
            proposals.append(dict(kind=kind, contact_key=key, parts=parts,
                                  fact_ids=[f['id'] for f in facts], composition_error=error, **extra))
        def requirements_for(key):
            return [f for f in ctx.get('facts', []) if f.get('kind') == 'requirement' and usable(f, ctx['contacts'][key], self.day())]
        customer = ctx.get('customer_key')
        if plan['intent'] == 'uncertain':
            add('ask_owner', ctx['contact_key'], payload=dict(reason='intent_or_model_uncertain'))
            return proposals
        if event['kind'] in ('supplier_reply', 'forwarder_reply') and not origin['type'].startswith('supplier_'):
            add('ask_owner', ctx['contact_key'], payload=dict(reason='supplier_identity_required'))
            return proposals
        if not ctx.get('deal'):
            add('create_deal', ctx['contact_key'], payload=dict(conversation_key=ctx['contact_key'], stage='inquiry'))
            add('ask_owner', ctx['contact_key'], payload=dict(reason='link_suppliers_and_products'))
            return proposals
        if event['kind'] == 'payment_confirmed':
            if not ctx.get('payment_confirmed'):
                add('ask_owner', ctx['contact_key'], payload=dict(reason='human_payment_evidence_required'))
            else:
                add('schedule_shipping', ctx['contact_key'], payload=dict(deal_id=ctx['deal']['id'], nodes=['inspection','booking','transit']))
                for key, contact in ctx['contacts'].items():
                    if contact['type'] == 'supplier_logistics':
                        add('shipping', key, requirements_for(key), payload=dict(deal_id=ctx['deal']['id'], nodes=['inspection','booking','transit']), document_ready=True)
            return proposals
        if plan['intent'] in ('accepted', 'payment_report') and not origin['type'].startswith('customer_'):
            add('ask_owner', ctx['contact_key'], payload=dict(reason='customer_confirmation_required'))
            return proposals
        if plan['intent'] == 'payment_report':
            add('confirm_payment', ctx['contact_key'], payload=dict(reason='owner_click_required'))
            add('ask_owner', ctx['contact_key'], payload=dict(reason='verify_payment_in_deals'))
            return proposals
        if plan['intent'] == 'profile_update':
            inbound = next((m for m in reversed(ctx.get('messages', [])) if m['role'] == 'buyer'), None)
            if inbound and event.get('message_id'):
                # Record the original statement as a sourced, unverified note; never
                # turn model extraction into an approved profile field.
                add('update_profile', ctx['contact_key'], payload=dict(deal_id=ctx['deal']['id'],
                    party_id=origin['party_id'], note='待核对资料变更：'+inbound['text'][:300],
                    source_ref='inbox:'+event['message_id'][:100]))
            else:
                add('ask_owner', ctx['contact_key'], payload=dict(reason='profile_source_required'))
            return proposals
        if plan['intent'] == 'accepted':
            from .documents import prepare_documents
            prepare_documents(ctx, self.day())
            if customer:
                for kind in ('draft_pi', 'draft_order'):
                    # Document numbers/terms require a ready document from the quote workflow.
                    document = ctx.get('documents', {}).get(kind, {})
                    ready = bool(document.get('ready') and document.get('fact'))
                    add(kind, customer, [document['fact']] if ready else [],
                        payload=dict(deal_id=ctx['deal']['id']), document_ready=ready,
                        document_hash=document.get('source_hash'))
            if any(d.get('ready') for d in ctx.get('documents', {}).values()):
                add('advance_deal', ctx['contact_key'], payload=dict(deal_id=ctx['deal']['id'], stage='awaiting_payment'))
            return proposals
        for f in ctx.get('facts', []):
            if f.get('kind') == 'profile_update' and f.get('status') == 'confirmed' and f.get('source_ref'):
                add('update_profile', ctx['contact_key'], payload=dict(deal_id=ctx['deal']['id'], note=f['text'], source_ref=f['source_ref']))
        if plan['intent'] == 'routine' or (event['kind'] in ('task_due','reply_timeout') and origin['type'].startswith('customer_')):
            if customer:
                approved = {f['id']: f for f in ctx.get('facts', []) if usable(f, ctx['contacts'][customer], self.day())}
                selected = [approved[fid] for fid in plan.get('fact_ids', []) if fid in approved]
                add('reply_customer', customer, selected)
            return proposals
        # Missing/expired costs cause parallel requests, and a later reply re-enters this branch.
        quote, problems = calculate_quote(ctx, self.day())
        if quote and customer:
            ctx['facts'].extend(quote['facts'])
            lang = locale(ctx['contacts'][customer])
            fact = next((f for f in quote['facts'] if f['language'] == lang), None)
            add('quote_customer', customer, [fact] if fact else [], quote_digest=quote['digest'], calculation=quote)
            if ctx['deal']['stage'] in ('inquiry', 'sourcing'):
                add('advance_deal', ctx['contact_key'], payload=dict(deal_id=ctx['deal']['id'], stage='quoting'))
            return proposals
        # Ask both sides on initial inquiry. On replies only chase the missing dependency.
        initial = event['kind'] == 'inbound'
        for key, contact in ctx['contacts'].items():
            if contact['type'] in ('supplier_product','supplier_support') and (initial or any(p in problems for p in ('product_unapproved','cost_required','packing_required'))):
                add('ask_supplier', key, requirements_for(key), payload=dict(deal_id=ctx['deal']['id'], waiting_for='supplier_reply'))
            elif contact['type'] == 'supplier_logistics' and (initial or 'freight_required' in problems):
                add('ask_forwarder', key, requirements_for(key), payload=dict(deal_id=ctx['deal']['id'], waiting_for='forwarder_reply'))
        if initial and customer:
            add('reply_customer', customer)
        if problems and (not any(p in problems for p in ('cost_required','freight_required','packing_required','product_unapproved'))
                         or not any(p['kind'] in ('ask_supplier','ask_forwarder') for p in proposals)):
            add('ask_owner', ctx['contact_key'], payload=dict(reason=problems))
        return proposals

    @staticmethod
    def _event_actions(db, event_id):
        return [Store.action(r) for r in db.execute('SELECT * FROM operator_actions WHERE event_id=? ORDER BY id', (event_id,))]

    def _fresh(self, action, event):
        ctx = self.source.snapshot(event, self.clock())
        data = action['data']
        if data.get('quote_digest'):
            quote, errors = calculate_quote(ctx, self.day())
            if errors or quote['digest'] != data['quote_digest']:
                return ctx, ['quote_changed_or_expired']
            ctx['facts'].extend(quote['facts'])
        if data.get('document_hash'):
            from .documents import prepare_documents
            prepare_documents(ctx, self.day())
            doc = ctx.get('documents', {}).get(action['kind'], {})
            if not doc.get('ready') or doc.get('source_hash') != data['document_hash']:
                return ctx, ['document_changed']
        # Profile and party binding are always fetched again, never taken from the model.
        old = (data.get('context') or {}).get('contacts', {}).get(action['contact_key'])
        new = ctx['contacts'].get(action['contact_key'])
        if not old or not new or any(old.get(k) != new.get(k) for k in ('party_id','type','channel','language','tone')):
            return ctx, ['identity_or_profile_changed']
        if ctx.get('deal') and ctx['deal']['status'] in ('lost','paused'):
            return ctx, ['deal_closed']
        old_deal = (data.get('context') or {}).get('deal')
        if (old_deal or {}).get('id') != (ctx.get('deal') or {}).get('id'):
            return ctx, ['deal_changed']
        if action['kind'] == 'advance_deal' and old_deal['stage'] != ctx['deal']['stage']:
            return ctx, ['deal_changed']
        return ctx, guard(data, ctx, self.day())

    def dispatch(self, action_id):
        """One segment per call; no waits, loops or retries at the transport boundary."""
        with self.store.write() as db:
            row = db.execute('SELECT * FROM operator_actions WHERE id=?', (action_id,)).fetchone()
            if not row:
                raise Missing('未找到这条运营动作')
            action = self.store.action(row)
            if action['data'].get('rehearsal_only'):
                return action
            if action['state'] in TERMINAL or action['state'] == 'blocked':
                return action
            event = json.loads(db.execute('SELECT payload FROM operator_events WHERE id=?', (action['event_id'],)).fetchone()[0])
        try:
            ctx, errors = self._fresh(action, event)
        except Exception:
            ctx, errors = {}, ['context_unavailable']
        with self.store.write() as db:
            action = self.store.action(db.execute('SELECT * FROM operator_actions WHERE id=?', (action_id,)).fetchone())
            if action['state'] in TERMINAL or action['state'] == 'blocked':
                return action
            settings = self.store.settings(db)
            profile = ctx.get('contacts', {}).get(action['contact_key'], {})
            state, reason, due = evaluate(settings, action, profile, self.clock(), approved=bool(action['approved_by']))
            if errors:
                state, reason, due = 'blocked', errors[0], None
            if state == 'ready' and action['kind'] not in INTERNAL and hasattr(self.bridge, 'available') and not self.bridge.available(profile.get('channel')):
                state, reason, due = 'pending', 'channel_unavailable', None
            if action['state'] == 'dry_run' and not action['approved_by'] and not errors:
                state, reason, due = 'dry_run', 'old_rehearsal_requires_review', None
            if state != 'ready':
                self._record(db, action, state, reason, due)
                return self.store.action(db.execute('SELECT * FROM operator_actions WHERE id=?', (action_id,)).fetchone())
            if action['kind'] in ('shipping','schedule_shipping') and not ctx.get('payment_confirmed'):
                self._record(db, action, 'blocked', 'human_payment_evidence_required')
                return self.store.action(db.execute('SELECT * FROM operator_actions WHERE id=?', (action_id,)).fetchone())
            attempts = [dict(r) for r in db.execute('SELECT * FROM operator_attempts WHERE action_id=? ORDER BY part', (action_id,))]
            if any(a['state'] != 'sent' for a in attempts):
                self._record(db, action, 'unknown', 'attempt_unresolved_do_not_retry')
                return self.store.action(db.execute('SELECT * FROM operator_actions WHERE id=?', (action_id,)).fetchone())
            parts = action['data'].get('parts') or ['internal']
            part = len(attempts)
            if part >= len(parts):
                self._record(db, action, 'done' if action['kind'] in INTERNAL else 'sent', 'complete')
                return self.store.action(db.execute('SELECT * FROM operator_actions WHERE id=?', (action_id,)).fetchone())
            full_digest = fingerprint(dumps([action['kind'], action['data'].get('payload')])
                                      if action['kind'] in INTERNAL else dumps(parts))
            quota_key = profile.get('quota_key', action['contact_key'])
            if action['kind'] in INTERNAL:
                quota_key = 'internal:' + quota_key
            unresolved = db.execute("SELECT 1 FROM operator_attempts WHERE contact_key=? "
                                    "AND state IN ('sending','unknown') AND action_id!=? LIMIT 1",
                                    (quota_key, action_id)).fetchone()
            old = db.execute('SELECT state FROM operator_attempts WHERE contact_key=? AND digest=? AND action_id!=?',
                             (quota_key, full_digest, action_id)).fetchone()
            if old or unresolved:
                self._record(db, action, 'duplicate', 'same_content_or_unknown')
                return self.store.action(db.execute('SELECT * FROM operator_actions WHERE id=?', (action_id,)).fetchone())
            due = pace(db, quota_key, profile, self.clock())
            if due > self.clock():
                self._record(db, action, 'scheduled', 'rate_or_interval', due)
                return self.store.action(db.execute('SELECT * FROM operator_actions WHERE id=?', (action_id,)).fetchone())
            idem = 'operator-'+sha(str(self.store.path.resolve())+':'+str(action_id)+':'+str(part))[:40]
            db.execute('INSERT INTO operator_attempts(action_id,part,contact_key,digest,idempotency_key,state,created_at) '
                       "VALUES(?,?,?,?,?,'sending',?)", (action_id, part, quota_key, full_digest, idem, self.clock()))
            self._record(db, action, 'sending', 'reserved')
        # Reservation is durable before bridge entry. A process crash cannot replay it.
        # This second DB transaction serializes kill-switch writes with bridge admission.
        with self.store.write() as db:
            latest = db.execute('SELECT * FROM operator_actions WHERE id=?', (action_id,)).fetchone()
            settings = self.store.settings(db)
            try:
                ctx, last_errors = self._fresh(action, event)
                profile = ctx.get('contacts', {}).get(action['contact_key'], {})
            except Exception:
                last_errors = ['context_unavailable']
            st, why, due = evaluate(settings, action, profile, self.clock(), approved=bool(latest['approved_by']))
            if last_errors:
                st = 'blocked'
            # A rejection/stop arriving between reservation and bridge invocation cancels safely.
            if st != 'ready' or latest['state'] != 'sending':
                db.execute("UPDATE operator_attempts SET state='cancelled' WHERE idempotency_key=?", (idem,))
                if latest['state'] != 'rejected':
                    self._record(db, action, 'blocked' if last_errors else 'unknown', last_errors[0] if last_errors else 'admission_cancelled_do_not_retry')
                return self.store.action(db.execute('SELECT * FROM operator_actions WHERE id=?', (action_id,)).fetchone())
            actor = latest['approved_by'] or 'operator_auto'
            try:
                if action['kind'] in INTERNAL:
                    outcome = self.bridge.apply(kind=action['kind'], payload=action['data'].get('payload', {}), operation_key=idem, actor=actor)
                else:
                    outcome = self.bridge.send(channel=profile['channel'], conversation_key=action['contact_key'],
                        text=parts[part], idempotency_key=idem, actor=actor)
            except Exception:
                outcome = 'unknown'
            outcome = outcome if outcome in ('sent','done','failed') else 'unknown'
            db.execute('UPDATE operator_attempts SET state=? WHERE idempotency_key=?', ('sent' if outcome == 'done' else outcome, idem))
            final = outcome if outcome in ('unknown','failed','done') or part+1 == len(parts) else 'scheduled'
            self._record(db, action, final, 'bridge_'+outcome,
                         self.clock()+profile.get('interval_seconds', 30) if final == 'scheduled' else None)
            self.store.decision(db, self.clock(), 'delivery', dict(part=part, idempotency_key=idem, outcome=outcome,
                bridge=self.bridge.name, actor=actor, context=ctx), action['event_id'], action_id)
            return self.store.action(db.execute('SELECT * FROM operator_actions WHERE id=?', (action_id,)).fetchone())

    def tick(self):
        try:
            events = self.source.due_events(self.clock())
            self.store.health('tick.sources', self.clock())
        except Exception as error:
            self.store.health('tick.sources', self.clock(), error)
            events = []
        for event in events:
            try:
                self.handle(event)
            except Exception as error:
                self.store.health('tick.event', self.clock(), error)
        with self.store.write() as db:
            ids = [r[0] for r in db.execute("SELECT id FROM operator_actions WHERE state IN ('ready','scheduled','sending') "
                   'AND (due_at IS NULL OR due_at<=?) ORDER BY id LIMIT 100', (self.clock(),))]
        result = []
        for i in ids:
            try:
                result.append(self.dispatch(i))
            except Exception as error:
                self.store.health('tick.dispatch', self.clock(), error)
        return result

    def read(self):
        from .store import DEFAULTS
        with self.store.read() as db:
            if db is None:
                return dict(settings=json.loads(dumps(DEFAULTS)), profiles={}, actions=[], decisions=[], unresolved_events=[],
                            transport=self.bridge.name, types=list(TYPES))
            actions = [self.store.action(r) for r in db.execute("SELECT * FROM operator_actions WHERE id IN ("
                "SELECT id FROM operator_actions WHERE state NOT IN ('sent','done','rejected','unknown','failed','duplicate') ORDER BY id LIMIT 200) "
                "OR id IN (SELECT id FROM operator_actions WHERE reason!='same_open_owner_question' ORDER BY id DESC LIMIT 200) ORDER BY id DESC")]
            decisions = [dict(r, data=json.loads(r['data'])) for r in db.execute('SELECT * FROM operator_decisions ORDER BY id DESC LIMIT 100')]
            events = [dict(r) for r in db.execute("SELECT id,state,created_at FROM operator_events WHERE state!='complete'")]
            from .rehearsal import preview
            for action in actions:
                action['rehearsal'] = preview(db, action, self.clock(), self.profiles_in(db))
            health = [dict(r) for r in db.execute('SELECT * FROM operator_health ORDER BY component')]
            return dict(settings=self.store.settings(db), profiles=self.profiles_in(db), actions=actions,
                        decisions=decisions, unresolved_events=events, transport=self.bridge.name, types=list(TYPES), health=health)

    @staticmethod
    def profiles_in(db):
        return {r['conversation_key']: json.loads(r['value']) for r in db.execute('SELECT * FROM operator_profiles')}

    def write(self, operation, row_id, fields, who):
        fields = dict(fields)
        receipt = fields.pop('operation_key', '')
        if not re.fullmatch(r'[A-Za-z0-9-]{8,100}', receipt):
            raise BadRequest('操作编号无效')
        if not who or who == 'operator_auto':
            raise BadRequest('需要已登录人员操作')
        digest = sha(dumps([operation, row_id, fields, who]))
        with self.store.write() as db:
            old = db.execute('SELECT * FROM operator_receipts WHERE operation_key=?', (receipt,)).fetchone()
            if old:
                if old['digest'] != digest:
                    raise Conflict('同一次操作内容改变，请刷新后核对')
                return json.loads(old['result'])
            settings = self.store.settings(db)
            if operation == 'settings':
                if set(fields) != {'scope','key','enabled'} or fields['enabled'] not in ('0','1'):
                    raise BadRequest('开关参数无效')
                scope, key, enabled = fields['scope'], fields['key'], fields['enabled'] == '1'
                if scope == 'master' and key == 'enabled':
                    settings['enabled'] = enabled
                elif scope == 'master' and key == 'dry_run':
                    settings['dry_run'] = enabled
                elif scope in ('levels','types','contacts'):
                    if scope == 'levels' and key not in ('1','2','3'):
                        raise BadRequest('等级无效')
                    if scope == 'types' and key not in {f'{level}:{typ}' for level in (1,2,3) for typ in TYPES}:
                        raise BadRequest('类型无效')
                    if scope == 'contacts' and (not re.match(r'^[123]:.+$', key) or len(key) > 8200):
                        raise BadRequest('联系人无效')
                    settings[scope][key] = enabled
                else:
                    raise BadRequest('开关范围无效')
                db.execute('UPDATE operator_settings SET value=? WHERE id=1', (dumps(settings),))
                if not settings['enabled'] or settings['dry_run']:
                    db.execute("UPDATE operator_actions SET state='dry_run',reason='master_stopped',due_at=NULL,approved_by=NULL "
                               "WHERE state IN ('ready','scheduled','pending','dry_run')")
                # Enabling never releases old dry-run drafts. Only an explicit approval/new event can.
                result = dict(ok=True, settings=settings)
            elif operation == 'profile':
                from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
                from console.inbox.model import decode_key
                allowed = {'conversation_key','type','timezone','language','tone','hourly_limit','daily_limit','interval_seconds'}
                if set(fields) != allowed or fields['type'] not in TYPES or fields['language'] not in ('zh','zh-CN','en','en-US','es','fr','pt'):
                    raise BadRequest('联系人资料不完整或语言暂不支持')
                try:
                    decode_key(fields['conversation_key'], 'conversation')
                except ValueError:
                    raise BadRequest('联系人标识无效') from None
                try:
                    ZoneInfo(fields['timezone'])
                    for k, maximum in (('hourly_limit',60),('daily_limit',200),('interval_seconds',3600)):
                        if not re.fullmatch(r'[0-9]+', fields[k]) or not 1 <= int(fields[k]) <= maximum:
                            raise ValueError
                except (ValueError, ZoneInfoNotFoundError):
                    raise BadRequest('请填写有效时区和发送上限') from None
                if fields['tone'] not in ('professional','friendly'):
                    raise BadRequest('语气无效')
                key = fields.pop('conversation_key')
                profile = dict(fields)
                for k in ('hourly_limit','daily_limit','interval_seconds'):
                    profile[k] = int(profile[k])
                db.execute('INSERT INTO operator_profiles VALUES(?,?) ON CONFLICT(conversation_key) DO UPDATE SET value=excluded.value', (key,dumps(profile)))
                result = dict(ok=True, conversation_key=key)
            elif operation in ('approve','reject','edit'):
                row = db.execute('SELECT * FROM operator_actions WHERE id=?', (row_id,)).fetchone()
                if not row:
                    raise Missing('未找到这条运营动作')
                action = self.store.action(row)
                if action['data'].get('rehearsal_only') and operation != 'reject':
                    raise Conflict('档案缺口仅供演练，核对档案与联系人后重新起草')
                if action['state'] in TERMINAL or action['state'] == 'sending':
                    raise Conflict('动作已处理或结果待核对，不能再次发送')
                if operation == 'edit' and db.execute('SELECT 1 FROM operator_attempts WHERE action_id=?', (row_id,)).fetchone():
                    raise Conflict('已有分段发送记录，不能修改剩余正文；请拒绝后重新起草')
                if operation == 'reject':
                    if set(fields) != {'reason'} or not fields['reason'].strip() or len(fields['reason']) > 500:
                        raise BadRequest('请填写拒绝原因')
                    self._record(db, action, 'rejected', 'owner_rejected')
                elif operation == 'approve':
                    if fields or action['kind'] in ('confirm_payment','ask_owner'):
                        raise BadRequest('请到案子页人工核对收款或处理问题')
                    if not settings['enabled'] or settings['dry_run']:
                        raise Conflict('当前处于停用或演练模式，只能保存草稿')
                    if action['state'] == 'blocked':
                        raise Conflict('资料校验未通过，请先核对来源并重新起草')
                    db.execute("UPDATE operator_actions SET approved_by=?,state='ready',due_at=NULL WHERE id=?", (who, row_id))
                else:
                    if set(fields) != {'text'} or len(fields['text']) > 12000:
                        raise BadRequest('草稿内容无效')
                    data = action['data']
                    parts = fields['text'].split('\n\n')
                    # Editing removes facts; adding a claim requires first recording a reviewed source.
                    selected = [f for f in (data.get('context') or {}).get('facts', []) if f['text'] in parts[1:]]
                    data.update(parts=parts, fact_ids=[f['id'] for f in selected])
                    if guard(data, data.get('context') or {}, self.day()):
                        raise BadRequest('修改包含无来源事实，请先补入已核准档案')
                    db.execute("UPDATE operator_actions SET data=?,approved_by=NULL,state='pending',reason='owner_edited' WHERE id=?", (dumps(data), row_id))
                result = dict(ok=True, id=row_id, operation=operation)
            else:
                raise BadRequest('运营操作无效')
            self.store.decision(db, self.clock(), 'owner', dict(operation=operation, fields=fields, actor=who), action_id=row_id)
            db.execute('INSERT INTO operator_receipts VALUES(?,?,?)', (receipt, digest, dumps(result)))
        if operation == 'approve':
            result['action'] = self.dispatch(row_id)
        return result
