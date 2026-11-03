from contextlib import closing
import hashlib
import json
import sqlite3

import pytest

from console.pipeline import PipelineHealth
from console.pipeline.config import DEFAULTS, resolve_config, validate
from console.pipeline.readers import READERS, read_db
from console.pipeline.service import effective_age
from conftest import NOW, add, execute, source


@pytest.mark.parametrize('key', [key for key in READERS if key != 'whatsapp_session'])
@pytest.mark.parametrize('age,expected', [(99, 'ok'), (100, 'ok'), (101, 'slow'), (200, 'slow'), (201, 'interrupted')])
def test_threshold_boundaries(paths, key, age, expected):
    add(paths, key, NOW - age)
    if key == 'whatsapp_events':
        add(paths, 'whatsapp_messages')
    item = source(paths, key, config={'thresholds': {key: {'slow_seconds': 100, 'interrupted_seconds': 200}}})
    assert item['status'] == expected
    assert item['last_seen_at'] == NOW - age
    assert item['reason'] and item['next_step']


def test_empty_connected_and_new_whatsapp(paths):
    data = PipelineHealth(paths=paths, clock=lambda: NOW).read()
    assert data['summary'] == '三个渠道：3 从未有'
    for item in data['sources']:
        assert item['status'] == 'never'
        assert item['last_seen_at'] is None
    assert all(metric['status'] == 'not_connected' for ch in data['channels'] for metric in ch['coverage'])
    add(paths, 'whatsapp_session')
    data = PipelineHealth(paths=paths, clock=lambda: NOW).read()
    assert data['summary'] == '三个渠道：1 正常，2 从未有'
    assert data['sources'][-1]['status'] == 'ok'
    assert '还没有联系人，正常' in data['sources'][-1]['reason']


def test_missing_files_do_not_create_anything(tmp_path):
    data = PipelineHealth(paths={'inbound': tmp_path / 'missing.sqlite3'}, clock=lambda: NOW).read()
    assert {item['status'] for item in data['sources']} == {'not_connected'}
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('sql', ['DROP TABLE inbound', 'ALTER TABLE inbound RENAME COLUMN created_at TO obsolete_at'])
def test_missing_schema_degrades_independently(paths, sql):
    execute(paths['inbound'], sql)
    item = source(paths, 'alibaba_inbound')
    assert item['status'] == 'not_connected'
    assert source(paths, 'wechat_messages')['status'] == 'never'


def test_corrupt_database_is_unavailable_without_raw_error(paths):
    paths['inbound'].write_bytes(b'fictional-private-corruption')
    data = PipelineHealth(paths=paths, clock=lambda: NOW).read()
    assert data['sources'][0]['status'] == 'unavailable'
    assert 'fictional' not in json.dumps(data)


def test_only_history_does_not_refresh_live_clock(paths):
    add(paths, 'alibaba_inbound', NOW - 3 * 86400, identity='fictional-live')
    add(paths, 'alibaba_inbound', NOW - 1, history=True, identity='fictional-history')
    item = source(paths, 'alibaba_inbound')
    assert item['status'] == 'interrupted'
    assert item['last_arrival_at'] == NOW - 1
    assert item['mix']['live']['last_arrival_at'] == NOW - 3 * 86400
    assert item['mix']['history']['ratio_24h'] == 1
    assert item['history_only'] and '只有历史在进' in item['reason']
    assert all(word in item['next_step'] for word in ('Win10', '暂停', '心跳', '版本'))


def test_never_live_with_history_is_explicit(paths):
    add(paths, 'alibaba_inbound', history=True)
    item = source(paths, 'alibaba_inbound')
    assert item['status'] == 'interrupted'
    assert '从未收到' in item['reason'] and '只有历史在进' in item['reason']


def test_history_reason_overrides_false_and_unknown_is_not_live(paths):
    rows = [(False, 'history_import', None), (True, None, None), (False, 'live_drafts_not_enabled', None),
            (None, None, '{"history":0}'), (None, None, '{"history":"false"}'), (None, None, 'invalid-json')]
    for i, (history, reason, raw) in enumerate(rows):
        add(paths, 'alibaba_inbound', history=history, reason=reason, raw=raw, identity=f'fictional-{i}')
    item = source(paths, 'alibaba_inbound')
    assert item['status'] == 'ok'
    assert item['count_24h'] == 6
    assert {k: v['count_24h'] for k, v in item['mix'].items()} == {'live': 1, 'history': 2, 'unknown': 3}
    assert sum(v['ratio_24h'] for v in item['mix'].values()) == 1


def test_unknown_live_status_is_not_normal(paths):
    add(paths, 'alibaba_inbound', raw='[]')
    assert source(paths, 'alibaba_inbound')['status'] == 'unavailable'


