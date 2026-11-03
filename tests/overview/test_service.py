from datetime import datetime
import json

import pytest

from console.overview import Overview
from console.overview.budget import FREE_MODELS, LEDGER_KEYS
from console.overview.service import PATHS
from console.pipeline.service import STATUS
from conftest import NOW, PRIVATE, channel, execute, inbound, probe, read, session, stage, write_json


def test_fixed_frontend_contract_and_unknown_history(healthy):
    data = read(healthy)
    assert set(data) == {'generated_at', 'summary', 'channels', 'model_budget', 'history'}
    assert datetime.fromisoformat(data['generated_at'].replace('Z', '+00:00')).utcoffset().total_seconds() == 0
    assert set(data['summary']) == {'state', 'headline', 'problems'}
    assert [item['key'] for item in data['channels']] == ['wechat', 'alibaba', 'whatsapp']
    for item in data['channels']:
        assert set(item) == {'key', 'label', 'state', 'headline', 'stages', 'notes'}
        assert item['state'] == 'ok'
        assert [s['key'] for s in item['stages']] == ['connected', 'collecting', 'stored', 'knowledge']
        for value in item['stages']:
            assert set(value) == {'key', 'label', 'state', 'detail', 'last_arrival_at', 'age_seconds',
                                  'count_24h', 'total', 'processed', 'pending', 'last_processed_at'}
            assert value['state'] in STATUS
    assert set(data['model_budget']) == {'deepseek_official', 'free_pools', 'note'}
    assert set(data['model_budget']['deepseek_official']) == {'currency', 'balance_micros', 'observed_at'}
    assert all(set(item) == {'model', 'remaining_tokens', 'observed_at', 'expires_on'}
               for item in data['model_budget']['free_pools'])
    assert set(data['history']['alibaba']) == {'messages', 'conversations', 'earliest_sent_at', 'latest_sent_at',
                                             'by_month', 'completeness', 'detail'}
    assert data['history']['alibaba']['completeness'] == 'unproven'
    assert data['summary']['state'] == 'unavailable'  # no history proof, even with good channels
    assert all(set(p) == {'channel', 'text', 'next_step'} for p in data['summary']['problems'])
    text = json.dumps(data, ensure_ascii=False)
    assert PRIVATE not in text and 'fake-account' not in text and 'fake-conversation' not in text
    assert '/var/lib/' not in text and str(healthy['inbound']) not in text
    assert stage(data, 'wechat', 'stored')['last_arrival_at'] is None
    assert stage(data, 'wechat', 'stored')['age_seconds'] is None
    assert stage(data, 'whatsapp', 'connected')['count_24h'] is None
    assert stage(data, 'whatsapp', 'connected')['last_arrival_at'] is None


@pytest.mark.parametrize('duration,expected', [(600, 'slow'), (601, 'slow'), (1800, 'slow'), (1801, 'interrupted'), (7200, 'interrupted')])
def test_alibaba_paused_thresholds_and_error(healthy, duration, expected):
    probe(healthy, state='paused', paused_seconds=duration, error='date_window_expired')
    item = stage(read(healthy), 'alibaba', 'collecting')
    assert item['state'] == expected
    assert f'至少 {duration} 秒' in item['detail']
    assert 'last_error=date_window_expired' in item['detail']
    assert stage(read(healthy), 'alibaba', 'connected')['state'] == 'ok'


@pytest.mark.parametrize('age,expected', [(300, 'ok'), (301, 'interrupted')])
def test_alibaba_heartbeat_wall_clock_deadline(healthy, age, expected):
    probe(healthy, at=NOW - age)
    data = read(healthy)
    assert stage(data, 'alibaba', 'collecting')['state'] == expected
    assert stage(data, 'alibaba', 'connected')['state'] == expected


def test_alibaba_stale_heartbeat_is_red_even_when_state_unknown(healthy):
    probe(healthy, state='not_a_state', at=NOW - 301)
    assert stage(read(healthy), 'alibaba', 'collecting')['state'] == 'interrupted'


def test_pause_cannot_use_first_seen_or_missing_log(healthy):
    probe(healthy, state='paused', error='login_required')
    item = stage(read(healthy), 'alibaba', 'collecting')
    assert item['state'] == 'unavailable'
    assert '暂停时长未知' in item['detail'] and 'last_error=login_required' in item['detail']


