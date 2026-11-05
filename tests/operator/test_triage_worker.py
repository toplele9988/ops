"""Optional triage polling records health and never applies business ownership."""
from types import SimpleNamespace
from unittest.mock import Mock

from console.operator.worker import Worker
from console.roles.automatic import AutomaticTriage
from console.roles.service import Roles


def health(system):
    with system.op.store.read() as db:
        return dict(db.execute("SELECT * FROM operator_health WHERE component='triage'").fetchone())


def test_optional_triage_component_success_failure_and_recovery(system):
    poller = SimpleNamespace(poll=Mock(return_value={'scanned': 0}))
    worker = Worker(system.op, None, [], triage=poller)
    assert worker.run_once(include_tick=False)['triage'] == {'scanned': 0}
    row = health(system)
    assert row['ok'] == 1 and row['failures'] == 0 and row['at'] == system.now[0]
    poller.poll.side_effect = RuntimeError('fictional private error text')
    assert worker.run_once(include_tick=False)['triage'] is None
    row = health(system)
    assert row['ok'] == 0 and row['error_type'] == 'RuntimeError' and row['failures'] == 1
    assert 'private' not in str(row)
    poller.poll.side_effect = None
    worker.run_once(include_tick=False)
    assert health(system)['ok'] == 1 and health(system)['error_type'] is None
    assert not system.model.calls and not system.bridge.calls


def test_absent_triage_is_optional(system):
    assert 'triage' not in Worker(system.op, None, []).run_once(include_tick=False)


def test_missing_dossiers_scans_quietly_with_healthy_triage_and_durable_throttle(system, tmp_path):
    path = tmp_path / 'never-created' / 'dossiers.sqlite3'
    inbox = SimpleNamespace(read=lambda now: dict(ready=True, conversations={}))
    runner = Mock(side_effect=AssertionError('automatic triage cannot invoke runner'))
    roles = Roles(system.archive.path, inbox=inbox, runner=runner, clock=system.op.clock,
                  dossiers_path=path, hub=SimpleNamespace())
    with system.archive.read() as db:
        before = {table: [tuple(r) for r in db.execute('SELECT * FROM ' + table)]
                  for table in ('parties', 'identities', 'deals')}
    poller = AutomaticTriage(roles, system.op.store, system.op.clock)
    worker = Worker(system.op, None, [], triage=poller)
    result = worker.run_once(include_tick=False)['triage']
    assert result['items'] == [] and result['scanned'] == 0
    assert health(system)['ok'] == 1 and not path.parent.exists()
    # A fresh worker/poller object still consults the persisted admission clock.
    restarted = Worker(system.op, None, [], triage=AutomaticTriage(roles, system.op.store, system.op.clock))
    assert restarted.run_once(include_tick=False)['triage'] == dict(skipped=True)
    assert health(system)['ok'] == 1
    with system.archive.read() as db:
        for table, rows in before.items():
            assert rows == [tuple(r) for r in db.execute('SELECT * FROM ' + table)]
    runner.assert_not_called()
    assert not system.model.calls and not system.bridge.calls


def test_host_constructs_roles_with_same_archive_and_shared_inbox_without_io(tmp_path, monkeypatch):
    from console.operator import host
    inbox = SimpleNamespace(read=lambda now: dict(ready=True, conversations={}))
    factory = Mock(return_value=inbox)
    monkeypatch.setattr(host, 'shared_inbox', factory)
    archive = tmp_path / 'archive.sqlite3'
    op, worker = host.assemble(operator_path=tmp_path / 'operator.sqlite3', archive_path=archive,
        inputs=[], hub=SimpleNamespace(), dossiers_path=tmp_path / 'missing-dossiers.sqlite3',
        language=lambda key: None, quote_policy=lambda deal: None)
    factory.assert_called_once_with()
    assert worker.triage.roles.store.path == archive and worker.triage.roles.inbox is inbox
    assert worker.triage.roles.runner is None and worker.triage.clock == op.clock
    assert not op.store.path.exists() and not archive.exists()