def test_rolling_windows_and_hour_boundaries(paths):
    start = NOW // 3600 * 3600 - 47 * 3600
    times = [NOW, NOW - 3600, NOW - 3601, NOW - 86400, NOW - 86401, start, start - 1, NOW + 1, -1, 'bad']
    for i, at in enumerate(times):
        add(paths, 'alibaba_inbound', at, identity=f'fictional-{i}')
    item = source(paths, 'alibaba_inbound')
    assert item['count_1h'] == 2
    assert item['count_24h'] == 4
    assert item['last_arrival_at'] == NOW
    assert len(item['hourly']) == 48 and item['hourly'][0] == dict(start_at=start, count=1)
    assert sum(hour['count'] for hour in item['hourly']) == 6
    assert item['invalid_timestamps'] == 3


def test_invalid_time_is_not_never_or_healthy(paths):
    add(paths, 'wechat_messages', 'bad')
    item = source(paths, 'wechat_messages')
    assert item['status'] == 'unavailable' and item['last_seen_at'] is None


def test_wechat_does_not_invent_arrival_timestamp(paths):
    add(paths, 'wechat_messages', NOW - 86400)
    add(paths, 'wechat_callbacks')
    item = source(paths, 'wechat_messages')
    assert item['last_arrival_at'] is None and item['time_basis'] == 'message_time'
    assert '回调仍有更新' in item['reason']
    assert '没有到达时间' in ' '.join(item['notes'])


def test_probe_latest_only_and_paused_are_explicit(paths):
    add(paths, 'alibaba_probe', state='paused')
    item = source(paths, 'alibaba_probe')
    assert item['count_1h'] is item['count_24h'] is item['hourly'] is None
    assert item['status'] == 'interrupted' and '暂停' in item['reason']
    assert item['script_version'] == '0.3.5.4'


def test_probe_and_payload_never_expose_private_fields(paths):
    add(paths, 'alibaba_probe', raw=json.dumps(dict(version='fictional-private-key', state='fictional-private-state', last_error='fictional-private-error')))
    add(paths, 'alibaba_inbound', raw=json.dumps(dict(history=False, content='fictional-private-content', account='fictional-private-account')))
    data = PipelineHealth(paths=paths, clock=lambda: NOW).read()
    assert 'fictional-private' not in json.dumps(data)
    assert data['sources'][1]['script_version'] is None


def test_business_hours_discount_and_no_0800_jump():
    # NOW is 12:30 Beijing; last night 00:00 -> this morning 08:00.
    morning = NOW - 4.5 * 3600
    midnight = morning - 8 * 3600
    day = DEFAULTS['alibaba_day']
    assert effective_age(midnight, morning, day) == 2 * 3600
    assert effective_age(midnight, morning + 1, day) - effective_age(midnight, morning - 1, day) == 1.25
    assert effective_age(NOW - 3600, NOW, day) == 3600
    assert effective_age(NOW - 86400, NOW, day) == 18 * 3600


def test_night_and_custom_day_window_change_alarm(paths):
    add(paths, 'alibaba_inbound', NOW - 3600)
    threshold = {'alibaba_inbound': {'slow_seconds': 1200, 'interrupted_seconds': 2400}}
    assert source(paths, 'alibaba_inbound', config={'thresholds': threshold})['status'] == 'interrupted'
    assert source(paths, 'alibaba_inbound', config={'thresholds': threshold,
        'alibaba_day': {'start': '16:00', 'end': '24:00', 'night_multiplier': 4}})['status'] == 'ok'


def test_new_whatsapp_ignores_old_session_events_but_not_real_messages(paths):
    add(paths, 'whatsapp_session')
    assert source(paths, 'whatsapp_messages')['status'] == 'ok'
    add(paths, 'whatsapp_messages', NOW - 10 * 86400)
    assert source(paths, 'whatsapp_messages')['status'] == 'interrupted'


@pytest.mark.parametrize('sql', ["INSERT INTO recipients VALUES('fictional-session','fictional-account','fictional-chat','inbound',1)", 'DROP TABLE recipients'])
def test_empty_whatsapp_requires_evidence_of_no_contacts(paths, sql):
    execute(paths['whatsapp'], sql)
    item = source(paths, 'whatsapp_messages')
    assert item['status'] == 'never' and '还没有联系人' not in item['reason']


def test_event_activity_does_not_mask_whatsapp_message_gap(paths):
    add(paths, 'whatsapp_events')
    add(paths, 'whatsapp_messages', NOW - 10 * 86400)
    data = PipelineHealth(paths=paths, clock=lambda: NOW).read()
    assert data['channels'][2]['status'] == 'interrupted'
    assert '事件仍有更新' in data['sources'][-1]['reason']


@pytest.mark.parametrize('value', [None, [], {'unknown': 1}, {'thresholds': {'alibaba_probe': {'slow_seconds': True}}},
    {'thresholds': {'alibaba_probe': {'slow_seconds': 1500}}}, {'alibaba_day': {'start': '24:00'}},
    {'alibaba_day': {'start': '25:00'}}, {'alibaba_day': {'night_multiplier': float('nan')}},
    {'paths': {'inbound': '../private'}}, {'query_timeout_seconds': 10}])
def test_invalid_config_rejected(value):
    with pytest.raises((ValueError, TypeError)):
        validate(value)


