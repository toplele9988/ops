from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from email.message import Message
import importlib
import inspect
import io
import socket
import threading
import time
from types import SimpleNamespace

import pytest

from conftest import ROOT, receiver


@contextmanager
def running(module, attachments, tmp_path):
    store = module.Store(tmp_path / 'inbound.sqlite3', attachments=attachments.AttachmentStore(
        tmp_path / 'local' / 'attachments.sqlite3', tmp_path / 'blobs'))
    server = module.Server(('127.0.0.1', 0), store, 'fictional-instance-only-' + 'x' * 32,
                           import_archive=object(), probe_heartbeats=object())
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


@pytest.mark.parametrize('phase', ['headers', 'body'])
def test_continuous_trickle_has_absolute_deadline(candidate, monkeypatch, tmp_path, phase):
    def attempt(root, suffix):
        module, attachments, helper = receiver(root, monkeypatch, suffix)
        monkeypatch.setattr(module.Handler, 'header_seconds', .18, raising=False)
        monkeypatch.setattr(module.Handler, 'request_seconds', .18, raising=False)
        original_setup = module.Handler.setup
        def setup(self):
            original_setup(self)
            self.connection.settimeout(.08)
        monkeypatch.setattr(module.Handler, 'setup', setup)
        with running(module, attachments, tmp_path / suffix) as server:
            with socket.create_connection(server.server_address, timeout=2) as client:
                if phase == 'body':
                    head = ('POST /api/inbound/alibaba HTTP/1.0\r\nAuthorization: Bearer ' + server.token +
                            '\r\nContent-Type: application/json\r\nContent-Length: 500\r\n\r\n')
                    client.sendall(head.encode())
                else:
                    client.sendall(b'GET /healthz HTTP/1.0\r\nX-Fictional: ')
                closed = False
                for _ in range(20):
                    try:
                        client.sendall(b'x')
                    except OSError:
                        closed = True
                        break
                    time.sleep(.02)
                # Inspect while the idle timeout has not elapsed after the final byte.
                client.settimeout(.005)
                try:
                    closed = closed or client.recv(4096) == b''
                except socket.timeout:
                    pass
                return closed
    assert not attempt(ROOT, 'before_' + phase)
    assert attempt(candidate, 'after_' + phase)


def test_unmounted_directory_with_sentinel_never_counts_as_nas(candidate, monkeypatch, tmp_path):
    for root, suffix, expected in ((ROOT, 'before', 'ok'), (candidate, 'after', 'unavailable')):
        _, attachments, _ = receiver(root, monkeypatch, suffix)
        archive = tmp_path / suffix / 'nas'
        blobs = archive / 'icbu' / 'attachments'
        blobs.mkdir(parents=True, mode=0o700)
        (archive / '.imgw-archive-id').touch(mode=0o600)
        monkeypatch.setattr(attachments.os.path, 'ismount', lambda _: False)
        store = attachments.AttachmentStore(tmp_path / suffix / 'local' / 'attachments.sqlite3',
                                            blobs, archive_root=archive)
        assert store.storage_status() == expected
        if suffix == 'after':
            assert not store.path.exists() and not (blobs / '.attachments.lock').exists()


def test_mount_loss_preserves_text_partial_ack_and_recovery(candidate, monkeypatch, tmp_path):
    module, attachments, helper = receiver(candidate, monkeypatch, 'recovery')
    archive = tmp_path / 'nas'
    blobs = archive / 'icbu' / 'attachments'
    blobs.mkdir(parents=True, mode=0o700)
    (archive / '.imgw-archive-id').touch(mode=0o600)
    online = [True]
    monkeypatch.setattr(attachments.os.path, 'ismount', lambda _: online[0])
    registry = attachments.AttachmentStore(tmp_path / 'local' / 'attachments.sqlite3', blobs, archive_root=archive)
    store = module.Store(tmp_path / 'inbound.sqlite3', attachments=registry)
    payload = module.validate(helper.message(attachments=[helper.attachment()]))
    online[0] = False
    first = store.receive(payload)
    assert first['persisted'] and first['attachments_pending']
    with pytest.raises(attachments.AttachmentRejected) as caught:
        registry.readback(payload['event_id'], 'fictional-file')
    assert caught.value.status == 503
    online[0] = True
    second = store.receive(payload)
    assert second['inbound_id'] == first['inbound_id'] and second['duplicate']
    assert 'attachments_pending' not in second


def test_alias_input_authentication_and_frozen_classification(candidate, monkeypatch, tmp_path):
    module, attachments, helper = receiver(candidate, monkeypatch, 'freeze')
    for alias in ('e1_A', 'e1_////', '../fictional'):
        with pytest.raises(attachments.AttachmentRejected):
            attachments.canonical_event_id(alias, transport=True)
    store = module.Store(tmp_path / 'inbound.sqlite3')
    first = store.receive(module.validate(helper.message()))
    replay = module.validate(helper.message(event_id='fictional-new-alias', history=False))
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda _: store.receive(replay), range(8)))
    assert all(r['inbound_id'] == first['inbound_id'] for r in responses)
    assert store.status(first['inbound_id'])['archive_reason'] == 'history_import'
    handler = object.__new__(module.Handler)
    handler.server = SimpleNamespace(token='fictional-token')
    handler.headers = Message()
    handler.headers['Authorization'] = 'Bearer fictional-token'
    handler.headers['Authorization'] = 'Bearer fictional-token'
    with pytest.raises(module.Rejected) as caught:
        handler.authenticate()
    assert caught.value.status == 401


