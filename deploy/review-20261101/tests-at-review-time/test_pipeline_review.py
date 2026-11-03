import importlib
import json
import sqlite3
import time

import pytest

from conftest import ROOT, package

NOW = 1800000000


def modules(root, name):
    pkg = package(root, 'pipeline', name)
    return (importlib.import_module(pkg.__name__ + '.readers'),
            importlib.import_module(pkg.__name__ + '.scorecard'))


def continuity(readers, scorecard, times):
    with sqlite3.connect(':memory:') as db:
        db.execute('CREATE TABLE heartbeat_log(received_at REAL)')
        db.executemany('INSERT INTO heartbeat_log VALUES(?)', ((at,) for at in times))
        data = readers.heartbeat_continuity(db, NOW)
    return data, scorecard._a1(dict(ready=True, continuity=data))


def test_slow_cadence_cannot_report_full_day_100_percent(candidate):
    times = [NOW - 86400 + 60 + 179 * i for i in range(483)]
    before, metric = continuity(*modules(ROOT, 'slow_before'), times)
    assert before['rate'] == 1 and metric['result'] == 'pass'
    after, metric = continuity(*modules(candidate, 'slow_after'), times)
    assert after['rate'] < .7 and metric['result'] == 'fail'


def test_burst_duplicates_cannot_fill_empty_slots(candidate):
    times = [NOW - 86400 + 60 + 240 * i for i in range(360) for _ in range(150)]
    before, metric = continuity(*modules(ROOT, 'burst_before'), times)
    assert before['rate'] > .99 and metric['result'] == 'pass'
    after, metric = continuity(*modules(candidate, 'burst_after'), times)
    assert after['present'] <= 361 and metric['result'] == 'fail'


def test_previous_day_log_keeps_leading_outage_in_denominator(candidate):
    times = [NOW - 90000] + [NOW - 7200 + 120 * i for i in range(60)]
    data, metric = continuity(*modules(candidate, 'leading'), times)
    assert data['slots'] == 720 and data['max_consecutive_missing'] >= 650
    assert metric['result'] == 'fail'


@pytest.mark.parametrize('times,expected', [([], 'unknown'), ([NOW - 1], 'unknown'),
    ([NOW - 86400 + 120 * i for i in range(720)], 'pass'),
    ([NOW - 86400 + 60 + 120 * i + (25 if i % 2 else -25) for i in range(720)], 'pass'),
    ([NOW - 86400 + 60 + 120 * i + (-25 if i % 2 else 25) for i in range(720)], 'pass')])
def test_zero_partial_complete_and_jitter(candidate, times, expected):
    _, metric = continuity(*modules(candidate, 'boundaries'), times)
    assert metric['result'] == expected


def test_whatsapp_raw_fields_do_not_leave_sql(candidate):
    readers, _ = modules(candidate, 'private')
    with sqlite3.connect(':memory:') as db:
        db.execute('CREATE TABLE events(event_type TEXT,received REAL,raw_json TEXT)')
        db.execute('INSERT INTO events VALUES(?,?,?)', ('session.status', NOW, json.dumps({
            'payload': {'status': 'WORKING', 'body': 'FICTIONAL_BODY_SENTINEL'},
            'me': {'identity': 'FICTIONAL_ID_SENTINEL'}, 'extra': 'FICTIONAL_EXTRA_SENTINEL'})))
        result = readers.whatsapp_session(db, NOW)
    assert result['session_status'] == 'WORKING' and result['has_me'] is True
    assert 'SENTINEL' not in json.dumps(result)


def test_readonly_and_query_budget(candidate, tmp_path):
    readers, _ = modules(candidate, 'budget')
    path = tmp_path / 'source.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE evidence(value INTEGER)')
    before = path.read_bytes()
    with readers.read_db(path, .02) as db:
        with pytest.raises(sqlite3.OperationalError):
            db.execute('INSERT INTO evidence VALUES(1)')
        started = time.monotonic()
        with pytest.raises(sqlite3.OperationalError, match='interrupted'):
            db.execute('WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n WHERE x<100000000) SELECT sum(x) FROM n').fetchone()
        assert time.monotonic() - started < 2
    assert path.read_bytes() == before