def test_config_file_environment_and_safe_fallback(tmp_path, monkeypatch):
    path = tmp_path / 'config.json'
    path.write_text('{"thresholds":{"alibaba_probe":{"slow_seconds":12,"interrupted_seconds":60}}}')
    monkeypatch.setenv('IMGW_PIPELINE_CONFIG', str(path))
    config, notes = resolve_config()
    assert config['thresholds']['alibaba_probe']['slow_seconds'] == 12 and not notes
    path.write_text('fictional-private-invalid-config')
    config, notes = resolve_config()
    assert config == DEFAULTS and notes and 'fictional' not in str(notes)
    link = tmp_path / 'link.json'
    link.symlink_to(path)
    assert resolve_config(path=link)[1]


def test_read_only_no_file_mutation_or_sidecars(paths):
    add(paths, 'alibaba_inbound')
    root = paths['inbound'].parent
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.iterdir()}
    PipelineHealth(paths=paths, clock=lambda: NOW).read()
    assert before == {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.iterdir()}
    with read_db(paths['inbound']) as db:
        with pytest.raises(sqlite3.OperationalError):
            db.execute('DELETE FROM inbound')


def test_wal_commits_are_visible_and_uncommitted_changes_are_not(paths):
    with closing(sqlite3.connect(paths['inbound'])) as writer:
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute('PRAGMA wal_autocheckpoint=0')
        add(paths, 'alibaba_inbound')
        assert source(paths, 'alibaba_inbound')['count_24h'] == 1
        writer.execute("INSERT INTO inbound VALUES('fictional-uncommitted','{}','archived',NULL,?)", (NOW,))
        assert source(paths, 'alibaba_inbound')['count_24h'] == 1
        writer.rollback()
        add(paths, 'alibaba_inbound', identity='fictional-committed')
        assert source(paths, 'alibaba_inbound')['count_24h'] == 2


def test_permission_error_is_generic_and_other_sources_survive(paths, monkeypatch):
    from console.pipeline import service
    original = service.read_db

    def restricted(path, seconds):
        if path == paths['inbound']:
            raise PermissionError('fictional-private-path')
        return original(path, seconds)

    monkeypatch.setattr(service, 'read_db', restricted)
    data = PipelineHealth(paths=paths, clock=lambda: NOW).read()
    assert data['sources'][0]['status'] == 'unavailable'
    assert data['sources'][1]['status'] == 'never'
    assert 'fictional-private' not in json.dumps(data)


def test_query_budget_interrupts_expensive_sql(paths):
    with read_db(paths['inbound'], seconds=.01) as db:
        with pytest.raises(sqlite3.OperationalError, match='interrupted'):
            db.execute('WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM n WHERE x<10000000) SELECT sum(x) FROM n').fetchone()


def test_optional_coverage_counts_failures_and_missing(paths):
    def missing(now):
        raise FileNotFoundError

    def failed(now):
        raise sqlite3.OperationalError('fictional-private-sql')

    readers = {('alibaba', 'attachments'): lambda now: (1, 4), ('alibaba', 'translations'): lambda now: (0, 0),
               ('wechat', 'attachments'): missing, ('wechat', 'translations'): failed,
               ('whatsapp', 'attachments'): lambda now: (10, 2), ('whatsapp', 'translations'): lambda now: None}
    data = PipelineHealth(paths=paths, clock=lambda: NOW, coverage_readers=readers).read()
    metrics = [metric for ch in data['channels'] for metric in ch['coverage']]
    assert [m['status'] for m in metrics] == ['ready', 'ready', 'not_connected', 'unavailable', 'unavailable', 'not_connected']
    assert metrics[0]['ratio'] == .25 and metrics[1]['ratio'] is None
    assert 'fictional-private' not in json.dumps(data)


def test_paused_heartbeat_reason_codes_become_plain_advice(paths):
    add(paths, 'alibaba_probe', raw=json.dumps(dict(version='0.2.1', state='paused', last_error='browser_disconnected')))
    item = source(paths, 'alibaba_probe')
    assert item['status'] == 'interrupted' and '浏览器扩展' in item['reason'] and 'Chrome' in item['next_step']


def test_paused_heartbeat_unknown_code_is_shown_but_private_text_is_not(paths):
    add(paths, 'alibaba_probe', raw=json.dumps(dict(version='0.2.1', state='paused', last_error='some_new_reason')))
    item = source(paths, 'alibaba_probe')
    assert '原因代码 some_new_reason' in item['reason']
    add(paths, 'alibaba_probe', identity='fictional-2', at=NOW - 5,
        raw=json.dumps(dict(version='0.2.1', state='paused', last_error='Contains spaces and 客户名')))
    assert '客户名' not in json.dumps(PipelineHealth(paths=paths, clock=lambda: NOW).read(), ensure_ascii=False)


def test_history_false_without_live_provenance_is_not_counted_as_live(paths):
    add(paths, 'alibaba_inbound', raw=json.dumps(dict(history=False, role='buyer', content='fictional')))
    item = source(paths, 'alibaba_inbound')
    assert item['mix']['live']['count_24h'] == 0
    assert item['mix']['unknown']['count_24h'] == 1
    assert item['status'] == 'unavailable' or item['status'] == 'interrupted'
