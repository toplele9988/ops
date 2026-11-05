"""Fictional inputs only. A subprocess guard makes real ai-task calls impossible."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sqlite3
import subprocess
from types import SimpleNamespace

import pytest

from console.dossiers.incremental import Incremental
from console.dossiers.incremental.model import AiTask, FLASH, PRO, INSTRUCTION, ModelFailure, prompt_for
from console.dossiers.incremental.schema import migrate
from console.dossiers.service import family_key


@pytest.fixture(autouse=True)
def no_real_runner(monkeypatch):
    def blocked(*a, **kw):
        raise AssertionError('real subprocess/model calls are forbidden in incremental tests')
    monkeypatch.setattr(subprocess, 'run', blocked)


@pytest.fixture
def inc(env):
    with sqlite3.connect(env.target) as db:
        db.execute("UPDATE sd_offer SET moq_text='10' WHERE offer_id='O1'")
        for sid, group in [('S1', 'fictional-group@chatroom'), ('S2', 'fictional-other@chatroom')]:
            db.execute('UPDATE sd_supplier SET contacts_json=? WHERE supplier_id=?',
                (json.dumps([dict(channel='wechat_group', group_id=group)]), sid))
    with sqlite3.connect(env.wx) as db:
        db.execute('ALTER TABLE company_media ADD COLUMN status TEXT')
        db.execute('ALTER TABLE company_media ADD COLUMN text TEXT')
        db.execute('ALTER TABLE company_media ADD COLUMN name TEXT')
        db.execute('ALTER TABLE company_note_assets ADD COLUMN status TEXT')
        db.execute('ALTER TABLE company_note_assets ADD COLUMN text TEXT')
        db.execute('CREATE TABLE wechat_private_messages(app_id TEXT,message_id TEXT,own_wxid TEXT,peer_id TEXT,direction TEXT)')
        db.execute('CREATE TABLE wechat_private_contacts(app_id TEXT,own_wxid TEXT,peer_id TEXT,business_role TEXT,display_name TEXT)')
    calls = []
    clock = [datetime(2030, 3, 20, tzinfo=timezone.utc)]
    def runner(args, **kw):
        calls.append((args, kw['input']))
        sources = json.loads(kw['input'][len(INSTRUCTION):])
        offers = []
        for source in sources:
            text = source['text']
            price = re.search(r'报价 ([0-9.]+)', text)
            if not price:
                continue
            fields = {}
            values = dict(model='DEMO-X', amount_decimal=price[1], **{'config.配置': 'config-alpha'})
            if 'MOQ 10' in text:
                values['moq_text'] = '10'
            for key, value in values.items():
                fields[key] = dict(value=value, source_id=source['source_id'], excerpt=text)
            offers.append(dict(fields=fields))
        return SimpleNamespace(returncode=0, stdout=json.dumps(dict(status='ok', actual_model=FLASH,
            output=dict(offers=offers), usage=dict(prompt_tokens=200, completion_tokens=50, total_tokens=250))), stderr='do not retain fictional stderr')
    worker = Incremental(env.target, env.wx, runner=runner, now=lambda: clock[0])
    return SimpleNamespace(env=env, worker=worker, calls=calls, runner=runner, clock=clock)


def add(inc, price='125.00', *, group='fictional-group@chatroom', day=20, text=None, attachment=None):
    with sqlite3.connect(inc.env.wx) as db:
        seq = db.execute('SELECT count(*) FROM messages').fetchone()[0]
        mid = 'fictional-new-' + str(seq)
        text = text if text is not None else f'DEMO-X 配置 config-alpha 报价 {price} MOQ 10'
        payload = dict(group_id=group, own_wxid='fictional-self', text=text, attachments=[])
        if attachment is not None:
            payload['attachments'] = [dict(name='fictional-table.txt')]
        db.execute('INSERT INTO messages VALUES(?,?,?,?,?)', ('fictional-app', mid, group,
            datetime(2030, 3, day, tzinfo=timezone.utc).timestamp(), json.dumps(payload)))
        if attachment is not None:
            db.execute('INSERT INTO company_media(app_id,message_id,ordinal,status,text,name) VALUES(?,?,?,?,?,?)',
                ('fictional-app', mid, 0, 'ready' if attachment else 'empty', attachment, 'fictional-table.txt'))
    return mid


def prime(inc):
    result = inc.worker.run()
    assert result['initialized'] and result['calls'] == 0


def rows(inc, table):
    with sqlite3.connect(inc.env.target) as db:
        db.row_factory = sqlite3.Row
        return [dict(r) for r in db.execute('SELECT * FROM ' + table)]


def test_initial_tail_and_cursor_continuation(inc):
    add(inc)
    prime(inc)
    assert len(rows(inc, 'sd_offer')) == 3
    add(inc)
    result = inc.worker.run()
    assert result['calls'] == 1 and len(result['changes']['offers']) == 1
    again = inc.worker.run()
    assert again['calls'] == 0 and len(rows(inc, 'sd_offer')) == 4
    assert len(inc.calls) == 1


def test_message_replay_idempotent(inc):
    prime(inc)
    add(inc)
    result = inc.worker.run()
    assert result['failed_windows'] == 0
    with sqlite3.connect(inc.env.target) as db:
        # Simulate durable source replay while preserving its stable message identity.
        original = db.execute('SELECT count(*) FROM sd_offer').fetchone()[0]
        db.execute("UPDATE sd_job SET state='pending'")
    inc.worker.run()
    assert len(rows(inc, 'sd_offer')) == original
    assert len({r['dedup_key'] for r in rows(inc, 'sd_offer')}) == original


def test_invalid_excerpt_drops_only_that_field_and_unknowns(inc):
    prime(inc)
    add(inc)
    original = inc.worker.model.runner
    def invalid(args, **kw):
        result = original(args, **kw)
        envelope = json.loads(result.stdout)
        f = envelope['output']['offers'][0]['fields']
        sid = f['model']['source_id']
        f['moq_text']['excerpt'] = 'not present in any fictional source'
        f['currency'] = dict(value='unknown', source_id=sid, excerpt=f['model']['excerpt'])
        f['tax_basis'] = dict(value='unknown', source_id=sid, excerpt=f['model']['excerpt'])
        f['tax_rate_text'] = dict(value='13%', source_id=sid, excerpt='invented fictional tax')
        result.stdout = json.dumps(envelope)
        return result
    inc.worker.model.runner = invalid
    result = inc.worker.run()
    offer = result['changes']['offers'][0]
    assert offer['moq_text'] is None and offer['currency'] is None and offer['tax_rate_text'] is None
    assert offer['tax_basis'] == 'unknown'  # Existing NOT NULL schema contract.
    assert 'moq_text' not in json.loads(offer['field_sources_json'])


def test_supersedes_chain_gap_overlay_and_immutable_old_rows(inc):
    prime(inc)
    with sqlite3.connect(inc.env.target) as db:
        db.execute("INSERT INTO sd_gap VALUES('GMOQ','S1','P1',NULL,'moq_missing',4,'fictional','fictional','[]','open')")
    old = {t: rows(inc, t) for t in ('sd_offer', 'sd_gap', 'sd_product', 'sd_supplier')}
    add(inc, '125.00')
    first = inc.worker.run()['changes']['offers'][0]
    assert first['supersedes_offer_id'] == 'O1'
    add(inc, '127.00', day=21)
    second = inc.worker.run()['changes']['offers'][0]
    assert second['supersedes_offer_id'] == first['offer_id']
    for table, original in old.items():
        current = rows(inc, table)
        assert all(row in current for row in original)
    assert len(rows(inc, 'sd_gap_fill')) == 1
    assert rows(inc, 'sd_gap_fill')[0]['source_id'] == first['source_id']
    assert 'GMOQ' not in {g['gap_id'] for g in inc.env.service.gaps()['items']}
    projected = inc.env.service.supplier('S1')['products'][0]['offers']
    quote = next(o for o in projected if o['offer_id'] == second['offer_id'])
    assert quote['quoted_on'] == '2030-03-21' and quote['source_id'] and quote['evidence']
    family = inc.env.service.family(family_key('fictional-family'))
    assert any(o['offer_id'] == second['offer_id'] and o['quoted_on'] for p in family['configurations'] for o in p['offers'])


@pytest.mark.parametrize('price,anomalies', [('160.485', 0), ('160.486', 1), ('80.00', 1)])
def test_price_anomaly_strictly_over_thirty_percent(inc, price, anomalies):
    prime(inc)
    add(inc, price)
    result = inc.worker.run()
    assert len(result['changes']['reviews']) == anomalies
    assert all(r['review_kind'] == 'price_anomaly' for r in rows(inc, 'sd_review'))
    with sqlite3.connect(inc.env.target) as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO sd_review VALUES('bad','bad','routine','S1',NULL,NULL,'{}','open','2030')")


def test_model_failure_isolated_and_backoff(inc):
    prime(inc)
    add(inc, day=20)
    add(inc, day=21)
    original = inc.worker.model.runner
    def fail(args, **kw):
        if '2030-03-20' in kw['input']:
            inc.calls.append((args, kw['input']))
            return SimpleNamespace(returncode=1, stdout='{}', stderr='fictional sensitive upstream text')
        return original(args, **kw)
    inc.worker.model.runner = fail
    result = inc.worker.run()
    assert result['failed_windows'] == 1 and result['succeeded_windows'] == 1
    assert len(rows(inc, 'sd_offer')) == 4
    job = next(j for j in rows(inc, 'sd_job') if j['state'] == 'failed')
    assert job['not_before'] > job['finished_at']
    assert len([r for r in rows(inc, 'sd_run') if r['state'] == 'failed']) == 1
    assert 'sensitive' not in json.dumps(rows(inc, 'sd_run') + rows(inc, 'sd_job'))
    assert inc.worker.run()['calls'] == 0
    inc.clock[0] += timedelta(minutes=2)
    inc.worker.model.runner = original
    retry = inc.worker.run()
    assert retry['succeeded_windows'] == 1 and len(rows(inc, 'sd_offer')) == 5


def test_dry_run_preserves_both_databases_and_writes_private_reports(inc):
    add(inc)
    before = {p: p.read_bytes() for p in [inc.env.target, inc.env.wx]}
    report = inc.env.root / 'private-report'
    result = inc.worker.run(dry_run=True, report=report, since_rowid=1)
    assert len(result['changes']['offers']) == 1
    assert all(p.read_bytes() == data for p, data in before.items())
    assert {p.suffix for p in report.iterdir()} == {'.json', '.md'}
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in report.iterdir())
    assert 'sd_job' not in {r['name'] for r in table_info(inc)}
    with pytest.raises(ValueError, match='since_rowid_requires_dry_run'):
        inc.worker.run(since_rowid=0)


def table_info(inc):
    with sqlite3.connect(inc.env.target) as db:
        db.row_factory = sqlite3.Row
        return [dict(r) for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]


def test_budget_stop_accounting_and_next_day_resume(inc):
    prime(inc)
    add(inc, day=20)
    add(inc, day=21)
    inc.worker.run(max_windows=0)
    window = json.loads(rows(inc, 'sd_job')[0]['cursor_json'])
    limit = len(prompt_for(window['sources']).encode()) + 8192 + 50
    result = inc.worker.run(daily_budget_tokens=limit)
    assert result['calls'] == 1 and result['tokens'] == result['reported_tokens'] == 250
    assert result['budget_exhausted'] and len(rows(inc, 'sd_offer')) == 4
    assert any(j['state'] == 'budget_wait' for j in rows(inc, 'sd_job'))
    assert inc.worker.run(daily_budget_tokens=limit)['calls'] == 0
    inc.clock[0] += timedelta(days=1)
    assert inc.worker.run(daily_budget_tokens=limit)['calls'] == 1


def test_no_text_attachments_parsed_text_and_note_assets(inc):
    prime(inc)
    mid = add(inc, text='', attachment='')
    mid2 = add(inc, text='', attachment='DEMO-X 配置 config-alpha 报价 128.00 MOQ 10')
    with sqlite3.connect(inc.env.wx) as db:
        db.execute('INSERT INTO company_note_assets(app_id,message_id,parent_ordinal,ordinal,status,text) VALUES(?,?,?,?,?,?)',
            ('fictional-app', mid, 0, 1, 'ready', 'DEMO-X 配置 config-alpha 报价 126.00 MOQ 10'))
    result = inc.worker.run()
    assert result['coverage']['no_text'] == 3 and result['coverage']['attachments'] == 3
    assert len(result['changes']['offers']) == 2
    assert any(r['kind'] == 'note_asset' for r in rows(inc, 'sd_source'))


def test_supplier_scope_private_identity_and_no_name_merge(inc):
    prime(inc)
    add(inc, group='fictional-unbound@chatroom')
    for peer, role in [('fictional-supplier-peer', 'supplier'), ('fictional-buyer-peer', 'customer')]:
        mid = add(inc, group=peer)
        with sqlite3.connect(inc.env.wx) as db:
            db.execute('INSERT INTO wechat_private_messages VALUES(?,?,?,?,?)', ('fictional-app', mid, 'fictional-self', peer, 'inbound'))
            db.execute('INSERT INTO wechat_private_contacts VALUES(?,?,?,?,?)', ('fictional-app', 'fictional-self', peer, role, '虚构甲灯具'))
    result = inc.worker.run()
    assert result['calls'] == 1 and result['skipped_messages'] == 2
    offer = result['changes']['offers'][0]
    assert offer['supplier_id'] not in ('S1', 'S2')
    assert offer['product_id'] not in ('P1', 'P2')


def test_rule_alias_and_ambiguous_match_review(inc):
    prime(inc)
    add(inc)
    with sqlite3.connect(inc.env.target) as db:
        db.execute("UPDATE sd_product SET canonical_model='DEMO X' WHERE product_id='P1'")
        db.execute("UPDATE sd_supplier_product SET model_aliases_json='[]' WHERE supplier_id='S1' AND product_id='P1'")
    assert inc.worker.run()['changes']['offers'][0]['product_id'] == 'P1'
    with sqlite3.connect(inc.env.target) as db:
        db.execute('UPDATE sd_product SET config_json=? WHERE product_id=?', (json.dumps({'配置': 'config-alpha'}), 'P2'))
    add(inc, '126.00', day=21)
    result = inc.worker.run()
    assert result['changes']['offers'][0]['product_id'] not in ('P1', 'P2')
    assert result['changes']['reviews'][0]['review_kind'] == 'merge_conflict'


def test_projection_rollback_and_recovery_after_lease(inc):
    prime(inc)
    add(inc)
    with sqlite3.connect(inc.env.target) as db:
        db.execute("CREATE TRIGGER fictional_failure BEFORE INSERT ON sd_evidence BEGIN SELECT RAISE(ABORT,'fictional'); END")
    result = inc.worker.run()
    assert result['failed_windows'] == 1
    assert len(rows(inc, 'sd_offer')) == 3
    assert len(rows(inc, 'sd_projection')) == 0
    with sqlite3.connect(inc.env.target) as db:
        db.execute('DROP TRIGGER fictional_failure')
        db.execute("UPDATE sd_job SET state='running',lease_until='2020-01-01T00:00:00+00:00'")
    assert inc.worker.run()['succeeded_windows'] == 1


def test_updates_readonly_and_no_migration_on_read(inc):
    assert inc.env.service.updates()['ready'] is False
    prime(inc)
    add(inc, '200.00')
    inc.worker.now = lambda: datetime.now(timezone.utc)
    result = inc.worker.run()
    before = inc.env.target.read_bytes()
    update = inc.env.service.updates()
    assert update['ready'] and update['new_offers'] == 1 and update['pending_reviews'] == 1
    assert update['last_run_at'] and inc.env.target.read_bytes() == before


def test_pro_only_for_difficult_free_pool_and_stdin_limits(inc):
    prime(inc)
    add(inc)
    original = inc.worker.model.runner
    seen = []
    def difficult(args, **kw):
        result = original(args, **kw)
        env = json.loads(result.stdout)
        seen.append(1)
        env['output']['difficult'] = len(seen) == 1
        result.stdout = json.dumps(env)
        return result
    inc.worker.model.runner = difficult
    result = inc.worker.run()
    assert result['calls'] == 2 and result['tokens'] == 500
    assert [args[-1] for args, _ in inc.calls] == ['auto', 'auto']
    assert all(args[1:3] == ['run', 'bailian_free'] and len(args) == 5 for args, _ in inc.calls)
    with pytest.raises(ModelFailure, match='prompt_too_large'):
        AiTask(runner=inc.runner).extract('x' * 20001, FLASH)


def test_cursor_anchor_detects_reset_without_replaying_history(inc):
    prime(inc)
    with sqlite3.connect(inc.env.wx) as db:
        db.execute("UPDATE messages SET message_id='fictional-replaced' WHERE rowid=1")
    with pytest.raises(ValueError, match='source_cursor_reconciliation_required'):
        inc.worker.run()


def test_migration_is_additive_and_idempotent(inc):
    old = {t: rows(inc, t) for t in ('sd_offer', 'sd_gap', 'sd_source', 'sd_supplier')}
    with sqlite3.connect(inc.env.target) as db:
        migrate(db)
        migrate(db)
    for table, original in old.items():
        assert rows(inc, table) == original


def test_delayed_attachment_parse_and_content_revision(inc):
    prime(inc)
    mid = add(inc, text='', attachment='')
    first = inc.worker.run()
    assert first['calls'] == 0
    with sqlite3.connect(inc.env.wx) as db:
        db.execute("UPDATE company_media SET status='ready',text=? WHERE message_id=?",
            ('DEMO-X 配置 config-alpha 报价 126.00 MOQ 10', mid))
    second = inc.worker.run()
    assert len(second['changes']['offers']) == 1
    assert inc.worker.run()['calls'] == 0
    versions = [r for r in rows(inc, 'sd_source') if r['kind'] == 'message_attachment']
    assert any(r['parse_state'] == 'no_text' for r in versions)
    assert any(r['parse_state'] == 'read' for r in versions)


def test_budget_zero_has_no_call_and_unknown_usage_is_reserved(inc):
    prime(inc)
    add(inc)
    zero = inc.worker.run(daily_budget_tokens=0)
    assert zero['calls'] == 0 and zero['tokens'] == 0 and zero['budget_exhausted']
    assert not rows(inc, 'sd_run')
    inc.clock[0] += timedelta(days=1)
    original = inc.worker.model.runner
    def unmetered(args, **kw):
        result = original(args, **kw)
        env = json.loads(result.stdout)
        del env['usage']
        result.stdout = json.dumps(env)
        return result
    inc.worker.model.runner = unmetered
    result = inc.worker.run()
    assert result['unmetered_calls'] == 1 and result['tokens'] > 8192
    ledger = rows(inc, 'sd_run')[0]
    assert ledger['charged_tokens'] == ledger['reserved_tokens'] and ledger['total_tokens'] is None


def test_rollback_removes_only_owned_additions_and_restores_first_batch(inc):
    from console.dossiers.incremental.rollback import rollback
    from console.dossiers.incremental.worker import transaction
    baseline = {t: rows(inc, t) for t in ('sd_offer', 'sd_source', 'sd_product', 'sd_supplier', 'sd_supplier_product', 'sd_evidence', 'sd_gap')}
    prime(inc)
    add(inc, '200.00')
    inc.worker.run()
    with sqlite3.connect(inc.env.target, isolation_level=None) as db:
        db.execute('PRAGMA foreign_keys=ON')
        with transaction(db):
            removed = rollback(db)
    assert removed['sd_offer'] == 1
    for table, original in baseline.items():
        assert rows(inc, table) == original
    assert inc.env.service.updates()['ready'] is False


def test_window_limit_does_not_lose_queued_rows(inc):
    prime(inc)
    add(inc, day=20)
    add(inc, day=21)
    assert inc.worker.run(max_windows=1)['pending_windows'] == 1
    assert inc.worker.run(max_windows=1)['succeeded_windows'] == 1
    assert len(rows(inc, 'sd_offer')) == 5


def test_oversized_source_is_split_and_never_sends_oversized_stdin(inc):
    prime(inc)
    add(inc, text=('虚构备注' * 3000) + ' DEMO-X 配置 config-alpha 报价 126.00 MOQ 10')
    result = inc.worker.run()
    assert result['failed_windows'] == 0 and len(result['changes']['offers']) == 1
    assert all(len(prompt) <= 20000 and len(prompt.encode()) < 32768 for _, prompt in inc.calls)


def test_price_comparison_uses_historical_median(inc):
    prime(inc)
    for price, day in [('100.00', 20), ('140.00', 21), ('181.00', 22)]:
        add(inc, price, day=day)
        result = inc.worker.run()
    assert len(result['changes']['reviews']) == 1
    assert result['changes']['reviews'][0]['details']['median'] == '123.4500'


def test_gateway_failure_codes_are_allowlisted_and_arbitrary_text_is_discarded(inc):
    def fail(args, **kw):
        return SimpleNamespace(returncode=1, stdout=json.dumps(dict(status='failed', error='gateway_unavailable')), stderr='fictional secret')
    with pytest.raises(ModelFailure, match='gateway_unavailable'):
        AiTask(runner=fail).extract('fictional', FLASH)
    def arbitrary(args, **kw):
        return SimpleNamespace(returncode=1, stdout=json.dumps(dict(status='failed', error='fictional private business text')), stderr='fictional secret')
    with pytest.raises(ModelFailure, match='model_failed_or_unknown'):
        AiTask(runner=arbitrary).extract('fictional', FLASH)


def test_free_pool_envelope_with_dashscope_provider_is_accepted():
    def runner(args, **kw):
        return SimpleNamespace(returncode=0, stderr='', stdout=json.dumps(dict(
            status='ok', provider='dashscope', route='bailian_free', actual_model='qwen3.8-2.4t-a95b',
            output=dict(offers=[]), usage=dict(prompt_tokens=1, completion_tokens=1, total_tokens=2))))
    value, meta = AiTask(runner=runner).extract('fictional', FLASH)
    assert value == dict(offers=[]) and meta['actual_model'] == 'qwen3.8-2.4t-a95b'
    def other_route(args, **kw):
        return SimpleNamespace(returncode=0, stderr='', stdout=json.dumps(dict(
            status='ok', provider='dashscope', route='deepseek_official', actual_model='qwen3.8-2.4t-a95b',
            output=dict(offers=[]))))
    with pytest.raises(ModelFailure, match='unexpected_provider_identity'):
        AiTask(runner=other_route).extract('fictional', FLASH)
