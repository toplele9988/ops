"""Validated UTC maintenance intervals, capped per UTC calendar day."""
from datetime import datetime, timezone
import json
import os
import stat


def windows(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return []
    with os.fdopen(fd, encoding='utf8') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > 65536:
            raise ValueError('维护窗口文件无效')
        data = json.load(stream)
    if not isinstance(data, dict) or data.get('schema') != 'icbu_maintenance_windows_v1' or not isinstance(data.get('windows'), list) or len(data['windows']) > 1000:
        raise ValueError('维护窗口格式无效')
    intervals = []
    for row in data['windows']:
        if not isinstance(row, dict):
            raise ValueError('维护窗口无效')
        def parse(value):
            if not isinstance(value, str) or not value.endswith('Z'):
                raise ValueError('维护时间须为 UTC')
            return datetime.fromisoformat(value[:-1] + '+00:00').timestamp()
        start, end = parse(row.get('start_utc')), parse(row.get('end_utc'))
        if not 0 < start < end or end - start > 31 * 86400:
            raise ValueError('维护时间范围无效')
        while start < end:
            day_end = (int(start // 86400) + 1) * 86400
            stop = min(end, day_end)
            intervals.append((start, stop))
            start = stop
    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1] and int(start // 86400) == int(merged[-1][0] // 86400):
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    result, used = [], {}
    for start, end in merged:
        day = int(start // 86400)
        allowed = min(end - start, max(0, 1800 - used.get(day, 0)))
        if allowed:
            result.append((start, start + allowed))
            used[day] = used.get(day, 0) + allowed
    return result
