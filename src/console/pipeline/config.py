"""Validated, credential-free configuration for read-only pipeline health."""
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import stat

CONFIG_PATH = Path('/var/lib/imgw/ops-app/pipeline-health.json')
DEFAULTS = {
    'paths': {
        'inbound': '/var/lib/imgw/icbu/inbound.sqlite3',
        'probe': '/var/lib/imgw/icbu/probe-status.sqlite3',
        'wechat': '/var/lib/imgw/wechat/api_state.sqlite3',
        'whatsapp': '/var/lib/imgw/whatsapp/messages.sqlite3',
        'maintenance': '/var/lib/imgw/icbu/maintenance-windows.json',
        'failed_units': '/var/lib/imgw/ops-app/systemd-failed.json',
    },
    'alibaba_day': {'start': '08:00', 'end': '24:00', 'night_multiplier': 4},
    'thresholds': {
        'alibaba_inbound': {'slow_seconds': 7200, 'interrupted_seconds': 21600},
        'alibaba_probe': {'slow_seconds': 300, 'interrupted_seconds': 1200},
        'wechat_messages': {'slow_seconds': 7200, 'interrupted_seconds': 21600},
        'wechat_callbacks': {'slow_seconds': 3600, 'interrupted_seconds': 10800},
        'whatsapp_events': {'slow_seconds': 86400, 'interrupted_seconds': 259200},
        'whatsapp_messages': {'slow_seconds': 86400, 'interrupted_seconds': 259200},
        'whatsapp_session': {'slow_seconds': 60, 'interrupted_seconds': 300},
    },
    'whatsapp_session_event_limit': 50000,
    'whatsapp_session_max_age_seconds': 21600,
    'wechat_pending_stuck_seconds': 3600,
    'query_timeout_seconds': 1,
}


def minute(value):
    if not isinstance(value, str) or len(value) != 5 or value[2] != ':':
        raise ValueError('作息时间格式无效')
    hour, part = value.split(':')
    if not hour.isascii() or not part.isascii() or not hour.isdigit() or not part.isdigit():
        raise ValueError('作息时间格式无效')
    hour, part = int(hour), int(part)
    if not 0 <= hour <= 24 or not 0 <= part <= 59 or (hour == 24 and part):
        raise ValueError('作息时间范围无效')
    return hour * 60 + part


def _merge(base, override):
    if not isinstance(override, dict) or set(override) - set(base):
        raise ValueError('管道健康配置字段无效')
    for key, value in override.items():
        if isinstance(base[key], dict):
            _merge(base[key], value)
        else:
            base[key] = value


def _number(value, low, high):
    return type(value) in (int, float) and math.isfinite(value) and low <= value <= high


def validate(value):
    result = deepcopy(DEFAULTS)
    _merge(result, value)
    for path in result['paths'].values():
        if not isinstance(path, str) or not path or '\x00' in path or not Path(path).is_absolute():
            raise ValueError('管道数据路径须为绝对路径')
    day = result['alibaba_day']
    if minute(day['start']) >= minute(day['end']) or not _number(day['night_multiplier'], 1, 100):
        raise ValueError('管道作息配置无效')
    for threshold in result['thresholds'].values():
        slow, stopped = threshold['slow_seconds'], threshold['interrupted_seconds']
        if not _number(slow, 1, 90 * 86400) or not _number(stopped, 1, 90 * 86400) or slow >= stopped:
            raise ValueError('断流阈值须大于变慢阈值')
    if not _number(result['query_timeout_seconds'], .01, 3):
        raise ValueError('查询时间预算无效')
    limit = result['whatsapp_session_event_limit']
    if type(limit) is not int or not 20 <= limit <= 200000:
        raise ValueError('会话状态采样条数无效')
    for key in ('wechat_pending_stuck_seconds', 'whatsapp_session_max_age_seconds'):
        if not _number(result[key], 1, 90 * 86400):
            raise ValueError('状态时限配置无效')
    return result


def resolve_config(value=None, path=None):
    try:
        if value is None:
            source = Path(path if path is not None else os.environ.get('IMGW_PIPELINE_CONFIG', CONFIG_PATH))
            try:
                fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            except FileNotFoundError:
                return deepcopy(DEFAULTS), []
            with os.fdopen(fd, encoding='utf8') as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > 32768:
                    raise ValueError('管道健康配置文件无效')
                value = json.loads(stream.read(32769))
        return validate(value), []
    except (OSError, ValueError, TypeError, RecursionError):
        return deepcopy(DEFAULTS), ['管道健康配置无法使用，当前按默认阈值判断；请管理员核对配置后刷新。']
