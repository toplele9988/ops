from concurrent.futures import ThreadPoolExecutor
import errno
import hashlib
import json
import os
import sqlite3

import pytest

from entities_fixtures import batch, entities, entity


def rows(store, table):
    assert table in ('entity_current', 'entity_versions', 'entity_batches')
    with store.connect(readonly=True) as db:
        return [dict(r) for r in db.execute('SELECT * FROM ' + table)]


def test_all_types_roundtrip_preserves_json_and_independent_ids(store):
    fields = {'raw_name': ' 虚构值\n ', 'nest': {'unknown': [None, True, 17, 1.25]}, 'empty': {}}
    values = [entity(entity_type=kind, fields=fields) for kind in entities.ENTITY_TYPES]
    reply = store.receive(batch(values=values))
    assert reply['persisted'] and reply['entity_count'] == 12 and reply['versions_added'] == 12
    for kind in entities.ENTITY_TYPES:
        saved = store.get(kind, 'fictional-order-1')['entity']
        assert saved['fields'] == fields and saved['entity_type'] == kind
    stats = store.stats()
    assert stats['total_entities'] == stats['total_versions'] == 12
    assert all(row['count'] == row['versions'] == 1 for row in stats['types'])


def test_exact_retry_new_batch_and_metadata_only_do_not_create_versions(store):
    original = store.receive(batch())
    before = {name: rows(store, name) for name in ('entity_current', 'entity_versions', 'entity_batches')}
    assert store.receive(batch()) == dict(original, duplicate=True)
    for name in before:
        assert rows(store, name) == before[name]
    newer = entity(observed_at='2026-11-02T08:00:00Z', source_page='/orders/detail?q=remove#also-remove',
                   collector_version='win-1.0.1', related={'conversation_id': 'tm:fictional'},
                   attachments=[{'event_id': 'icbu:v1:fictional', 'attachment_id': 'fictional-file'}])
    second = store.receive(batch('fictional-2', [newer]))
    assert second['versions_added'] == 0 and second['current_updated'] == 1
    current = store.get('order', 'fictional-order-1')
    assert current['entity']['source_page'] == '/orders/detail'
    assert current['entity']['related'] == newer['related']
    assert current['entity']['attachments'] == newer['attachments']
    assert current['version_id'] == original['results'][0]['version_id']
    assert len(rows(store, 'entity_versions')) == 1


def test_out_of_order_backfill_and_revisiting_content_keep_latest_snapshot(store):
    initial = entity(observed_at='2026-11-02T00:00:00Z', fields={'status': 'new'})
    store.receive(batch(values=[initial]))
    older = entity(observed_at='2024-11-01T00:00:00Z', fields={'status': 'old'})
    reply = store.receive(batch('older', [older]))
    assert reply['versions_added'] == 1 and reply['current_updated'] == 0
    assert store.get('order', initial['entity_id'])['entity']['fields'] == initial['fields']
    # A later real reversion selects the old content version instead of duplicating it.
    reverted = dict(older, observed_at='2026-11-03T00:00:00Z')
    assert store.receive(batch('reversion', [reverted]))['versions_added'] == 0
    assert store.get('order', initial['entity_id'])['entity']['fields'] == older['fields']
    store.receive(batch('repeat-older', [initial]))
    assert len(rows(store, 'entity_versions')) == 2
    summary = store.stats()['types'][0]
    assert summary['earliest_observed_at'] == '2024-11-01T00:00:00.000000+00:00'
    assert summary['latest_observed_at'] == '2026-11-03T00:00:00.000000+00:00'


def test_batch_conflict_and_same_time_conflict_roll_back_every_row(store):
    store.receive(batch())
    for payload, code in [
        (batch(values=[entity(fields={'status': 'changed'})]), 'entity_batch_conflict'),
        (batch('second', [entity(entity_id='new'), entity(fields={'status': 'changed'})]), 'entity_observation_conflict'),
    ]:
        with pytest.raises(entities.EntityRejected) as exc:
            store.receive(payload)
        assert (exc.value.status, exc.value.code) == (409, code)
    assert all(len(rows(store, table)) == 1 for table in ('entity_current', 'entity_versions', 'entity_batches'))


def test_sql_failure_after_first_item_rolls_back_entire_batch(store):
    with store.connect() as db:
        db.execute("CREATE TRIGGER fictional_failure BEFORE INSERT ON entity_versions WHEN NEW.entity_id='fail' BEGIN SELECT RAISE(ABORT,'private test error'); END")
    with pytest.raises(entities.EntityRejected, match='entity_storage_unavailable'):
        store.receive(batch(values=[entity(), entity(entity_id='fail')]))
    assert store.stats()['total_entities'] == store.stats()['total_batches'] == store.stats()['total_versions'] == 0


