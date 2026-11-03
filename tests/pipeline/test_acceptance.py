from contextlib import closing
import json
import sqlite3

import pytest

from console.pipeline import PipelineHealth
from conftest import NOW, add, execute, source


def read(paths, **options):
    return PipelineHealth(paths=paths, clock=lambda: NOW, **options).read()


def checks(data, channel):
    return {item['key']: item for item in next(row for row in data['scorecard'] if row['channel'] == channel)['metrics']}


def session(paths, state, at=NOW - 1, identity='fictional-session', me=None):
    add(paths, 'whatsapp_session', at, identity=identity,
        raw=json.dumps(dict(payload=dict(status=state), me=me, ignored='fictional-private-event-field')))


def test_restart_storm_is_not_healthy_even_when_bridge_keeps_reporting(paths):
    session(paths, 'WORKING', NOW - 4000, 'fictional-old-working', {'id': 'fictional-private-id'})
    for index in range(25):
        session(paths, 'FAILED', NOW - index * 2 - 2, f'fictional-failed-{index}')
        session(paths, 'STARTING', NOW - index * 2 - 1, f'fictional-starting-{index}')
    data = read(paths)
    item = next(row for row in data['sources'] if row['key'] == 'whatsapp_session')
    assert item['session_status'] == 'STARTING' and item['has_me'] is False
    assert item['restart_storm'] is True and item['status'] == 'interrupted'
    assert item['failed_count_1h'] == item['failed_count_24h'] == 25
    assert item['last_working_at'] == NOW - 4000
    assert '一直在失败重试' in item['reason'] and '重启风暴' in item['reason']
    assert '重新扫码' in item['next_step'] and 'WAHA 容器' in item['next_step']
    assert data['channels'][2]['status'] == 'interrupted'
    bridge = next(row for row in data['sources'] if row['key'] == 'whatsapp_events')
    assert bridge['status'] == 'ok' and bridge['auxiliary'] is True
    assert bridge['last_arrival_at'] is item['last_arrival_at'] is None
    assert bridge['time_basis'] == 'service_event'
    assert data['sources'][-1]['count_24h'] == 0
    assert 'fictional-private' not in json.dumps(data)


def test_working_in_last_ten_minutes_breaks_storm_and_continuous_failure(paths):
    for index in range(25):
        session(paths, 'FAILED', NOW - index * 20 - 1, f'fictional-failed-{index}')
    session(paths, 'WORKING', NOW - 30, 'fictional-working')
    item = source(paths, 'whatsapp_session')
    assert item['restart_storm'] is False and item['non_working_since'] == NOW - 21
    assert item['status'] == 'slow'


@pytest.mark.parametrize('seconds,status', [(300, 'slow'), (301, 'interrupted')])
def test_failed_empty_new_account_still_alarms(paths, seconds, status):
    session(paths, 'FAILED', NOW - seconds, 'fictional-first')
    session(paths, 'STARTING')
    data = read(paths)
    item = next(row for row in data['sources'] if row['key'] == 'whatsapp_session')
    assert item['status'] == data['channels'][2]['status'] == status
    assert data['sources'][-1]['status'] == 'never'
    assert checks(data, 'whatsapp')['session_working']['result'] == 'fail'


def test_working_new_account_waits_and_bridge_alone_cannot_make_it_green(paths):
    add(paths, 'whatsapp_events')
    assert read(paths)['channels'][2]['status'] == 'never'
    session(paths, 'WORKING', me={'id': 'fictional-private-identity'})
    data = read(paths)
    assert data['channels'][2]['status'] == 'ok'
    assert data['sources'][-1]['status'] == 'ok'
    assert checks(data, 'whatsapp')['last_arrival']['result'] == 'pass'
    add(paths, 'whatsapp_messages', NOW - 4 * 86400)
    assert read(paths)['channels'][2]['status'] == 'interrupted'


def test_stale_working_evidence_does_not_allow_new_account_exception(paths):
    session(paths, 'WORKING', NOW - 21601)
    data = read(paths)
    assert data['channels'][2]['status'] == 'unavailable'
    assert data['sources'][-1]['status'] == 'never'
    assert checks(data, 'whatsapp')['session_working']['result'] == 'unknown'


@pytest.mark.parametrize('raw', ['invalid-fictional-private-json', '{}',
    '{"payload":{"status":"fictional-private-status"},"me":{"id":"fictional-private-id"}}'])
