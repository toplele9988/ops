import asyncio
from contextlib import contextmanager
import json
import sqlite3
from types import SimpleNamespace

import pytest
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from console import v3_profiles_api
from console.profiles.schedule import ProfilesSchedule
from console.profiles.schema import FIELDS
from console.profiles.store import Profiles
from console.profiles.worker import Worker
from conftest import Inbox, NOW, Runner, conversation


def request(path, *, query='', login=True, valid=True):
    state = dict(password_principal=SimpleNamespace(username='fictional-owner') if login else None,
                 session=SimpleNamespace(authenticated=True), password_recheck=lambda: valid)
    return Request(dict(type='http', method='GET', path=path, query_string=query.encode(), headers=[],
                        state=state, path_params=dict(conversation_key='fictional-conversation')))


def test_authenticated_contract_arrays_filters_counts_and_recheck(tmp_path):
    path = tmp_path / 'p.sqlite3'
    Worker(path, inbox=Inbox(conversation()), runner=Runner(), clock=lambda: NOW).run()
    gate = SimpleNamespace(error_response=lambda r, c: PlainTextResponse('error', c))
    routes = v3_profiles_api.build(gate, path=path)
    listing, detail = routes
    def call(route, req):
        return asyncio.run(route.endpoint(req))
    assert call(listing, request(listing.path, login=False)).status_code == 403
    assert call(listing, request(listing.path, valid=False)).status_code == 403
    result = json.loads(call(listing, request(listing.path)).body)
    p = result['items'][0]
    assert set(p) == set(FIELDS)
    assert all(isinstance(p[f], list) for f in ('people_json', 'products_json', 'evidence_json'))
    assert result['counts'] == dict(kind=dict(customer=1), stage=dict(potential=1))
    assert json.loads(call(detail, request(detail.path)).body) == p
    assert json.loads(call(listing, request(listing.path, query='kind=supplier')).body)['items'] == []
    assert json.loads(call(listing, request(listing.path, query='q=ZX')).body)['items']
    for query in ('kind=bad', 'stage=bad', 'q=x&q=y', 'unsupported=x'):
        assert call(listing, request(listing.path, query=query)).status_code == 400
    req = request(detail.path)
    req.scope['path_params']['conversation_key'] = 'fictional-missing'
    assert call(detail, req).status_code == 404


def test_missing_db_reads_and_mounts_do_not_create_files(tmp_path):
    path = tmp_path / 'missing' / 'p.sqlite3'
    service = Profiles(path)
    assert service.get('fictional') is None
    assert service.list({}) == dict(items=[], counts=dict(kind={}, stage={}), updated_at=None)
    assert len(v3_profiles_api.build(SimpleNamespace(), path=path)) == 2
    assert not path.parent.exists()


class Store:
    def __init__(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.execute('CREATE TABLE operator_cursors(name TEXT PRIMARY KEY,generation TEXT,position INT)')

    @contextmanager
    def write(self):
        with self.db:
            yield self.db


def test_hourly_schedule_survives_restart_and_starts_independent_process():
    store, now, calls = Store(), [NOW], []
    def popen(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(poll=lambda: None)
    schedule = ProfilesSchedule(store, lambda: now[0], popen=popen, source_root='/fictional/src')
    assert schedule.poll()['started'] == NOW
    assert schedule.poll()['running']
    fresh = ProfilesSchedule(store, lambda: now[0], popen=popen)
    assert fresh.poll()['waiting']
    now[0] += 3600
    assert fresh.poll()['started'] == now[0]
    argv, kw = calls[0]
    assert argv[argv.index('--max-conversations') + 1] == '20'
    assert kw['start_new_session'] is True and kw['env']['PYTHONPATH'] == '/fictional/src'


def test_failed_spawn_is_still_hourly_claimed():
    store = Store()
    def fail(*a, **kw):
        raise OSError('fictional-failure')
    with pytest.raises(OSError):
        ProfilesSchedule(store, lambda: NOW, popen=fail).poll()
    assert ProfilesSchedule(store, lambda: NOW, popen=fail).poll()['waiting']


def test_cli_passes_repeat_only_and_limits_to_worker(tmp_path, monkeypatch, capsys):
    from console.profiles import __main__ as cli
    calls = []
    class FakeWorker:
        def run(self, **options):
            calls.append(options)
            return dict(source_ready=True, failed_conversations=0, missing=0, calls=0, profiles=[])
    monkeypatch.setattr(cli, 'Worker', FakeWorker)
    assert cli.main(['run', '--once', '--dry-run', '--report', str(tmp_path), '--max-conversations', '8',
                     '--max-windows', '6', '--only', 'fictional-a', '--only', 'fictional-b']) == 0
    assert calls[0]['dry_run'] and calls[0]['only'] == ['fictional-a', 'fictional-b']
    assert calls[0]['max_windows'] == 6 and calls[0]['max_conversations'] == 8
    assert 'profiles' not in json.loads(capsys.readouterr().out)
    with pytest.raises(SystemExit):
        cli.main(['run', '--once', '--dry-run'])
