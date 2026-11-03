from unittest.mock import Mock

import pytest


@pytest.mark.parametrize('expiry,expected', [('2030-01-09', 'expired'), ('2030-01-10', 'expiring'), ('2030-01-13', 'expiring'), ('2030-01-14', None)])
def test_customer_cost_and_freight_snapshot_expiry_boundaries(env, expiry, expected):
    did, rid, lid, cid, *_ = env.good_round()
    with env.quotes.store.write() as db:
        db.execute('UPDATE quote_rounds SET valid_until=? WHERE id=?', (expiry, rid))
        db.execute('UPDATE quote_lines SET cost_valid_until=? WHERE id=?', (expiry, lid))
        db.execute('UPDATE quote_costs SET valid_until=? WHERE id=?', (expiry, cid))
    runner = Mock(side_effect=AssertionError('patrol must never use AI'))
    service = env.service(runner=runner)
    result = service.run_patrol('fictional-owner')
    alerts = service.alerts()
    if expected:
        assert {a['kind'] for a in alerts} == {p + '_' + expected for p in ('quote', 'offer', 'freight')}
        assert all(a['severity'] == ('high' if expected == 'expired' else 'medium') for a in alerts)
    else:
        assert alerts == []
    assert result['source'] == 'rules' and result['output_text']
    runner.assert_not_called()


def test_no_reply_and_party_waiting_resolve_on_customer_acknowledgement(env):
    key = env.conv(role='seller', text='Please confirm the quote', age=5)
    did, rid, *_ = env.good_round(key=key)
    env.qw('status', rid, action='send', confirm='1')
    env.clock.advance(48 * 3600)
    env.roles.run_patrol('fictional-owner')
    assert 'no_reply' not in {a['kind'] for a in env.roles.alerts()}
    env.clock.advance(1)
    env.roles.run_patrol('fictional-owner')
    assert {'no_reply', 'waiting_too_long'} <= {a['kind'] for a in env.roles.alerts()}
    env.message(key, text='Thanks')
    env.roles.run_patrol('fictional-owner')
    assert not {'no_reply', 'waiting_too_long'} & {a['kind'] for a in env.roles.alerts()}
    with env.store.read() as db:
        assert all(r[0] is not None for r in db.execute("SELECT resolved_at FROM role_alerts WHERE kind IN ('no_reply','waiting_too_long')"))


def test_payment_and_stale_thresholds_and_auto_resolution(env):
    payment = env.deal(stage='awaiting_payment', title='虚构待付款')
    stale = env.deal(title='虚构静默案子')
    env.clock.advance(3 * 86400 - 1)
    env.roles.run_patrol('fictional-owner')
    assert not env.roles.alerts()
    env.clock.advance(1)
    env.roles.run_patrol('fictional-owner')
    assert env.roles.alerts(payment)[0]['kind'] == 'payment_overdue'
    env.clock.advance(4 * 86400)
    env.roles.run_patrol('fictional-owner')
    assert any(a['kind'] == 'deal_stale' for a in env.roles.alerts(stale))
    env.historical_paid(payment)  # Historical won deal without a payment ledger.
    env.dw('event', stale, note='虚构人工跟进')
    env.roles.run_patrol('fictional-owner')
    assert env.roles.alerts() == []


def test_dedup_last_seen_dismiss_and_new_episode(env):
    did = env.deal(stage='awaiting_payment')
    env.clock.advance(3 * 86400)
    first = env.roles.run_patrol('fictional-owner')
    alert = env.roles.alerts()[0]
    env.clock.advance(10)
    second = env.roles.run_patrol('fictional-owner')
    assert first['id'] == second['id'] and second['reused']
    after = env.roles.alerts()[0]
    assert after['id'] == alert['id'] and after['first_seen_at'] == alert['first_seen_at']
    assert after['last_seen_at'] > alert['last_seen_at']
    env.roles.write('alert_dismiss', alert['id'], {}, 'fictional-decider')
    env.roles.run_patrol('fictional-owner')
    assert env.roles.alerts() == []
    assert env.roles.alerts(include_dismissed=True)[0]['dismissed_by'] == 'fictional-decider'
    env.dw('update', did, status='paused')
    env.roles.run_patrol('fictional-owner')
    env.dw('update', did, status='open')
    env.roles.run_patrol('fictional-owner')
    assert env.roles.alerts()[0]['id'] != alert['id']