def test_bad_session_json_or_state_is_unknown_without_content_leak(paths, raw):
    add(paths, 'whatsapp_session', raw=raw)
    data = read(paths)
    item = next(row for row in data['sources'] if row['key'] == 'whatsapp_session')
    assert item['status'] == 'unavailable' and item['session_status'] is None
    assert item['failed_count_24h'] is None and item['restart_storm'] is None
    assert 'fictional-private' not in json.dumps(data)


def test_sampling_limit_does_not_invent_full_window_counts_or_storm(paths):
    session(paths, 'WORKING', NOW - 100, 'fictional-working')
    for index in range(21):
        session(paths, 'FAILED', NOW - index - 1, f'fictional-failure-{index}')
    item = source(paths, 'whatsapp_session', config={'whatsapp_session_event_limit': 20})
    assert item['sample_truncated'] is True and item['sampled_events'] == 20
    assert item['last_working_at'] is item['failed_count_1h'] is item['failed_count_24h'] is None
    assert item['failed_count_24h_lower_bound'] == 20 and item['restart_storm'] is None
    assert '采样' in ' '.join(item['notes'])


def test_failed_windows_include_boundaries_and_exclude_future(paths):
    for index, at in enumerate((NOW - 86400, NOW - 3600, NOW - 86401, NOW + 1)):
        session(paths, 'FAILED', at, f'fictional-{index}')
    item = source(paths, 'whatsapp_session')
    assert item['failed_count_1h'] == 1 and item['failed_count_24h'] == 2
    assert item['last_seen_at'] == NOW - 3600


def test_empty_me_object_is_not_a_logged_in_identity(paths):
    add(paths, 'whatsapp_session', raw='{"payload":{"status":"WORKING"},"me":{  }}')
    assert source(paths, 'whatsapp_session')['has_me'] is False


@pytest.mark.parametrize('state', ['paused', 'running'])
def test_pairing_reconnect_explains_how_to_reload_without_uninstall(paths, state):
    add(paths, 'alibaba_probe', raw=json.dumps(dict(version='1.2.3', state=state, last_error='pairing_reconnect')))
    item = source(paths, 'alibaba_probe')
    assert item['status'] == 'interrupted'
    assert 'Win10 的采集网关在线，但浏览器扩展还没有与它重新配对' in item['reason']
    assert all(text in item['next_step'] for text in ('Chrome', '阿里聊天采集助手', '扩展程序页点一次', '不要卸载'))
    assert checks(read(paths), 'alibaba')['probe_state']['result'] == 'fail'


def test_running_probe_with_older_error_needs_evidence_before_acceptance(paths):
    add(paths, 'alibaba_probe', raw=json.dumps(dict(version='1.2.3', state='running', last_error='browser_disconnected')))
    data = read(paths)
    assert next(item for item in data['sources'] if item['key'] == 'alibaba_probe')['status'] == 'ok'
    assert checks(data, 'alibaba')['probe_state']['result'] == 'unknown'


def test_unknown_acceptance_checks_are_not_passes(paths):
    add(paths, 'alibaba_inbound')
    add(paths, 'alibaba_probe')
    data = read(paths)
    metrics = checks(data, 'alibaba')
    assert all(metrics[key]['result'] == 'unknown' for key in ('A1', 'A2', 'A3', 'A4', 'A6', 'A7', 'A8'))
    assert next(row for row in data['scorecard'] if row['channel'] == 'alibaba')['result'] == 'unknown'
    assert metrics['live_24h']['actual'] == '1 条'
    assert metrics['A5']['result'] == metrics['probe_age']['result'] == 'pass'
    assert all(set(('name', 'target', 'actual', 'result', 'source', 'method')) <= set(item)
               for channel in data['scorecard'] for item in channel['metrics'])


def test_missing_sources_cannot_pass_and_create_no_files(tmp_path):
    data = read({'inbound': tmp_path / 'missing.sqlite3'})
    assert all(channel['result'] == 'unknown' for channel in data['scorecard'])
    assert list(tmp_path.iterdir()) == []


def test_alibaba_day_check_does_not_count_the_night_as_daytime_silence(paths):
    add(paths, 'alibaba_inbound', NOW - 86400)
    morning = NOW - 4.5 * 3600
    early = PipelineHealth(paths=paths, clock=lambda: morning + 60).read()
    assert checks(early, 'alibaba')['A5']['result'] == 'unknown'
    assert '1.0 分钟' in checks(early, 'alibaba')['A5']['actual']
    later = PipelineHealth(paths=paths, clock=lambda: morning + 7201).read()
    assert checks(later, 'alibaba')['A5']['result'] == 'fail'


