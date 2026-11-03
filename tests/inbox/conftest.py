"""Inject channel composition only: this public snapshot omits console.media.

The real W2 service, SQLite stores, policy wrapper, HTTP routes, draft parser and
outbox are tested. The supplied fake Inbox replaces channel collection; adapter
and attachment transport integration is explicitly outside this isolated suite.
"""
from pathlib import Path
import importlib.util
import sys
import types

if importlib.util.find_spec('console.media') is None:
    package = types.ModuleType('console.channels')
    package.__path__ = [str(Path(__file__).resolve().parents[2] / 'src/console/channels')]
    def unavailable(*args, **kwargs):
        raise AssertionError('Inject a fictional Inbox; real channel composition forbidden')
    package.build_inbox = package.shared_inbox = unavailable
    sys.modules['console.channels'] = package
    whatsapp = types.ModuleType('console.channels.whatsapp')
    whatsapp.CONFIG_PATH = Path('/tmp/inbox-unconfigured-whatsapp.json')
    sys.modules['console.channels.whatsapp'] = whatsapp