def test_missing_channel_does_not_resolve_existing_chat_alerts(env):
    key = env.conv(role='seller', text='Please confirm measurements')
    did = env.deal(key=key)
    env.clock.advance(49 * 3600)
    env.roles.run_patrol('fictional-owner')
    assert any(a['kind'] == 'waiting_too_long' for a in env.roles.alerts())
    env.inbox.missing.add(key)
    result = env.roles.run_patrol('fictional-owner')
    assert result['output_json']['incomplete_deals'] == [did]
    assert any(a['kind'] == 'waiting_too_long' for a in env.roles.alerts())


def test_replaced_quote_resolves_old_expiry_alerts_and_severity_order(env):
    did, rid, lid, cid, *_ = env.good_round()
    env.qw('update', rid, valid_until='2030-01-09')
    env.roles.run_patrol('fictional-owner')
    expired = next(a for a in env.roles.alerts() if a['kind'] == 'quote_expired')
    new = env.qw('create', did)
    env.qw('update', new, valid_until='2030-01-12')
    env.roles.run_patrol('fictional-owner')
    assert expired['id'] not in {a['id'] for a in env.roles.alerts()}
    assert any(a['kind'] == 'quote_expiring' and a['ref_id'] == str(new) for a in env.roles.alerts())


def test_repeating_no_alert_state_after_resolution_is_valid(env):
    did = env.deal(stage='awaiting_payment')
    env.roles.run_patrol('fictional-owner')
    env.clock.advance(3 * 86400)
    env.roles.run_patrol('fictional-owner')
    env.dw('update', did, status='paused')
    env.roles.run_patrol('fictional-owner')
    env.dw('update', did, status='open')
    env.roles.run_patrol('fictional-owner')
    env.dw('update', did, status='paused')
    assert env.roles.run_patrol('fictional-owner')['output_json']['alerts'] == []


def test_threshold_overrides_and_source_changes_invalidate_cached_patrol(env):
    from dataclasses import replace
    key = env.conv(role='seller', text='Please confirm')
    did = env.deal(key=key, stage='awaiting_payment')
    env.clock.advance(86400)
    service = env.service(config=replace(env.roles.config, reply_hours=12, payment_days=1, stale_days=1))
    first = service.run_patrol('fictional-owner')
    assert {a['kind'] for a in service.alerts()} == {'waiting_too_long', 'payment_overdue', 'deal_stale'}
    env.dw('task', did, title='虚构新待办')
    second = service.run_patrol('fictional-owner')
    assert second['id'] != first['id']
    assert 'deal_stale' not in {a['kind'] for a in service.alerts()}


def test_quote_reply_uses_detail_history_even_when_overview_only_has_seller(env):
    from unittest.mock import patch
    key = env.conv(age=5, channel='whatsapp')
    did, rid, *_ = env.good_round(key=key)
    env.qw('status', rid, action='send', confirm='1')
    env.clock.advance(86400)
    env.message(key, role='buyer', text='I received the quote and have a question')
    env.clock.advance(3600)
    env.message(key, role='seller', text='Here is the answer, please confirm')
    env.clock.advance(49 * 3600)
    full = env.inbox.conversations[key]
    overview = dict(full, messages=[full['messages'][-1]])
    with patch.object(env.inbox, 'read', lambda *a: dict(ready=True, conversations={key: overview})), \
         patch.object(env.inbox, 'detail', lambda *a: full):
        env.roles.run_patrol('fictional-owner')
    kinds = {a['kind'] for a in env.roles.alerts()}
    assert 'no_reply' not in kinds and 'waiting_too_long' in kinds


def test_unavailable_detail_does_not_invent_no_reply_from_overview(env):
    from unittest.mock import patch
    key = env.conv(role='seller', text='Please confirm', age=5)
    did, rid, *_ = env.good_round(key=key)
    env.qw('status', rid, action='send', confirm='1')
    env.clock.advance(49 * 3600)
    with patch.object(env.inbox, 'detail', side_effect=RuntimeError('fictional unavailable')):
        result = env.roles.run_patrol('fictional-owner')
    assert result['output_json']['incomplete_deals'] == [did]
    assert not {'no_reply', 'waiting_too_long'} & {a['kind'] for a in env.roles.alerts()}
