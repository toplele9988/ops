"""Read only safe heartbeat metadata, using the observed probe_state schema."""
import json
from pathlib import Path
import re

from console.inbox.store import read_database
from .common import timestamp
from .wechat import schema

PROBE_PATH = Path('/var/lib/imgw/icbu/probe-status.sqlite3')
CHECKLIST = ['Win10 上 Chrome 是否开着并已登录阿里国际站', 'Tampermonkey 里“乐初探针”脚本是否启用',
             '阿里页面是否停在“消息”页', '电脑有没有休眠或断网']


def safe_value(value):
    return value if isinstance(value, str) and re.fullmatch(r'[\w. +:-]{1,80}', value) else None


def read_probe(path=PROBE_PATH):
    path = Path(path)
    if not path.is_file():
        return None
    try:
        with read_database(path) as db:
            tables = schema(db)
            if not {'account', 'snapshot_json', 'received_at'} <= tables.get('probe_state', set()):
                return None
            accounts = []
            for account, raw, received in db.execute('SELECT account,snapshot_json,received_at FROM probe_state ORDER BY received_at DESC'):
                received = timestamp(received)
                if received is None:
                    continue
                try:
                    snapshot = json.loads(raw)
                    version, status = safe_value(snapshot.get('version')), safe_value(snapshot.get('state'))
                except (ValueError, TypeError, AttributeError):
                    version = status = None
                accounts.append(dict(account=account, last_heartbeat_at=received, script_version=version, status=status))
        if not accounts:
            return None
        return dict(last_heartbeat_at=accounts[0]['last_heartbeat_at'], script_version=accounts[0]['script_version'],
                    status=accounts[0]['status'], accounts=accounts)
    except Exception:
        return None


def diagnose(last_message_at, probe, now):
    if (last_message_at is not None and now - last_message_at > 6 * 3600 and probe is not None
            and now - probe['last_heartbeat_at'] > 6 * 3600):
        hours = int((now - last_message_at) / 3600)
        return f'阿里已 {hours} 小时没有收到消息，探针心跳也已中断，请到 Win10 上检查', list(CHECKLIST)
    return None, []