def test_known_pairing_error_remains_red_during_short_pause(healthy):
    probe(healthy, state='paused', paused_seconds=600, error='pairing_reconnect')
    item = stage(read(healthy), 'alibaba', 'collecting')
    assert item['state'] == 'interrupted'
    assert '重新配对' in item['detail']


@pytest.mark.parametrize('break_kind', ['running', 'unknown', 'instance', 'gap'])
def test_pause_run_breaks_on_state_instance_and_gap(healthy, break_kind):
    probe(healthy, state='paused', paused_seconds=3600, error='clock_skew', gap=break_kind == 'gap')
    if break_kind in ('running', 'unknown'):
        execute(healthy['probe'], 'UPDATE heartbeat_log SET state=? WHERE received_at=?',
                ('running' if break_kind == 'running' else None, NOW - 120))
    elif break_kind == 'instance':
        execute(healthy['probe'], 'UPDATE heartbeat_log SET instance_id=? WHERE received_at>?', ('new-fake', NOW - 120))
    item = stage(read(healthy), 'alibaba', 'collecting')
    assert item['state'] == 'slow'
    assert '至少 3600 秒' not in item['detail']


def test_pause_wrong_log_snapshot_time_is_unknown(healthy):
    probe(healthy, state='paused', paused_seconds=3600)
    execute(healthy['probe'], 'DELETE FROM heartbeat_log WHERE received_at=?', (NOW - 5,))
    assert stage(read(healthy), 'alibaba', 'collecting')['state'] == 'unavailable'


def test_error_body_and_unknown_probe_state_are_not_normal(healthy):
    probe(healthy, state='invented', error=PRIVATE)
    data = read(healthy)
    assert stage(data, 'alibaba', 'collecting')['state'] == 'unavailable'
    assert PRIVATE not in json.dumps(data)


def test_history_does_not_refresh_live_arrival(healthy):
    execute(healthy['inbound'], 'DELETE FROM inbound')
    inbound(healthy, 'old-live', at=NOW - 90000)
    inbound(healthy, 'new-history', at=NOW - 1, history=True)
    item = stage(read(healthy), 'alibaba', 'collecting')
    assert item['state'] == 'interrupted'
    assert item['age_seconds'] == 90000 and item['count_24h'] == 0


@pytest.mark.parametrize('lag,state', [(86400, 'ok'), (86401, 'interrupted'), (172800, 'interrupted')])
def test_wechat_knowledge_compares_success_to_message_time(healthy, lag, state):
    execute(healthy['calls'], 'UPDATE calls SET updated_at=?', (NOW - 10 - lag,))
    item = stage(read(healthy), 'wechat', 'knowledge')
    assert item['state'] == state
    assert f'处理滞后 {lag} 秒' in item['detail']
    assert '小时' in item['detail'] and item['last_processed_at'] is not None


def test_recent_hold_does_not_hide_last_success_lag(healthy):
    execute(healthy['calls'], 'UPDATE calls SET updated_at=?', (NOW - 200000,))
    execute(healthy['calls'], 'INSERT INTO calls(state,updated_at) VALUES(?,?)', ('hold', NOW - 1))
    item = stage(read(healthy), 'wechat', 'knowledge')
    assert item['state'] == 'interrupted'
    assert (item['processed'], item['pending']) == (1, 1)


@pytest.mark.parametrize('bad_time', [None, 0, -1, NOW + 1, 'invalid'])
def test_invalid_success_time_is_unknown(healthy, bad_time):
    execute(healthy['calls'], 'UPDATE calls SET updated_at=?', (bad_time,))
    item = stage(read(healthy), 'wechat', 'knowledge')
    assert item['state'] == 'unavailable' and item['last_processed_at'] is None


