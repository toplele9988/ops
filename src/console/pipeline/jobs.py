"""Read bounded, credential-free failed-unit snapshots; never invoke systemd here."""
import json
import math
import os
import re
import stat


def read_failed_file(path, now):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, encoding='utf8') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > 65536:
            raise ValueError('状态文件无效')
        data = json.load(stream)
    if not isinstance(data, dict) or set(data) != {'schema', 'generated_at', 'items'} or data['schema'] != 'pipeline_failed_units_v1':
        raise ValueError('状态文件格式无效')
    at = data['generated_at']
    if type(at) not in (int, float) or not math.isfinite(at) or not 0 < at <= now or now - at > 900:
        raise ValueError('状态文件时间无效')
    return data['items']


def failed_jobs(reader, now):
    result = dict(status='not_connected', items=[], count=None,
                  reason='后台作业失败清单未接入，请管理员接入只读的服务状态读取。')
    if reader is None:
        return result
    try:
        rows = reader(now)
        if rows is None:
            return result
        if not isinstance(rows, (tuple, list)) or len(rows) > 100:
            raise ValueError('失败单元清单无效')
        items = []
        for row in rows:
            unit, at = row['unit'], row.get('failed_at')
            if not isinstance(unit, str) or not re.fullmatch(r'[A-Za-z0-9_.@:-]{1,160}\.(?:service|timer)', unit):
                raise ValueError('失败单元名称无效')
            if at is not None and (type(at) not in (int, float) or not math.isfinite(at) or not 0 < at <= now):
                raise ValueError('失败时间无效')
            channel, outcome, code = row.get('channel'), row.get('result'), row.get('exit_code')
            if channel is not None and channel not in ('alibaba', 'wechat', 'whatsapp'):
                raise ValueError('渠道无效')
            if outcome is not None and (not isinstance(outcome, str) or not re.fullmatch(r'[a-z0-9_-]{1,40}', outcome)):
                raise ValueError('结果无效')
            if code is not None and (type(code) is not int or not 0 <= code <= 255):
                raise ValueError('退出码无效')
            item = dict(unit=unit, failed_at=at)
            for key, value in (('channel', channel), ('result', outcome), ('exit_code', code)):
                if key in row:
                    item[key] = value
            items.append(item)
        result.update(status='ready', items=items, count=len(items),
                      reason='这是后台作业状态，不等于数据丢失。请管理员核对服务状态。')
    except Exception:
        result.update(status='unavailable', reason='后台作业失败清单暂时读不到或已过期，请管理员核对状态文件后刷新。')
    return result