@pytest.mark.parametrize('module_name,case', [
    ('test_attachments', 'test_message_first_then_upload_and_permissions'),
    ('test_attachments', 'test_wrong_hash_and_interrupted_upload_leave_no_binding_and_retry_works'),
    ('test_attachments', 'test_symlink_in_root_or_parent_is_rejected_without_touching_target'),
    ('test_attachments', 'test_symlink_db_or_blob_and_insecure_root_are_rejected'),
    ('test_attachments', 'test_two_store_instances_serialize_on_same_file_lock'),
    ('test_attachments_v21', 'test_registered_then_uploaded_readback_has_one_canonical_binding'),
    ('test_attachments_v21', 'test_nas_sentinel_disappears_mid_upload_and_cannot_ack_old_descriptor'),
    ('test_receiver', 'test_enrichment_conflict_rolls_back_and_does_not_replace_payload'),
    ('test_receiver', 'test_alias_new_event_links_same_message_and_late_upload'),
    ('test_receiver', 'test_legacy_five_fields_and_current_message_keep_original_fingerprint'),
    ('test_receiver', 'test_enrichment_updates_existing_record_without_changing_hash_state_or_job'),
    ('test_receiver', 'test_event_only_legacy_message_enrichment_preserves_dedup_fingerprint'),
    ('test_receiver', 'test_upload_before_message_conflict_does_not_create_message'),
    ('test_receiver', 'test_missing_sha_can_be_filled_but_existing_hash_is_immutable'),
    ('test_receiver', 'test_concurrent_enrichment_creates_one_message'),
    ('test_receiver', 'test_cumulative_metadata_limits_are_enforced_across_enrichment'),
    ('test_receiver', 'test_cumulative_cards_and_serialized_size_reject_without_replacing_saved_data'),
    ('test_attachments', 'test_capacity_and_duplicate_at_full_capacity'),
    ('test_attachments', 'test_disk_full_is_clear_and_own_temporary_file_is_removed'),
    ('test_attachments', 'test_concurrent_duplicate_writes_leave_one_blob_and_one_binding'),
    ('test_attachments', 'test_one_event_cannot_upload_more_than_twenty_distinct_attachments'),
    ('test_attachments', 'test_bulk_alias_preflight_detects_conflicts_before_any_alias_is_linked'),
    ('test_attachments_v21', 'test_failed_enrichment_rolls_back_ledger_with_binding'),
    ('test_attachments_v21', 'test_nas_missing_mount_during_runtime_returns_503_and_never_recreates_root'),
    ('test_attachments_v21', 'test_nas_replaced_root_identity_fails_before_creating_lock'),
])
def test_existing_assertion_on_review_candidate(candidate, module_name, case, tmp_path, monkeypatch):
    module, attachments, _ = receiver(candidate, monkeypatch, 'existing')
    monkeypatch.syspath_prepend(str(ROOT / 'tests/icbu_v2'))
    old = importlib.import_module(module_name)
    for name, value in {'receiver': module, 'module': attachments, 'AttachmentStore': attachments.AttachmentStore,
                        'AttachmentRejected': attachments.AttachmentRejected}.items():
        if hasattr(old, name):
            monkeypatch.setattr(old, name, value)
    # Old NAS tests simulate a mount with a temporary directory; supply that topology explicitly.
    monkeypatch.setattr(attachments.os.path, 'ismount', lambda p: str(p).startswith(str(tmp_path)))
    registry = attachments.AttachmentStore(tmp_path / 'local' / 'attachments.sqlite3', tmp_path / 'blobs')
    store = module.Store(tmp_path / 'inbound.sqlite3', attachments=registry) if module_name == 'test_receiver' else registry
    function = getattr(old, case)
    arguments = dict(store=store, tmp_path=tmp_path, monkeypatch=monkeypatch)
    function(**{name: arguments[name] for name in inspect.signature(function).parameters})


@pytest.mark.parametrize('case,extra', [
    *[('test_startup_offline_preserves_text_and_recovers_without_restart', {'state': state})
      for state in ('absent', 'missing-sentinel', 'missing-root')],
    ('test_runtime_detach_and_restore_same_mount_never_falls_back', {}),
    ('test_mid_commit_loss_returns_partial_and_replay_repairs_aliases', {}),
    *[('test_offline_rich_variants_and_conflicts_are_durable_and_idempotent', {'variant': variant})
      for variant in ('cards', 'empty-content', 'event-only')],
    ('test_offline_auth_origin_validation_and_non_attachment_delegation', {}),
    *[('test_replaced_mount_or_root_stays_unavailable_until_original_returns', {'replacement': replacement})
      for replacement in ('symlink', 'directory')],
    ('test_main_starts_when_storage_is_offline', {}),
    ('test_v22_cli_on_temporary_loopback_instance', {}),
    ('test_environment_fault_at_startup_and_recovery', {}),
    ('test_partial_success_requires_main_database_commit', {}),
])
def test_existing_v22_scenario_on_review_candidate(candidate, case, extra, tmp_path, monkeypatch, capsys):
    module, attachments, _ = receiver(candidate, monkeypatch, 'v22_existing')
    monkeypatch.syspath_prepend(str(ROOT / 'tests/icbu_v2'))
    old = importlib.import_module('test_receiver_v22')
    monkeypatch.setattr(old, 'receiver', module)
    monkeypatch.setattr(old, 'attachments', attachments)
    monkeypatch.setattr(attachments.os.path, 'ismount', lambda p: str(p).startswith(str(tmp_path)))
    function = getattr(old, case)
    arguments = dict(tmp_path=tmp_path, monkeypatch=monkeypatch, capsys=capsys, **extra)
    function(**{name: arguments[name] for name in inspect.signature(function).parameters})
