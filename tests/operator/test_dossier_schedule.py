"""Daily dossier update scheduling: once per Beijing day, separate process, health recorded."""
from datetime import datetime, timezone
from types import SimpleNamespace

from console.dossiers.incremental.schedule import DailyIncremental, last_summary
from console.operator.worker import Worker


class FakeProcess:
    def __init__(self, code=None):
        self.code = code

    def poll(self):
        return self.code


def at(text):
    return datetime.fromisoformat(text).astimezone(timezone.utc).timestamp()


def test_runs_once_per_beijing_day_after_hour(system, tmp_path):
    now = [at('2030-01-02T01:30:00+08:00')]
    started = []
    def popen(args, **kw):
        started.append((args, kw))
        return FakeProcess()
    daily = DailyIncremental(system.op.store, lambda: now[0], log_dir=tmp_path / 'runs', popen=popen, python='python-fictional')
    assert daily.poll() == dict(waiting=True)
    now[0] = at('2030-01-02T02:05:00+08:00')
    assert daily.poll() == dict(started='2030-01-02')
    args, kw = started[0]
    assert args == ['nice', '-n', '10', 'python-fictional', '-m', 'console.dossiers.incremental', 'run', '--once'] and kw['start_new_session'] is True
    assert daily.poll() == dict(running=True)
    daily.process.code = 0
    assert daily.poll() == dict(finished=True)
    assert daily.poll() == dict(done_today=True)
    # A restarted console does not repeat the same day.
    again = DailyIncremental(system.op.store, lambda: now[0], log_dir=tmp_path / 'runs', popen=popen)
    assert again.poll() == dict(done_today=True) and len(started) == 1
    now[0] = at('2030-01-03T03:00:00+08:00')
    assert again.poll() == dict(started='2030-01-03') and len(started) == 2


def test_failure_is_recorded_as_component_health(system, tmp_path):
    now = [at('2030-01-02T09:00:00+08:00')]
    daily = DailyIncremental(system.op.store, lambda: now[0], log_dir=tmp_path / 'runs',
                             popen=lambda args, **kw: FakeProcess(code=1))
    worker = Worker(system.op, None, [], incremental=daily)
    assert worker.run_once(include_tick=False)['dossier_incremental'] == dict(started='2030-01-02')
    assert worker.run_once(include_tick=False)['dossier_incremental'] is None
    with system.op.store.read() as db:
        row = db.execute("SELECT * FROM operator_health WHERE component='dossier_incremental'").fetchone()
    assert row['ok'] == 0 and row['error_type'] == 'RuntimeError'
    assert not system.model.calls and not system.bridge.calls


def test_last_summary(tmp_path):
    assert last_summary(tmp_path / 'missing') is None
    (tmp_path / '2030-01-02.json').write_text('{"new_offers": 3}')
    assert last_summary(tmp_path) == dict(day='2030-01-02', new_offers=3)
