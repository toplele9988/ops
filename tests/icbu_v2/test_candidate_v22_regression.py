"""在v2.2候选上直接复用旧断言，覆盖正常存储时的兼容与安全边界。"""
import importlib
import inspect

import pytest

from test_receiver_v22 import attachments, receiver

CASES = {
    'test_receiver': (
        'test_legacy_five_fields_and_current_message_keep_original_fingerprint',
        'test_enrichment_updates_existing_record_without_changing_hash_state_or_job',
        'test_event_only_legacy_message_enrichment_preserves_dedup_fingerprint',
        'test_alias_new_event_links_same_message_and_late_upload',
        'test_enrichment_conflict_rolls_back_and_does_not_replace_payload',
        'test_upload_before_message_conflict_does_not_create_message',
        'test_missing_sha_can_be_filled_but_existing_hash_is_immutable',
        'test_concurrent_enrichment_creates_one_message',
        'test_cumulative_metadata_limits_are_enforced_across_enrichment',
        'test_cumulative_cards_and_serialized_size_reject_without_replacing_saved_data',
    ),
    'test_attachments': (
        'test_message_first_then_upload_and_permissions',
        'test_wrong_hash_and_interrupted_upload_leave_no_binding_and_retry_works',
        'test_capacity_and_duplicate_at_full_capacity',
        'test_disk_full_is_clear_and_own_temporary_file_is_removed',
        'test_symlink_in_root_or_parent_is_rejected_without_touching_target',
        'test_symlink_db_or_blob_and_insecure_root_are_rejected',
        'test_concurrent_duplicate_writes_leave_one_blob_and_one_binding',
        'test_two_store_instances_serialize_on_same_file_lock',
        'test_one_event_cannot_upload_more_than_twenty_distinct_attachments',
        'test_bulk_alias_preflight_detects_conflicts_before_any_alias_is_linked',
    ),
    'test_attachments_v21': (
        'test_registered_then_uploaded_readback_has_one_canonical_binding',
        'test_failed_enrichment_rolls_back_ledger_with_binding',
        'test_nas_missing_mount_during_runtime_returns_503_and_never_recreates_root',
        'test_nas_sentinel_disappears_mid_upload_and_cannot_ack_old_descriptor',
        'test_nas_replaced_root_identity_fails_before_creating_lock',
    ),
}


@pytest.mark.parametrize('module_name,case', [(module, name) for module, names in CASES.items() for name in names])
def test_existing_assertion_on_v22_candidate(module_name, case, tmp_path, monkeypatch):
    module = importlib.import_module(module_name)
    for name, value in {'receiver': receiver, 'module': attachments,
                        'AttachmentStore': attachments.AttachmentStore,
                        'AttachmentRejected': attachments.AttachmentRejected}.items():
        if hasattr(module, name):
            monkeypatch.setattr(module, name, value)
    reg = attachments.AttachmentStore(tmp_path / 'local' / 'attachments.sqlite3', tmp_path / 'blobs')
    store = receiver.Store(tmp_path / 'inbound.sqlite3', attachments=reg) if module_name == 'test_receiver' else reg
    available = dict(store=store, tmp_path=tmp_path, monkeypatch=monkeypatch)
    function = getattr(module, case)
    function(**{name: available[name] for name in inspect.signature(function).parameters})
