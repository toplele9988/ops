"""Read-only, role-based operating plan for channel coverage and diagnostics."""
from copy import deepcopy
import json
import os
from pathlib import Path
import stat


CHANNEL_PLAN_PATH = Path('/etc/imgw/channel-operation-plan.json')
ALIBABA_EXPECTED_ACCOUNTS = 6
ALIBABA_MAIN_COUNT = 1
ALIBABA_OTHERS_COUNT = ALIBABA_EXPECTED_ACCOUNTS - ALIBABA_MAIN_COUNT
PLAN_STATES = frozenset(('operating', 'planned', 'not_started'))

DEFAULT_PLAN = {
    'whatsapp': 'not_started',
    'alibaba_accounts': {'main': 'operating', 'others': 'planned'},
    'wechat': 'operating',
}


def default_plan():
    return deepcopy(DEFAULT_PLAN)


def _validate(value):
    if not isinstance(value, dict) or set(value) != {'whatsapp', 'alibaba_accounts', 'wechat'}:
        raise ValueError('invalid channel plan')
    accounts = value['alibaba_accounts']
    if not isinstance(accounts, dict) or set(accounts) != {'main', 'others'}:
        raise ValueError('invalid account plan')
    if (value['whatsapp'] not in PLAN_STATES or value['wechat'] not in PLAN_STATES or
            accounts['main'] not in PLAN_STATES or accounts['others'] not in PLAN_STATES):
        raise ValueError('invalid plan state')
    return {
        'whatsapp': value['whatsapp'],
        'alibaba_accounts': {'main': accounts['main'], 'others': accounts['others']},
        'wechat': value['wechat'],
    }


def resolve_plan(plan=None, path=None):
    """Return validated role statuses; any unavailable file falls back safely."""
    if plan is not None:
        try:
            return _validate(plan)
        except (TypeError, ValueError):
            return default_plan()
    source = CHANNEL_PLAN_PATH if path is None else Path(path)
    try:
        fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, encoding='utf8') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 16384:
                raise ValueError('invalid plan file')
            raw = stream.read(16385)
            if len(raw) > 16384:
                raise ValueError('invalid plan file')
        return _validate(json.loads(raw))
    except Exception:
        return default_plan()


def public_plan(plan):
    """Expose only statuses and fixed role counts, never source identifiers."""
    statuses = _validate(plan)
    return {
        'whatsapp': statuses['whatsapp'],
        'alibaba_accounts': {
            **statuses['alibaba_accounts'],
            'expected': ALIBABA_EXPECTED_ACCOUNTS,
            'main_count': ALIBABA_MAIN_COUNT,
            'others_count': ALIBABA_OTHERS_COUNT,
        },
        'wechat': statuses['wechat'],
    }
