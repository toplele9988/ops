"""Offline WhatsApp bridge checks; no WAHA transport or send operations."""
import json
import os
import stat

from console.channels.whatsapp import SESSION_STATES
from console.channels.common import timestamp
from console.channel_plan import DEFAULT_PLAN
from .common import finding, read_db, sample_id

LOGIN = '系统后台 → 渠道接入 → WhatsApp 扫码登录'


def check_session(ctx):
    plan = getattr(ctx, 'plan', DEFAULT_PLAN)['whatsapp']
    provider = getattr(ctx, 'session_status', None)
    if provider is not None:
        observed = provider()
        if isinstance(observed, str):
            status, at = observed, ctx.now
        elif isinstance(observed, dict):
            status, at = observed.get('status'), observed.get('received_at')
        else:
            status, at = None, None
        source = 'injected_status'
    else:
        with read_db(ctx.whatsapp_path) as db:
            row = db.execute("SELECT CASE WHEN json_valid(raw_json) THEN json_extract(raw_json,'$.payload.status') END,"
                "received FROM events WHERE event_type='session.status' ORDER BY received DESC,event_key DESC LIMIT 1").fetchone()
        status, at = (row[0], row[1]) if row else (None, None)
        source = 'archived_session_event'
    status = status if isinstance(status, str) and status in SESSION_STATES else 'UNKNOWN'
    at = at if type(at) in (int, float) and 0 < at <= ctx.now else None
    stale = at is None or ctx.now - at > 6 * 3600
    bad = status != 'WORKING' or stale
    level = 'medium' if bad and plan in ('not_started', 'planned') else 'high' if bad else 'ok'
    if bad and plan in ('not_started', 'planned'):
        meaning = 'WhatsApp 还没登录，尚未开始运营；准备开始运营时到 系统后台 → 渠道接入 扫码登录。'
        remedy = '还没登录；准备开始运营时到 ' + LOGIN + '。'
    else:
        meaning = ('最近记录的会话状态不是 WORKING，或状态证据已超过 6 小时，收件可能中断；归档事件不是实时在线探测。'
                   if bad else
                   '最近 6 小时归档的状态为 WORKING；这里只确认本地状态证据，不保证此刻在线或收件完整。')
        remedy = LOGIN + '，核对状态并按页面提示重新连接。'
    return finding('whatsapp.session', 'WhatsApp 会话状态', level, int(bad),
        meaning, remedy, [], session_status=status, observed_at=at,
        age_seconds=ctx.now - at if at is not None else None, source=source, plan=plan)


def check_latest_message(ctx):
    plan = getattr(ctx, 'plan', DEFAULT_PLAN)['whatsapp']
    with read_db(ctx.whatsapp_path) as db:
        at, ever = db.execute("SELECT MAX(CASE WHEN typeof(received) IN ('integer','real') AND received<=? "
                              "THEN received END),COUNT(*) FROM messages WHERE direction='inbound'",
                              (ctx.now,)).fetchone()
    never_started = plan in ('not_started', 'planned') and ever == 0
    stale = at is None or ctx.now - at > 7 * 86400
    if never_started:
        level, count = 'ok', 0
        meaning = 'WhatsApp 尚未开始运营，暂无数据。'
        remedy = '准备开始运营时再到 系统后台 → 渠道接入 扫码登录。'
    else:
        level, count = ('medium' if stale else 'ok'), int(stale)
        meaning = ('超过 7 天未归档收到的消息，或还没有收件记录；可能没有新业务，也可能桥接收件中断，需与原渠道核对。'
                   if stale else
                   '最近 7 天有消息进入桥接库；消息到达记录不能证明全部历史已经覆盖。')
        remedy = '/inbox → WhatsApp 对照原渠道最近消息；' + LOGIN + '检查连接，后台检查桥接接收服务。'
    return finding('whatsapp.latest_message', 'WhatsApp 最近收到消息', level, count,
        meaning, remedy, [], last_received_at=at, age_seconds=ctx.now - at if at is not None else None,
        has_ever_received=ever > 0, plan=plan)


def check_media(ctx):
    # knowledge.py writes archived only after bytes are downloaded and hashed.
    # `none` means has_media=false; pending_media_url/deferred are not archives.
    with read_db(ctx.whatsapp_path) as db:
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='media_jobs'").fetchone()
        join = ' LEFT JOIN media_jobs j ON j.message_key=m.message_key ' if exists else ' '
        condition = "m.has_media=1 AND (j.message_key IS NULL OR j.status!='archived')" if exists else 'm.has_media=1'
        query = ' FROM messages m' + join + 'WHERE ' + condition
        count = db.execute('SELECT COUNT(*)' + query).fetchone()[0]
        rows = db.execute('SELECT m.message_key,m.received' + query + ' ORDER BY m.received DESC,m.message_key LIMIT 5')
        samples = [dict(id=sample_id(row[0]), received_at=timestamp(row[1])) for row in rows]
    return finding('whatsapp.media', 'WhatsApp 媒体尚未下载归档', 'medium' if count else 'ok', count,
        '带媒体的消息尚无 archived 下载记录；等待媒体地址、下载失败或还未处理都计入，已下载但待解析的不计入。',
        '/inbox → WhatsApp 核对附件；系统后台检查 WhatsApp 桥接媒体任务与下载错误，再补齐原文件。', samples)


def _auto_send(path):
    # Keep the original object inside this allowlisted reader. No credentials,
    # paths, exceptions or unrelated config values leave the reader.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, encoding='utf8') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > 65536:
            raise ValueError('自动发送配置结构不可用')
        raw = stream.read(65537)
        if len(raw) > 65536:
            raise ValueError('自动发送配置结构不可用')
        data = json.loads(raw)
    value = data.get('auto_send') if isinstance(data, dict) else None
    if type(value) is not bool:
        raise ValueError('自动发送开关必须为布尔值')
    return value


def check_auto_send(ctx):
    enabled = _auto_send(ctx.whatsapp_config_path)
    return finding('whatsapp.auto_send', 'WhatsApp 自动发送开关', 'high' if enabled else 'ok', int(enabled),
        '自动发送已开启，配置不符合所有对外发送由人确认的约定，需要立即核对并关闭。' if enabled else
        '自动发送已关闭，符合对外发送由人确认的约定；此开关不代表发送链路已经验收。',
        '系统后台 → 渠道接入 → WhatsApp；由管理员将桥接配置 auto_send 设为 false，确认后按后台流程重载。',
        [], auto_send=enabled)


CHECKS = [
    ('whatsapp.session', 'WhatsApp 会话状态', check_session),
    ('whatsapp.latest_message', 'WhatsApp 最近收到消息', check_latest_message),
    ('whatsapp.media', 'WhatsApp 媒体尚未下载归档', check_media),
    ('whatsapp.auto_send', 'WhatsApp 自动发送开关', check_auto_send),
]
