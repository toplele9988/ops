import json

from conftest import NOW, add, source


def poll(paths, state, at, identity):
    add(paths, 'whatsapp_session', at, identity=identity,
        raw=json.dumps({'payload': {'status': state, 'source': 'poll'}}))


def test_poll_working_is_fresh_evidence_and_expires(paths):
    poll(paths, 'WORKING', NOW - 10, 'fictional-poll')
    item = source(paths, 'whatsapp_session')
    assert item['status'] == 'ok' and item['status_source'] == 'poll'
    assert item['has_me'] is None and '轮询' in item['reason']
    assert source(paths, 'whatsapp_session', config={'whatsapp_session_max_age_seconds': 5})['status'] == 'unavailable'


def test_poll_failed_does_not_inflate_failure_events_or_hide_storm(paths):
    for i in range(20):
        add(paths, 'whatsapp_session', NOW - 100 + i, identity=f'fictional-{i}',
            raw=json.dumps({'payload': {'status': 'FAILED'}}))
    for i in range(3):
        poll(paths, 'FAILED', NOW - 10 + i, f'fictional-poll-{i}')
    item = source(paths, 'whatsapp_session')
    assert item['failed_count_1h'] == 20 and item['restart_storm'] is True
    assert item['status'] == 'interrupted' and item['status_source'] == 'poll'


def test_event_source_remains_distinct(paths):
    add(paths, 'whatsapp_session', raw=json.dumps({'payload': {'status': 'WORKING'}}))
    assert source(paths, 'whatsapp_session')['status_source'] == 'event'
