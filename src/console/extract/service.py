"""Bounded, read-only conversation extraction with shared role infrastructure."""
from datetime import datetime
import time
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from console.archive.store import ARCHIVE_PATH, Store
from console.archive.validation import BadRequest, Missing
from console.channels import shared_inbox
from console.inbox import ai as inbox_ai
from console.roles import ai as role_ai, triage
from console.roles.common import fingerprint, message_ref, safe, seconds, stamp
from console.roles.inputs import tables
from console.roles.storage import (RateLimited, reserve, reused, run_lock, run_view,
                                   save_run, serialized)
from . import ai, rules, storage, validation
from .config import Config

DEFAULT_RUNNER = role_ai.DEFAULT_RUNNER


class Extract:
    def __init__(self, path=ARCHIVE_PATH, *, store=None, inbox=None, runner=DEFAULT_RUNNER,
                 clock=None, config=None, timer=None):
        self.store = (Store(store.path, connection=store.connection, target_version=9)
                      if store else Store(path, target_version=9))
        self.inbox = inbox if inbox is not None else shared_inbox()
        self.clock = clock or time.time
        self.config = Config.coerce(config)
        self.timer = timer or time.monotonic
        self.runner = inbox_ai.official_runner if runner is DEFAULT_RUNNER else runner

    def _snapshot(self):
        with self.store.read() as db:
            available = tables(db)
            rows = {table: [dict(r) for r in db.execute('SELECT * FROM ' + table + ' ORDER BY id')]
                    if table in available else [] for table in ('parties', 'identities', 'products')}
            latest = {}
            if 'role_runs' in available:
                latest = {r['subject_key']: run_view(r) for r in db.execute(
                    "SELECT * FROM role_runs WHERE id IN (SELECT max(id) FROM role_runs "
                    "WHERE role='extract' AND subject_kind='conversation' GROUP BY subject_key)")}
        return rows, latest

    @staticmethod
    def _party(conv, rows):
        # Classification stays identical to triage. Name/alias matches are only
        # hints: unlike triage's proposed identity, party_id requires identities.
        classification = triage.rules(conv, SimpleNamespace(rows=rows, deals={}))
        identity = next((i for i in rows['identities'] if i['conversation_key'] == conv['key']), None)
        party = next((p for p in rows['parties'] if identity and p['id'] == identity['party_id']), None)
        kind = party['kind'] if party else classification['suggested_party_kind']
        if not party and conv.get('counterpart_role') in ('internal', 'learning', 'ignored'):
            return None
        if kind not in triage.IDENTITY_KINDS:
            return None
        return dict(kind=kind, party_id=party['id'] if party else None,
                    party_hint=safe(party['name'] if party else classification['suggested_party_name'], 100))

    def _model(self):
        name = getattr(self.runner, 'model', None)
        if isinstance(name, str) and name.strip():
            return safe(name, 200)
        if self.runner is inbox_ai.official_runner:
            return ' → '.join(inbox_ai.OFFICIAL_ATTEMPTS) + '（尝试序列）'
        return '未返回模型标识'

    @staticmethod
    def _items(candidates, conv, party, products):
        items = []
        for candidate in candidates:
            item = dict(candidate)
            refs = item['source_message_refs']
            fp = fingerprint(['extract-price-v2', conv['key'], refs[0]['id'], item['kind'], item.get('currency'),
                              item.get('price_fen'), storage.normalized_product(item.get('product_text'))])
            item.update(conversation_key=conv['key'], party_id=party['party_id'],
                        party_hint=party['party_hint'], input_fingerprint=fp,
                        matched_product_id=rules.match_product(item.get('product_text'), products,
                                                               party_id=party['party_id'])
                        if item['kind'] == 'offer' else None)
            items.append(item)
        return storage.merge_items(items)

    def _analysis_fingerprint(self, prepared, party, products, overview_fp):
        return fingerprint(dict(version=2, prepared=prepared, party=party, products=products,
                                overview_fingerprint=overview_fp, ai_enabled=self.runner is not None))

    def _analyze(self, conv, prepared, party, products, overview_fp, who, now, *, apply, deadline):
        fp = self._analysis_fingerprint(prepared, party, products, overview_fp)
        previous = reused(self.store, 'extract', conv['key'], fp)
        if apply and previous and not previous['output_json'].get('deferred'):
            return dict(previous, reused=True), False, None
        candidates = [item for item in prepared['candidates'] if not item.get('is_bundle')]
        source, status, error, model, success, limited = 'rules', 'ok', None, None, None, None
        called = False
        ai_summary = None
        has_clues = any(m.get('clue') for m in prepared['messages'])
        if apply and has_clues and self.runner is not None:
            # Failures never promote the prefilter's guesses to suggestions.
            candidates, source = [], 'ai'
            try:
                reserve(self.store, self.clock(), self.config, bucket='extract')
                timeout = min(self.config.ai_timeout, deadline - self.timer())
                if timeout <= 0:
                    raise TimeoutError
                called = True
                model = self._model()
                parsed = role_ai.invoke(self.runner, ai.prompt(prepared), timeout)
                model = safe(parsed['model'], 200) if parsed['model'] != inbox_ai.FALLBACK_MODEL else '未返回模型标识'
                candidates, ai_summary = ai.parse_detailed(parsed, prepared)
                success = True
            except RateLimited as exc:
                limited, error, status = exc, str(exc), 'failed'
                # No model result exists when a reservation is refused.
                ai_summary = None
            except TimeoutError:
                error, status, success = '报价抽取 AI 超时，下次再扫', 'failed', False
                ai_summary = (dict(blocks_total=0, accepted=0, dropped_by_reason={'ai_timeout': 1})
                              if called else None)
            except ai.AIParseError as exc:
                ai_summary = exc.summary
                error = ('报价抽取 AI 输出完全不是约定格式，下次再扫' if exc.code == 'format_invalid'
                         else '报价抽取 AI 没有给出可用的块，下次再扫')
                status, success = 'failed', False
            except ValueError:
                error, status, success = '报价抽取 AI 输出完全不是约定格式，下次再扫', 'failed', False
                ai_summary = (dict(blocks_total=0, accepted=0, dropped_by_reason={'format_invalid': 1})
                              if called else None)
            except Exception:
                error, status, success = '报价抽取 AI 调用失败，下次再扫', 'failed', False
                ai_summary = (dict(blocks_total=0, accepted=0, dropped_by_reason={'ai_call_failed': 1})
                              if called else None)
        items = self._items(candidates, conv, party, products)
        output = dict(item_count=len(items), overview_fingerprint=overview_fp, deferred=status == 'failed')
        if ai_summary is not None:
            output['ai_parse'] = ai_summary
        if not apply:
            # Dry-run previews are returned to the caller but are never stored
            # as role_run output, where source text and product phrases belong.
            output['items'] = items
        text = f'提取到 {len(items)} 条价格线索；请核对产品、单位和有效期后确认。' if items else '未找到可确定的价格线索。'
        if error:
            text = error
        evidence = [dict(kind='conversation', id=conv['key'])]
        for item in items:
            for ref in item['source_message_refs']:
                if ref not in evidence:
                    evidence.append(ref)
        if party['party_id']:
            evidence.append(dict(kind='party', id=party['party_id']))
        if not apply:
            return dict(id=None, role='extract', subject_kind='conversation', subject_key=conv['key'],
                        input_fingerprint=fp, output_json=output, output_text=text, evidence_json=evidence,
                        source='rules', status='ok', model=None, ai_success=None, error=None,
                        created_at=stamp(now), created_by=who, dry_run=True, reused=False), False, None
        with self.store.write() as db:
            run = save_run(db, 'extract', 'conversation', conv['key'], fp, output, text, evidence, who, now,
                           source=source, status=status, model=model, error=error, success=success)
            added = 0
            for item in items:
                _, created = storage.save_suggestion(db, dict(item, run_id=run['id']), who, now)
                added += int(created)
        return dict(run, reused=False, added=added), called, limited

    @serialized
    def scan(self, who, *, apply=True, conversation_key=None, days=30, limit=None):
        # Validate direct CLI/service calls as strictly as HTTP input.
        if type(days) is not int or not 1 <= days <= 365:
            raise BadRequest('扫描天数须为 1 至 365 的整数')
        limit = self.config.scan_limit if limit is None else limit
        if type(limit) is not int or not 1 <= limit <= 100:
            raise BadRequest('扫描数量须为 1 至 100 的整数')
        if conversation_key is not None:
            from console.deals.validation import key
            conversation_key = key(conversation_key)
        now = self.clock()
        deadline = self.timer() + self.config.scan_timeout
        rows, latest = self._snapshot()
        try:
            source = self.inbox.read(now)
            conversations, ready = dict(source['conversations']), bool(source['ready'])
        except Exception:
            conversations, ready = {}, False
        if conversation_key is not None and conversation_key not in conversations:
            if ready:
                raise Missing('未找到该会话')
            raise BadRequest('收件箱暂时无法读取，请稍后再扫描')
        eligible = []
        for conv in conversations.values():
            if conversation_key is not None and conv['key'] != conversation_key:
                continue
            at = seconds(conv.get('last_seen'))
            if at is None or at < now - days * 86400:
                continue
            party = self._party(conv, rows)
            if party is None:
                continue
            overview_fp = fingerprint(dict(version=2, ai_enabled=self.runner is not None,
                                           messages=[message_ref(conv['key'], m) for m in conv.get('messages', ())],
                                           last_seen=at, n_messages=conv.get('n_messages'), party=party,
                                           days=days, products=rows['products']))
            previous = latest.get(conv['key'], {}).get('output_json', {})
            same = previous.get('overview_fingerprint') == overview_fp
            # New windows first, failed windows next, completed windows last.
            priority = (1 if previous.get('deferred') else 2) if same else 0
            eligible.append((priority, -at, conv['key'], conv, party, overview_fp))
        # Unscanned/new inputs come first, so repeated manual scans advance
        # through the remaining queue instead of starving older conversations.
        eligible.sort(key=lambda value: value[:3])
        runs, previews, scanned_keys, attempted, limited, added = [], [], [], 0, None, 0
        unavailable, deferred, completed = [], [], set()
        for priority, _, key, overview, party, overview_fp in eligible[:limit]:
            try:
                conv = self.inbox.detail(key, now)
            except Exception:
                conv = None
            if conv is None:
                unavailable.append(key)
                continue
            party = self._party(conv, rows)
            if party is None:
                completed.add(key)
                continue
            prepared = rules.prepare(conv, now, days=days, timezone=self.config.timezone, party_kind=party['kind'])
            has_clues = any(m.get('clue') for m in prepared['messages'])
            time_left = deadline - self.timer()
            previous = latest.get(key)
            cached = (previous and not previous['output_json'].get('deferred')
                      and previous['input_fingerprint'] == self._analysis_fingerprint(
                          prepared, party, rows['products'], overview_fp))
            if apply and self.runner is not None and has_clues and not cached:
                reason = ('本次扫描 AI 会话上限已到，下次再扫' if attempted >= self.config.scan_ai_limit else
                          '本次扫描等待上限已到，下次再扫' if time_left <= 0 else
                          str(limited) if limited else None)
                if reason:
                    deferred.append(dict(conversation_key=key, reason=reason))
                    continue
            run, called, quota_error = self._analyze(conv, prepared, party, rows['products'], overview_fp,
                                                    who, now, apply=apply, deadline=deadline)
            attempted += int(called)
            limited = limited or quota_error
            runs.append(run)
            if run['output_json'].get('deferred'):
                deferred.append(dict(conversation_key=key, reason=run['error']))
            else:
                completed.add(key)
                scanned_keys.append(key)
            added += run.get('added', 0)
            if not apply:
                previews.extend(item for item in run['output_json']['items'] if item['confidence'] > .2)
        if apply:
            items = []
            for key in scanned_keys:
                # The scan can return every pending result from these bounded
                # conversations; listing itself remains independently paginated.
                items.extend(storage.list_suggestions(self.store, dict(status='pending', conversation_key=key,
                                                                       page=1, page_size=100))['items'])
        else:
            items = previews
        fresh_ai_runs = [run for run in runs if run.get('source') == 'ai' and not run.get('reused')]
        dropped_total = sum(sum(run.get('output_json', {}).get('ai_parse', {}).get('dropped_by_reason', {}).values())
                            for run in fresh_ai_runs)
        ai_failed = sum(run.get('status') == 'failed'
                        and bool(run.get('output_json', {}).get('ai_parse')) for run in fresh_ai_runs)
        remaining_keys = {item[2] for item in eligible if item[0] != 2 and item[2] not in completed}
        remaining_keys.update(unavailable)
        remaining_keys.update(item['conversation_key'] for item in deferred)
        result = dict(items=items, runs=runs, scanned=len(runs), added=added if apply else len(previews),
                      remaining=len(remaining_keys), ai_calls=attempted, ai_windows=attempted,
                      ai_failed=ai_failed, dropped_total=dropped_total, source_ready=ready,
                      unavailable=unavailable, deferred=deferred, dry_run=not apply,
                      message=f'已扫描 {len(runs)} 个会话，新增 {added if apply else len(previews)} 条待确认价格建议'
                              if ready else '收件箱暂时无法读取，请稍后再扫描')
        if apply:
            aggregate = {k: result[k] for k in ('scanned', 'added', 'remaining', 'source_ready', 'ai_calls',
                                                'ai_windows', 'ai_failed', 'dropped_total', 'deferred')}
            with self.store.write() as db:
                day = datetime.fromtimestamp(now, ZoneInfo(self.config.timezone)).date().isoformat()
                result['scan_run'] = save_run(db, 'extract', 'day', 'scan:' + day, fingerprint(aggregate),
                    aggregate, result['message'], [], who, now, status='fallback' if deferred or not ready else 'ok',
                    error=str(limited) if limited else None)
        if limited:
            limited.result = result
            raise limited
        return result

    def read(self, resource, params=None, row_id=None):
        if resource == 'suggestions':
            return storage.list_suggestions(self.store, params or {})
        if resource == 'summary':
            return storage.summary(self.store)
        raise BadRequest('抽取员资源无效')

    def write(self, operation, row_id, fields, who):
        values = validation.fields(operation, fields)
        with run_lock(self.store):
            if operation == 'scan':
                return self.scan(who, **values)
            return storage.decide(self.store, operation, row_id, values, who, self.clock())