def test_concurrent_same_batch_and_different_batches_with_same_fields(store):
    with ThreadPoolExecutor(max_workers=8) as pool:
        replies = list(pool.map(lambda _: store.receive(batch()), range(16)))
    assert sum(not r['duplicate'] for r in replies) == 1
    with ThreadPoolExecutor(max_workers=8) as pool:
        replies = list(pool.map(lambda n: store.receive(batch('new-' + str(n))), range(12)))
    assert all(r['versions_added'] == 0 for r in replies)
    assert store.stats()['total_entities'] == store.stats()['total_versions'] == 1
    assert store.stats()['total_batches'] == 13


def test_readback_survives_restart_and_does_not_mutate_tables(store):
    store.receive(batch())
    before = {table: rows(store, table) for table in ('entity_current', 'entity_versions', 'entity_batches')}
    reopened = entities.EntityStore(store.path, initialize=False)
    assert reopened.stats()['total_entities'] == 1
    assert reopened.get('order', 'fictional-order-1')['entity']['fields'] == entity()['fields']
    for table, value in before.items():
        assert rows(store, table) == value
    with store.connect(readonly=True) as db:
        with pytest.raises(sqlite3.OperationalError, match='readonly'):
            db.execute('DELETE FROM entity_current')


def test_json_key_order_is_semantic_but_values_are_preserved(store):
    store.receive(batch(values=[entity(fields={'b': 2, 'a': 1})]))
    assert store.receive(batch(values=[entity(fields={'a': 1, 'b': 2})]))['duplicate']
    reply = store.receive(batch('changed-value', [entity(fields={'b': 2, 'a': 1.0}, observed_at='2026-11-01T08:01:00Z')]))
    assert reply['versions_added'] == 1  # retain JSON numeric representations, no lossy coercion


def test_empty_stats_and_recent_batch_limit(store):
    stats = store.stats()
    assert stats['total_entities'] == 0 and stats['recent_batches'] == []
    assert len(stats['types']) == 12
    assert all(r['earliest_observed_at'] is None for r in stats['types'])
    for n in range(25):
        store.receive(batch('fictional-' + str(n)))
    assert store.stats()['total_batches'] == 25 and len(store.stats()['recent_batches']) == 20


@pytest.mark.parametrize('variant', ['symlink-db', 'symlink-parent', 'symlink-wal', 'hardlink', 'weak-db', 'weak-parent'])
def test_unsafe_paths_rejected_without_modifying_target(tmp_path, variant):
    parent = tmp_path / 'private'
    parent.mkdir(mode=0o700)
    target = tmp_path / 'untouched'
    target.write_bytes(b'fictional untouched data')
    os.chmod(target, 0o600)
    path = parent / 'entities.sqlite3'
    if variant == 'symlink-parent':
        link = tmp_path / 'link'
        link.symlink_to(parent, target_is_directory=True)
        path = link / 'entities.sqlite3'
    elif variant == 'symlink-db':
        path.symlink_to(target)
    elif variant == 'symlink-wal':
        entities.EntityStore(path)
        (parent / 'entities.sqlite3-wal').symlink_to(target)
    elif variant == 'hardlink':
        os.link(target, path)
    elif variant == 'weak-db':
        path.touch(mode=0o644)
        path.chmod(0o644)
    else:
        parent.chmod(0o755)
    with pytest.raises(entities.EntityRejected, match='entity_storage_unsafe'):
        entities.EntityStore(path)
    assert target.read_bytes() == b'fictional untouched data'


def test_replaced_database_rejected(store):
    store.path.rename(store.path.with_suffix('.saved'))
    entities.EntityStore(store.path)
    with pytest.raises(entities.EntityRejected, match='entity_storage_unsafe'):
        store.stats()


@pytest.mark.parametrize('version', [0, 1, 2, 99])
def test_never_initializes_chat_or_unknown_schema(tmp_path, version):
    path = tmp_path / 'not-entities.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE inbound(payload TEXT)')
        db.execute('PRAGMA user_version=' + str(version))
    path.chmod(0o600)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(entities.EntityRejected, match='entity_database_version_invalid'):
        entities.EntityStore(path)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_disk_full_is_not_acknowledged(store, monkeypatch):
    def full(*args, **kwargs):
        raise OSError(errno.ENOSPC, 'fictional private detail')
    monkeypatch.setattr(entities.sqlite3, 'connect', full)
    with pytest.raises(entities.EntityRejected) as exc:
        store.receive(batch())
    assert (exc.value.status, exc.value.code) == (507, 'entity_disk_full')
