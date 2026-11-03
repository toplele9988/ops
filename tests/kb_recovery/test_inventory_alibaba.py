"""虚构消息验证跨版本覆盖与未知优先，避免整窗重放已完成来源。"""
import importlib.util
from pathlib import Path

import pytest


PATH = Path(__file__).resolve().parents[2] / 'deploy/kb/inventory_alibaba.py'
SPEC = importlib.util.spec_from_file_location('inventory_alibaba_test', PATH)
inventory = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(inventory)


def test_new_window_keeps_old_complete_source_and_never_resends_unknown():
    old = ('虚构旧消息', 'a' * 64)
    new = ('虚构新消息', 'b' * 64)
    held = ('虚构未知消息', 'c' * 64)
    record = {'body': '虚构灯具说明', 'sent_at': '2030-01-01T00:00:00+00:00'}
    end = len(record['body'])
    # An old version completed the same source in two windows. A later version
    # includes it alongside a fresh message and an unknown request's source.
    complete = {old: [(0, 3), (3, end)], held: [(0, end)]}
    responses = {held: [(0, end)]}
    requested = {old: [(0, end)], held: [(0, end)]}
    statuses = [inventory.classify_source(key, record, complete, responses, requested, {held})
                for key in (old, new, held)]
    assert statuses == ['complete', 'never_requested', 'unknown']
    assert [key for key, status in zip((old, new, held), statuses) if status == 'never_requested'] == [new]


def test_partial_old_response_requires_review_and_full_response_only_resumes_local():
    key = ('虚构消息', 'a' * 64)
    record = {'body': '虚构参数说明', 'sent_at': '2030-01-01T00:00:00+00:00'}
    end = len(record['body'])
    assert inventory.classify_source(key, record, {}, {key: [(0, end)]}, {key: [(0, end)]}, set()) == 'local_resume'
    assert inventory.classify_source(key, record, {key: [(0, 2), (3, end)]}, {}, {key: [(0, end)]}, set()) == 'requested_incomplete'
    record['sent_at'] = None
    assert inventory.classify_source(key, record, {}, {}, {}, set()) == 'time_unconfirmed'


def snapshot(tmp_path):
    root = tmp_path / 'private'
    root.mkdir(mode=0o700)
    for name in ('analysis.sqlite3', 'inbound.sqlite3', 'ledger.sqlite3', 'catalog.sqlite3',
                 'source-freeze.json', 'usage.json'):
        (root / name).touch(mode=0o600)
    return root


@pytest.mark.parametrize('path', ['/var/lib/imgw/icbu', '/tmp/../var/lib/imgw/icbu'])
def test_inventory_refuses_production_path(path):
    with pytest.raises(ValueError):
        inventory.private_snapshot(path)


@pytest.mark.parametrize('link', ['soft', 'hard'])
def test_inventory_refuses_linked_backup(tmp_path, link):
    root = snapshot(tmp_path)
    target = root / 'analysis.sqlite3'
    other = tmp_path / 'outside.sqlite3'
    target.rename(other)
    if link == 'soft':
        target.symlink_to(other)
    else:
        target.hardlink_to(other)
    with pytest.raises(ValueError):
        inventory.private_snapshot(root)


def test_inventory_only_accepts_private_unlinked_backup(tmp_path):
    root = snapshot(tmp_path)
    assert inventory.private_snapshot(root) == root
    (root / 'usage.json').chmod(0o644)
    with pytest.raises(ValueError):
        inventory.private_snapshot(root)
