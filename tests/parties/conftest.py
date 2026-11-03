"""The public snapshot omits console.media required by live channel adapters.

Keep that optional channel boundary offline; tests exercise actual archive, Deals,
Parties and HTTP code, supplying Inbox explicitly. Never import a live fallback.
"""
import sys
from types import ModuleType


def unavailable(*args, **kwargs):
    raise AssertionError('tests must inject the fictional Inbox')


channel_boundary = ModuleType('console.channels')
channel_boundary.build_inbox = unavailable
channel_boundary.shared_inbox = unavailable
sys.modules['console.channels'] = channel_boundary
