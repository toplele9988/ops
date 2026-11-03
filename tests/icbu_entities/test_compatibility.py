"""Exercise existing assertions directly against the entity-enabled receiver."""
import importlib
import inspect
from pathlib import Path

import pytest

from entities_fixtures import ROOT, attachments, receiver

CASES = [
    ('test_receiver', 'test_legacy_five_fields_and_current_message_keep_original_fingerprint'),
    ('test_receiver', 'test_enrichment_updates_existing_record_without_changing_hash_state_or_job'),
    ('test_receiver_v21', 'test_http_roundtrip_and_authenticated_metadata_only_readback'),
    ('test_receiver_v21', 'test_legacy_colon_enrichment_preserves_receipt_fingerprint_and_state'),
    ('test_receiver_v21', 'test_heartbeat_paths_still_delegate_without_extending_payload'),
    ('test_receiver_v22', 'test_runtime_detach_and_restore_same_mount_never_falls_back'),
    ('test_receiver_v22', 'test_mid_commit_loss_returns_partial_and_replay_repairs_aliases'),
    ('test_receiver_v22', 'test_offline_auth_origin_validation_and_non_attachment_delegation'),
    ('test_receiver_v22', 'test_partial_success_requires_main_database_commit'),
    ('test_receiver_v22', 'test_v22_cli_on_temporary_loopback_instance'),
]


@pytest.mark.parametrize('module_name,case', CASES)
def test_existing_assertion_against_entity_receiver(module_name, case, tmp_path, monkeypatch):
    module = importlib.import_module(module_name)
    monkeypatch.setattr(module, 'receiver', receiver)
    if hasattr(module, 'attachments'):
        monkeypatch.setattr(module, 'attachments', attachments)
    if hasattr(module, 'AttachmentStore'):
        monkeypatch.setattr(module, 'AttachmentStore', attachments.AttachmentStore)
    if hasattr(module, 'AttachmentRejected'):
        monkeypatch.setattr(module, 'AttachmentRejected', attachments.AttachmentRejected)
    # Fictional NAS only. Never change real mounts or the global network configuration.
    real_mount = attachments.os.path.ismount
    monkeypatch.setattr(attachments.os.path, 'ismount',
                        lambda path: str(path).startswith(str(tmp_path)) and Path(path).is_dir() or real_mount(path))
    reg = attachments.AttachmentStore(tmp_path / 'attachments.sqlite3', tmp_path / 'files')
    store = receiver.Store(tmp_path / 'inbound.sqlite3', attachments=reg)
    available = dict(store=store, tmp_path=tmp_path, monkeypatch=monkeypatch)
    function = getattr(module, case)
    function(**{name: available[name] for name in inspect.signature(function).parameters})