def test_alibaba_current_windows_not_old_audit_versions(healthy):
    for state_name in ['pending', 'retry_wait', 'infra_blocked', 'needs_repair']:
        execute(healthy['analysis'], 'INSERT INTO windows(version,state,updated_at) VALUES(?,?,?)',
                ('old', state_name, NOW - 5))
    execute(healthy['analysis'], 'INSERT INTO windows(version,state,updated_at) VALUES(?,?,?)',
            ('current', 'pending', NOW - 10))
    item = stage(read(healthy), 'alibaba', 'knowledge')
    assert (item['total'], item['processed'], item['pending']) == (2, 1, 1)
    assert item['state'] == 'slow'
    execute(healthy['analysis'], "UPDATE windows SET state='infra_blocked' WHERE version='current' AND state='pending'")
    assert stage(read(healthy), 'alibaba', 'knowledge')['state'] == 'interrupted'


@pytest.mark.parametrize('job_state,expected', [('indexed_no_supported_claims', 'ok'), ('raw_indexed', 'slow'),
    ('indexed_media_pending', 'slow'), ('pending', 'slow'), ('budget_wait', 'interrupted'),
    ('model_hold', 'interrupted'), ('not_a_real_state', 'unavailable')])
def test_whatsapp_knowledge_statuses(healthy, job_state, expected):
    execute(healthy['whatsapp'], 'UPDATE knowledge_jobs SET status=?', (job_state,))
    item = stage(read(healthy), 'whatsapp', 'knowledge')
    assert item['state'] == expected
    assert item['processed'] == int(expected == 'ok')
    assert 'not_a_real_state' not in item['detail']


@pytest.mark.parametrize('age,state', [(21600, 'ok'), (21601, 'unavailable')])
def test_whatsapp_session_expiry_and_quiet_period_text(healthy, age, state):
    execute(healthy['whatsapp'], "DELETE FROM events WHERE event_type='session.status'")
    session(healthy, at=NOW - age)
    item = stage(read(healthy), 'whatsapp', 'connected')
    assert item['state'] == state
    assert '静止期不产生状态事件，未必是故障' in item['detail']


def test_whatsapp_restart_storm_reuses_pipeline_rule(healthy):
    execute(healthy['whatsapp'], 'DELETE FROM events')
    for i in range(20):
        session(healthy, 'FAILED', NOW - 20 + i, str(i))
    item = stage(read(healthy), 'whatsapp', 'connected')
    assert item['state'] == 'interrupted' and '重启风暴' in item['detail']


@pytest.mark.parametrize('duration,state', [(300, 'slow'), (301, 'interrupted')])
def test_whatsapp_nonworking_duration_reuses_pipeline(healthy, duration, state):
    execute(healthy['whatsapp'], 'DELETE FROM events')
    session(healthy, 'STARTING', NOW - duration)
    item = stage(read(healthy), 'whatsapp', 'connected')
    assert item['state'] == state


def test_history_months_are_utc_sent_time_never_arrival_or_proof(healthy):
    execute(healthy['inbound'], 'DELETE FROM inbound')
    inbound(healthy, 'a', sent_at='2025-02-01T00:30:00+08:00')
    inbound(healthy, 'b', sent_at='2025-02-01T00:00:00Z', conv='second-fake')
    inbound(healthy, 'c', sent_at='invalid')
    result = read(healthy)['history']['alibaba']
    assert result['messages'] == 3 and result['conversations'] == 2
    assert result['earliest_sent_at'] == '2025-01-31T16:30:00Z'
    assert result['latest_sent_at'] == '2025-02-01T00:00:00Z'
    assert result['by_month'] == [dict(month='2025-01', count=1), dict(month='2025-02', count=1)]
    assert result['completeness'] == 'unproven'
    assert '1 条发送时间无效' in result['detail']


def test_bad_history_metadata_cannot_prove_zero_conversations(healthy):
    execute(healthy['inbound'], 'UPDATE inbound SET payload=?', ('{"sent_at":"invalid"}',))
    result = read(healthy)['history']['alibaba']
    assert result['messages'] == 1 and result['conversations'] is None
    assert result['earliest_sent_at'] is None and result['by_month'] == []
    assert result['completeness'] == 'unproven'


def test_zero_denominators_never_green(paths):
    data = read(paths)
    assert all(item['state'] == 'unavailable' for c in data['channels'] for item in c['stages'])
    assert data['summary']['state'] == 'unavailable'
    assert data['history']['alibaba']['conversations'] is None
    assert all(stage(data, key, 'knowledge')['processed'] == 0 for key in ('wechat', 'alibaba', 'whatsapp'))