def test_stuck_pending_is_visible_even_with_fresh_messages_and_callbacks(paths):
    with closing(sqlite3.connect(paths['wechat'])) as db, db:
        db.executemany('INSERT INTO messages VALUES(?,?,?,?,?,?)',
                       [('fictional-app', f'fictional-{i}', NOW - 7 * 86400, '{}', 'pending', NOW - 6 * 86400) for i in range(200)])
    add(paths, 'wechat_messages')
    add(paths, 'wechat_callbacks')
    data = read(paths)
    item = next(row for row in data['sources'] if row['key'] == 'wechat_messages')
    assert item['count_24h'] == 1 and item['pending_count'] == 200
    assert item['pending_oldest_age_seconds'] == 7 * 86400
    assert item['pending_stalled_age_seconds'] == 6 * 86400
    assert item['status'] == data['channels'][1]['status'] == 'interrupted'
    assert '积压卡住' in item['reason'] and '200' in item['reason']
    assert checks(data, 'wechat')['pending']['result'] == 'fail'
    assert data['scorecard'][1]['result'] == 'fail'


def test_old_pending_with_recent_progress_is_not_called_stuck(paths):
    add(paths, 'wechat_messages')
    execute(paths['wechat'], "INSERT INTO messages VALUES('fictional-app','fictional-pending',?,'{}','pending',?)", (NOW - 7 * 86400, NOW - 10))
    data = read(paths)
    item = next(row for row in data['sources'] if row['key'] == 'wechat_messages')
    assert item['status'] == 'ok' and '积压卡住' not in item['reason']
    assert checks(data, 'wechat')['pending']['result'] == 'pass'


def test_pending_with_invalid_times_is_unknown(paths):
    execute(paths['wechat'], "INSERT INTO messages VALUES('fictional-app','fictional-pending','invalid','{}','pending',NULL)")
    assert checks(read(paths), 'wechat')['pending']['result'] == 'unknown'


def test_missing_pending_columns_do_not_hide_message_statistics(paths):
    add(paths, 'wechat_messages')
    execute(paths['wechat'], 'ALTER TABLE messages DROP COLUMN status')
    data = read(paths)
    assert checks(data, 'wechat')['pending']['result'] == 'unknown'
    assert next(row for row in data['sources'] if row['key'] == 'wechat_messages')['status'] == 'ok'


def test_callback_comparison_is_only_reference_and_not_an_arrival_timestamp(paths):
    add(paths, 'wechat_messages')
    for i in range(4):
        add(paths, 'wechat_callbacks', identity=f'fictional-callback-{i}')
    metrics = checks(read(paths), 'wechat')
    assert '比值 4.00' in metrics['callbacks_to_messages']['actual']
    assert metrics['callbacks_to_messages']['required'] is False
    assert '不作为丢失证据' in metrics['callbacks_to_messages']['target']
    assert metrics['last_arrival']['result'] == 'unknown'


def test_media_counts_keep_blocked_and_unknown_downloads_separate(paths):
    for i, state in enumerate(('indexed', 'blocked', 'download_unknown', 'fictional-private-state', 'fictional-private-state-2')):
        execute(paths['wechat'], 'INSERT INTO video_card_jobs VALUES(?,?)', (f'fictional-{i}', state))
    execute(paths['wechat'], "INSERT INTO receive_collection_jobs VALUES('fictional-job','complete','download_failed')")
    execute(paths['wechat'], "INSERT INTO company_note_assets VALUES('fictional-asset','ready','not_ready')")
    data = read(paths)
    metrics = checks(data, 'wechat')
    assert metrics['wechat_video']['actual'] == '1 / 5（20.0%）'
    assert metrics['video_blocked']['actual'] == metrics['video_download_unknown']['actual'] == '1 条'
    assert metrics['video_download_unknown']['result'] == 'fail'
    assert metrics['wechat_collection']['actual'] == metrics['wechat_notes']['actual'] == '1 / 1（100.0%）'
    assert '不保证解析' in metrics['wechat_collection']['method']
    assert 'fictional-private' not in json.dumps(data)


