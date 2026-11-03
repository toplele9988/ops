"""Polling uses fictional responses and a temporary database only."""
import json
import sqlite3

import pytest

from whatsapp import bridge


def test_poll_throttles_changes_and_failure(tmp_path):
    config = {'db': str(tmp_path / 'events.db'), 'session': 'fictional'}
    bridge.initialize(config)
    state = ['WORKING']
    calls = []
    def api(c, path, **kwargs):
        calls.append((path, kwargs))
        if state[0] == 'error':
            raise TimeoutError('fictional')
        return {'status': state[0], 'me': {'id': 'fictional-private'}}
    def poll(at):
        return bridge.poll_session(config, api, lambda: at)
    assert poll(10000)
    assert not poll(10299)
    assert not poll(11799)
    assert poll(11800)
    state[0] = 'FAILED'
    assert poll(11801)
    state[0] = 'error'
    with pytest.raises(TimeoutError):
        poll(11802)
    state[0] = 'unknown'
    with pytest.raises(ValueError):
        poll(11803)
    with sqlite3.connect(config['db']) as db:
        rows = db.execute('select raw_json from events order by received').fetchall()
    assert [json.loads(row[0])['payload'] for row in rows] == [
        {'source': 'poll', 'status': 'WORKING'}, {'source': 'poll', 'status': 'WORKING'},
        {'source': 'poll', 'status': 'FAILED'}]
    assert 'fictional-private' not in str(rows)
    assert all(path == '/api/sessions/fictional' and kwargs == {'method': 'GET', 'timeout': 10}
               for path, kwargs in calls)


def test_loop_logs_failure_without_error_details_and_waits(monkeypatch, capsys):
    monkeypatch.setattr(bridge, 'cfg', lambda: {'session_poll_interval_seconds': 300})
    def failure(config):
        raise RuntimeError('fictional-private-provider-response')
    def stop(seconds):
        assert seconds == 300
        raise StopIteration
    monkeypatch.setattr(bridge, 'poll_session', failure)
    monkeypatch.setattr(bridge.time, 'sleep', stop)
    with pytest.raises(StopIteration):
        bridge.poll_loop()
    assert json.loads(capsys.readouterr().out) == {'event': 'session_poll_failed'}


def test_intervening_event_changes_state(tmp_path):
    config = {'db': str(tmp_path / 'events.db'), 'session': 'fictional'}
    bridge.initialize(config)
    def api(c, path, **kwargs):
        return {'status': 'WORKING'}
    assert bridge.poll_session(config, api, lambda: 10000)
    raw = json.dumps({'payload': {'status': 'FAILED'}})
    with sqlite3.connect(config['db']) as db:
        db.execute('insert into events values(?,?,?,?,?,?,?)',
                   ('fictional-event', '', 'session.status', 'fictional', '', raw, 10001))
    assert bridge.poll_session(config, api, lambda: 10002)