@pytest.mark.parametrize('source,channel_key,stage_key', [
    ('calls', 'wechat', 'knowledge'), ('analysis', 'alibaba', 'knowledge'),
    ('probe', 'alibaba', 'connected'), ('whatsapp', 'whatsapp', 'connected'),
    ('wechat', 'wechat', 'stored'), ('inbound', 'alibaba', 'stored')])
def test_single_missing_source_does_not_hide_other_channels(healthy, source, channel_key, stage_key):
    healthy[source].unlink()
    data = read(healthy)
    item = stage(data, channel_key, stage_key)
    assert item['state'] == 'unavailable' and item['total'] is None
    other = 'whatsapp' if channel_key != 'whatsapp' else 'alibaba'
    assert channel(data, other)['state'] == 'ok'


def test_partial_paths_isolate_all_production_defaults(tmp_path):
    service = Overview(paths={'inbound': tmp_path / 'absent.sqlite3'}, clock=lambda: NOW)
    assert set(service.paths) == set(PATHS)
    assert all(path.parent == tmp_path for path in service.paths.values())
    assert service.read()['model_budget']['deepseek_official'] is None
    assert not list(tmp_path.iterdir())


def test_free_pools_and_official_balance_are_persisted_metadata(healthy):
    budget = read(healthy)['model_budget']
    assert budget['deepseek_official']['balance_micros'] == 1234567
    assert budget['deepseek_official']['currency'] == 'CNY'
    assert len(budget['free_pools']) == len(FREE_MODELS)
    assert all(item['remaining_tokens'] == 500000 for item in budget['free_pools'])
    assert PRIVATE not in json.dumps(budget)


@pytest.mark.parametrize('failure', ['absent', 'malformed', 'future', 'bad_currency', 'boolean_amount'])
def test_invalid_or_absent_official_balance_is_null(healthy, failure):
    if failure == 'absent':
        healthy['physical'].unlink()
    else:
        value = dict(provider='deepseek_official', total_micros=1, currency='CNY', observed_at=NOW - 1)
        if failure == 'future':
            value['observed_at'] = NOW + 1
        if failure == 'bad_currency':
            value['currency'] = PRIVATE
        if failure == 'boolean_amount':
            value['total_micros'] = True
        execute(healthy['physical'], 'UPDATE gate_balances SET balance_json=?',
                ('invalid' if failure == 'malformed' else json.dumps(value),))
    data = read(healthy)
    assert data['model_budget']['deepseek_official'] is None
    assert channel(data, 'wechat')['state'] == 'ok'


@pytest.mark.parametrize('failure', ['expired', 'future', 'missing_ledger', 'blocked', 'invalid_count', 'in_flight', 'missing_model', 'missing_block_state'])
def test_free_budget_unknown_never_zero_or_positive_on_failure(healthy, failure):
    if failure in ('expired', 'future'):
        value = json.loads(healthy['free_snapshot'].read_text())
        value['valid_until' if failure == 'expired' else 'verified_at'] = '2020-01-01T00:00:00Z' if failure == 'expired' else '2030-01-01T00:00:00Z'
        write_json(healthy['free_snapshot'], value)
    elif failure == 'missing_ledger':
        healthy['cpq'].unlink()
    else:
        value = json.loads(healthy['cpq'].read_text())
        for record in value['models'].values():
            if failure == 'blocked': record['blocked_reason'] = PRIVATE
            if failure == 'invalid_count': record['unknown_reserved_tokens'] = -1
            if failure == 'in_flight': record['in_flight'] = True
            if failure == 'missing_block_state': record.pop('blocked_reason')
        if failure == 'missing_model': value['models'] = {}
        write_json(healthy['cpq'], value)
    data = read(healthy)
    assert all(item['remaining_tokens'] is None for item in data['model_budget']['free_pools'])
    assert '未知' in data['model_budget']['note']
    assert PRIVATE not in json.dumps(data)


def test_unknown_reservations_are_not_released(healthy):
    for key in LEDGER_KEYS:
        value = json.loads(healthy[key].read_text())
        for record in value['models'].values():
            record.update(accounted_tokens=110, unknown_reserved_tokens=10)
        write_json(healthy[key], value)
    assert all(item['remaining_tokens'] == 500000 - 60 for item in read(healthy)['model_budget']['free_pools'])
