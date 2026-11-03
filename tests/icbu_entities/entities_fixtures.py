"""Only synthetic fixtures, temporary stores and dynamic loopback ports."""
from contextlib import contextmanager
import importlib.util
from pathlib import Path
import sys
import threading
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
CANDIDATE = ROOT / 'deploy' / 'icbu' / 'entities'
sys.path.insert(0, str(ROOT / 'tests' / 'icbu_v2'))
from receiver_fixtures import load_receiver, message


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


entities = load('entities_test_module', CANDIDATE / 'icbu_entities.py')
attachments = load('entities_test_attachments', ROOT / 'deploy' / 'icbu' / 'v2.2' / 'icbu_attachments.py')
with patch.dict(sys.modules, {'icbu_entities': entities, 'icbu_attachments': attachments}):
    receiver = load_receiver(CANDIDATE / 'icbu_inbound_receiver.py', name='entities_test_receiver')


def entity(**changes):
    return dict(dict(entity_type='order', entity_id='fictional-order-1',
                     observed_at='2026-11-01T08:00:00Z', source_page='/orders/detail',
                     collector_version='win-1.0.0', fields={'status': 'fictional_pending', 'items': []}), **changes)


def batch(batch_id='fictional-batch-1', values=None):
    return dict(batch_id=batch_id, entities=[entity()] if values is None else values)


@pytest.fixture
def store(tmp_path):
    return entities.EntityStore(tmp_path / 'entities.sqlite3')


@contextmanager
def running(tmp_path):
    reg = attachments.AttachmentStore(tmp_path / 'attachments.sqlite3', tmp_path / 'files')
    chat = receiver.Store(tmp_path / 'inbound.sqlite3', attachments=reg)
    server = receiver.Server(('127.0.0.1', 0), chat, 'fictional-entity-test-token-' + 'x' * 32,
                             import_archive=object(), probe_heartbeats=object(),
                             entities=entities.EntityStore(tmp_path / 'entities.sqlite3'))
    assert server.server_address[1] != 8791
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        if server._maintenance:
            server._maintenance.join(timeout=5)


@pytest.fixture
def server(tmp_path):
    with running(tmp_path) as active:
        yield active
