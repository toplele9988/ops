import json
from datetime import datetime, timezone

from console.pipeline.jobs import failed_jobs, read_failed_file
from console.pipeline.maintenance import windows
from console.pipeline.coverage import wechat_attachments


def utc(s):
    return datetime.fromisoformat(s.replace('Z', '+00:00')).timestamp()


def test_maintenance_overlap_cross_midnight_and_daily_cap(tmp_path):
    path = tmp_path / 'windows.json'
    rows = [dict(start_utc='2026-01-01T23:40:00Z', end_utc='2026-01-02T00:20:00Z'),
            dict(start_utc='2026-01-01T23:50:00Z', end_utc='2026-01-02T00:50:00Z')]
    path.write_text(json.dumps(dict(schema='icbu_maintenance_windows_v1', windows=rows)))
    result = windows(path)
    assert result == [(utc('2026-01-01T23:40:00Z'), utc('2026-01-02T00:00:00Z')),
                      (utc('2026-01-02T00:00:00Z'), utc('2026-01-02T00:30:00Z'))]
    assert windows(tmp_path / 'absent') == []


def test_failed_file_stale_and_tampered(tmp_path):
    path = tmp_path / 'failed.json'
    data = dict(schema='pipeline_failed_units_v1', generated_at=1000, items=[dict(unit='fictional.service', channel='wechat', failed_at=900, result='exit-code', exit_code=1)])
    path.write_text(json.dumps(data))
    assert failed_jobs(lambda now: read_failed_file(path, now), 1900)['count'] == 1
    assert failed_jobs(lambda now: read_failed_file(path, now), 1901)['status'] == 'unavailable'
    data['schema'] = 'tampered'
    path.write_text(json.dumps(data))
    assert failed_jobs(lambda now: read_failed_file(path, now), 1001)['status'] == 'unavailable'


def test_wechat_message_coverage_distinct_and_empty(tmp_path):
    import sqlite3
    path = tmp_path / 'fictional.sqlite3'
    with sqlite3.connect(path) as db:
        db.executescript('CREATE TABLE messages(app_id TEXT, message_id TEXT); CREATE TABLE company_media(app_id TEXT, message_id TEXT);')
    assert wechat_attachments(path, 1) == (0, 0)
    with sqlite3.connect(path) as db:
        db.executescript("INSERT INTO messages VALUES ('a','1'),('a','2'); INSERT INTO company_media VALUES ('a','1'),('a','1');")
    assert wechat_attachments(path, 1) == (1, 2)


def test_heartbeat_maintenance_excludes_only_missing_slots(tmp_path):
    import sqlite3
    from console.pipeline.readers import heartbeat_continuity
    db = sqlite3.connect(tmp_path / 'heartbeat.sqlite3')
    db.execute('CREATE TABLE heartbeat_log(received_at REAL)')
    db.executemany('INSERT INTO heartbeat_log VALUES (?)', [(1000,), (1480,)])
    plain = heartbeat_continuity(db, 1480)
    adjusted = heartbeat_continuity(db, 1480, [(1120, 1361)])
    assert (plain['present'], plain['slots'], plain['max_consecutive_missing']) == (2, 4, 2)
    assert (adjusted['present'], adjusted['slots'], adjusted['maintenance_slots'], adjusted['max_consecutive_missing']) == (2, 2, 2, 0)


def test_heartbeat_flood_cannot_fill_empty_slots(tmp_path):
    import sqlite3
    from console.pipeline.readers import heartbeat_continuity
    db = sqlite3.connect(tmp_path / 'flood.sqlite3')
    db.execute('CREATE TABLE heartbeat_log(received_at REAL)')
    db.executemany('INSERT INTO heartbeat_log VALUES (?)', [(1000 + i * 0.01,) for i in range(5000)] + [(5800,)])
    result = heartbeat_continuity(db, 5800)
    assert result['present'] <= 3 and result['max_consecutive_missing'] >= 30 and result['rate'] < 0.2