def test_zero_media_jobs_and_missing_media_tables_are_unknown(paths):
    assert checks(read(paths), 'wechat')['wechat_video']['result'] == 'unknown'
    execute(paths['wechat'], 'DROP TABLE video_card_jobs')
    metrics = checks(read(paths), 'wechat')
    assert metrics['wechat_video']['actual'] == '未知'
    assert metrics['video_blocked']['result'] == 'unknown'
    assert metrics['wechat_collection']['result'] == 'unknown'
    assert checks(read(paths), 'whatsapp')['failed_24h']['result'] == 'unknown'


def test_whatsapp_media_states_and_strict_channel_summary(paths):
    session(paths, 'WORKING')
    add(paths, 'whatsapp_messages')
    execute(paths['whatsapp'], "INSERT INTO media_jobs VALUES('fictional-job','archived','{}',?)", (NOW,))
    data = read(paths)
    assert data['scorecard'][2]['result'] == 'pass'
    execute(paths['whatsapp'], "INSERT INTO media_jobs VALUES('fictional-job-2','deferred','{}',?)", (NOW,))
    data = read(paths)
    assert data['scorecard'][2]['result'] == 'fail'
    assert '暂缓处理 1 条' in checks(data, 'whatsapp')['media_jobs']['actual']


def test_failed_jobs_are_optional_and_return_only_allowed_metadata(paths):
    assert read(paths)['failed_jobs']['status'] == 'not_connected'
    data = read(paths, failed_units_reader=lambda now: [dict(unit='fictional-worker.service', failed_at=NOW - 30,
                                                          secret='fictional-private-value')])
    assert data['failed_jobs']['count'] == 1
    assert data['failed_jobs']['items'] == [dict(unit='fictional-worker.service', failed_at=NOW - 30)]
    assert 'fictional-private' not in json.dumps(data)
    assert read(paths, failed_units_reader=lambda now: [])['failed_jobs']['count'] == 0


@pytest.mark.parametrize('row', [dict(unit='../fictional.service'), dict(unit='fictional.service\nprivate'),
    dict(unit='fictional.service', failed_at=NOW + 1), dict(unit='fictional.service', failed_at=float('nan'))])
def test_invalid_failed_unit_metadata_cannot_leak_or_break_other_cards(paths, row):
    data = read(paths, failed_units_reader=lambda now: [row])
    assert data['failed_jobs']['status'] == 'unavailable' and data['failed_jobs']['items'] == []
    assert len(data['scorecard']) == 3


def _log(paths, times):
    execute(paths['probe'], '''CREATE TABLE IF NOT EXISTS heartbeat_log(id INTEGER PRIMARY KEY,received_at REAL NOT NULL)''')
    for at in times:
        execute(paths['probe'], 'INSERT INTO heartbeat_log(received_at) VALUES(?)', (at,))


def test_a1_without_a_log_table_stays_unknown(paths):
    add(paths, 'alibaba_probe')
    assert checks(read(paths), 'alibaba')['A1']['result'] == 'unknown'


def test_a1_perfect_full_day_passes(paths):
    add(paths, 'alibaba_probe')
    _log(paths, [NOW - 86400 + 120 * index for index in range(720)])
    metric = checks(read(paths), 'alibaba')['A1']
    assert metric['result'] == 'pass'
    assert '100.0%' in metric['actual']


def test_a1_short_clean_history_is_not_a_pass(paths):
    add(paths, 'alibaba_probe')
    _log(paths, [NOW - 7200 + 120 * index for index in range(60)])
    metric = checks(read(paths), 'alibaba')['A1']
    assert metric['result'] == 'unknown'
    assert '2.0 小时' in metric['actual']


def test_a1_three_consecutive_misses_fail(paths):
    add(paths, 'alibaba_probe')
    times = [NOW - 7200 + 120 * index for index in range(60) if index not in (20, 21, 22)]
    _log(paths, times)
    metric = checks(read(paths), 'alibaba')['A1']
    assert metric['result'] == 'fail'
    assert '连续漏 3 槽' in metric['actual']


def test_a1_jitter_within_tolerance_still_counts(paths):
    add(paths, 'alibaba_probe')
    _log(paths, [NOW - 86400 + 60 + 120 * index + (25 if index % 2 else -25) for index in range(720)])
    assert checks(read(paths), 'alibaba')['A1']['result'] == 'pass'


def test_a1_new_single_heartbeat_is_unknown(paths):
    add(paths, 'alibaba_probe')
    _log(paths, [NOW - 30])
    assert checks(read(paths), 'alibaba')['A1']['result'] == 'unknown'
