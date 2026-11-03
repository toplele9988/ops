"""Independent source snapshots and business-hour-aware health decisions."""
from collections import Counter
from pathlib import Path
import sqlite3
import time

from console.channel_plan import resolve_plan
from .config import DEFAULTS, minute, resolve_config
from .coverage import coverage, wechat_attachments
from .jobs import failed_jobs, read_failed_file
from .maintenance import windows
from .readers import heartbeat_continuity
from .readers import MissingSchema, READERS, read_db
from .scorecard import SESSION_LABELS, scorecard

STATUS = {'ok': '正常', 'slow': '变慢', 'interrupted': '断流', 'never': '从未有',
          'not_connected': '未接入', 'unavailable': '暂时读不到'}
SEVERITY = {'ok': 0, 'not_connected': 1, 'never': 2, 'slow': 3, 'unavailable': 4, 'interrupted': 5}
CHANNELS = {'alibaba': '阿里国际站', 'wechat': '微信', 'whatsapp': 'WhatsApp'}
LABELS = dict(alibaba_inbound='阿里入站', alibaba_probe='阿里心跳', wechat_messages='微信消息',
              wechat_callbacks='微信回调', whatsapp_events='WhatsApp 桥接服务（辅助）',
              whatsapp_session='WhatsApp 会话状态', whatsapp_messages='WhatsApp 消息')
# Reason codes the Win10 collector may report in its strict v1 heartbeat (receiver contract: lowercase snake_case).
PROBE_ERRORS = {
    'pairing_reconnect': ('Win10 的采集网关在线，但浏览器扩展还没有与它重新配对。', '到 Win10：确认 Chrome 里“阿里聊天采集助手”扩展已重新加载并已连接；重新加载只需在扩展程序页点一次，不要卸载。'),
    'browser_disconnected': ('Win10 的浏览器扩展与采集网关断开了。', '到 Win10：打开 Chrome，确认聊天页（messageManager）、身份页、工作台三个页面都开着，扩展已连接。'),
    'login_required': ('阿里账号需要重新登录。', '到 Win10 的 Chrome 里重新登录阿里国际站；不要导出登录信息。'),
    'identity_mismatch': ('页面里的账号或店铺与绑定的不一致。', '到 Win10 核对当前登录的是哪个账号，必要时重新核验绑定。'),
    'date_window_expired': ('采集的日期范围已到期。', '在 221 确认并开启日期自然日滚动后再恢复，不要手工改范围绕过。'),
    'clock_skew': ('Win10 时钟与标准时间偏差过大。', '到 Win10 校准系统时间（开启自动校时，检查虚拟机时间同步），校准后再恢复。'),
    'history_worker_lease_lost': ('旧采集脚本丢了租约（旧版本的暂停原因）。', '新采集器不应再出现；确认 Win10 上只运行一条采集链路。'),
}
ALI_NEXT = '到 Win10 检查：浏览器是否开着并登录阿里消息页，脚本是否暂停，电脑是否休眠；核对心跳上报的脚本版本。'
NEXT = {
    'alibaba_inbound': ALI_NEXT + '对照阿里原会话与收件箱，恢复采集后再看实时到达时间。',
    'alibaba_probe': ALI_NEXT + '等下一次心跳后刷新；心跳恢复不代表消息已经收全。',
    'wechat_messages': '到渠道接入核对微信接收服务、群的接入范围和消息处理状态，再对照微信原会话。',
    'wechat_callbacks': '到渠道接入核对微信登录、回调配置和接收服务，再看回调到达时间是否更新。',
    'whatsapp_events': '到渠道接入查看 WhatsApp 登录状态，并请管理员核对桥接服务和回调归档。',
    'whatsapp_messages': '到渠道接入查看 WhatsApp 登录状态；若事件仍在进，请管理员核对事件转消息的处理。',
    'whatsapp_session': '到渠道接入页查看是否需要重新扫码；请管理员查看 WAHA 容器的运行状态。',
}


def effective_age(last, now, day):
    """Count night time at a reduced rate; no abrupt alarm when 08:00 arrives."""
    start, end = minute(day['start']) * 60, minute(day['end']) * 60

    def daytime(at):
        days, second = divmod(at + 8 * 3600, 86400)
        return days * (end - start) + max(0, min(second - start, end - start))

    age = max(0, now - last)
    active = max(0, daytime(now) - daytime(last))
    return active + max(0, age - active) / day['night_multiplier']


def _session_state(item, now, config):
    if item['total'] == 0:
        return 'never', '没有可用的会话状态记录，无法确认是否登录成功。', NEXT['whatsapp_session']
    if item.get('session_status') is None:
        return 'unavailable', '最近一次会话状态无法识别，不能确认是否登录成功。', NEXT['whatsapp_session']
    if item['session_status'] == 'WORKING':
        if now - item['last_seen_at'] > config['whatsapp_session_max_age_seconds']:
            return 'unavailable', '最后记录为已登录可用，但状态记录已经太旧，需重新核对当前登录状态。', NEXT['whatsapp_session']
        evidence = '最近一次只读轮询确认 WhatsApp 会话已登录可用；这是轮询时的状态。' if item.get('status_source') == 'poll' else '最近记录的 WhatsApp 会话已登录可用；本项不是实时在线探测。'
        return 'ok', evidence, '继续观察；有正常业务消息时核对收件箱是否到达。'
    duration = now - item['non_working_since']
    item['effective_age_seconds'] = duration
    stopped = item['restart_storm'] is True or duration > config['thresholds']['whatsapp_session']['interrupted_seconds']
    retrying = item.get('failed_count_1h_lower_bound', 0) > 0
    reason = ('WhatsApp 会话没有登录成功，一直在失败重试。' if retrying else
              'WhatsApp 会话尚未登录可用，当前需要核对启动或扫码状态。')
    if item['restart_storm'] is True:
        reason += '近 10 分钟失败至少 20 次且没有登录成功，已判定为重启风暴。'
    return ('interrupted' if stopped else 'slow'), reason, NEXT['whatsapp_session']


def _classify(item, now, config, new_whatsapp):
    key = item['key']
    threshold = config['thresholds'][key]
    item['thresholds'] = dict(threshold)
    if key.startswith('alibaba_'):
        item['thresholds']['day'] = dict(config['alibaba_day'])
    item.update(effective_age_seconds=None, history_only=False, mix=None)
    if key == 'whatsapp_session':
        return _session_state(item, now, config)
    if key == 'whatsapp_messages' and new_whatsapp:
        item['new_account_waiting'] = True
        return 'ok', '会话已登录可用，本地还没有联系人，正常。当前没有消息记录，等待第一批联系人和会话。', '有正常业务消息时再看数据是否到达；登录状态变化后需重新核对。'
    if key == 'alibaba_inbound':
        kinds = item['kinds']
        item['mix'] = {name: dict(count_24h=row['count_24h'],
            ratio_24h=row['count_24h'] / item['count_24h'] if item['count_24h'] else None,
            last_arrival_at=row['last_seen_at']) for name, row in kinds.items()}
    if item['total'] == 0:
        return 'never', '数据表已接入，但从未收到记录。', NEXT[key]
    if item['last_seen_at'] is None:
        return 'unavailable', '已有记录的时间无效，暂时无法判断是否持续进来。', '请管理员核对来源记录的时间格式和服务器时钟，然后刷新。'
    last = item['last_seen_at']
    if key == 'alibaba_inbound':
        kinds = item['kinds']
        item['history_only'] = kinds['history']['count_24h'] > 0 and kinds['live']['count_24h'] == 0
        last = kinds['live']['last_seen_at']
        if last is None:
            if kinds['history']['total'] > 0:
                return 'interrupted', '从未收到带实时标记的消息；只有历史在进。历史补录不能证明实时采集正常。' if item['history_only'] else '只有历史记录，从未收到带实时标记的消息。', NEXT[key]
            return 'unavailable', '记录没有可识别的实时或历史标记，无法判断实时采集是否正常。', NEXT[key]
    age = effective_age(last, now, config['alibaba_day']) if key.startswith('alibaba_') else max(0, now - last)
    item['effective_age_seconds'] = age
    state = ('interrupted' if age > threshold['interrupted_seconds'] else
             'slow' if age > threshold['slow_seconds'] else 'ok')
    subject = ('实时消息' if key == 'alibaba_inbound' else '消息' if key in ('wechat_messages', 'whatsapp_messages')
               else '桥接服务事件' if key == 'whatsapp_events' else '数据')
    reason = (f'{subject}最近有更新，未超过变慢阈值。' if state == 'ok' else
              f'{subject}长时间没有更新，已超过' + ('断流' if state == 'interrupted' else '变慢') + '阈值；需要核对是否业务安静或采集停了。')
    if item['history_only']:
        reason = '近 24 小时只有历史在进，没有实时消息；' + reason
    if key == 'alibaba_probe' and (item.get('probe_state') == 'paused' or item.get('probe_error') == 'pairing_reconnect'):
        code = item.get('probe_error')
        known = PROBE_ERRORS.get(code)
        state = 'interrupted'
        reason = ('最近一次心跳报告采集异常：' + known[0] if known else
                  '最近一次心跳报告采集已暂停' + (f'（原因代码 {code}）' if code else '') + '，实时采集可能停止。')
        if known:
            return state, reason, known[1]
    return state, reason, NEXT[key] if state != 'ok' else '继续观察；若平台已有新消息而这里没有更新，请到渠道接入核对采集。'


class PipelineHealth:
    def __init__(self, *, config=None, config_path=None, paths=None, plan=None, plan_path=None,
                 coverage_readers=None, failed_units_reader=None, clock=None):
        self.config, self.config_path = config, config_path
        self.paths, self.plan, self.plan_path = paths, plan, plan_path
        self.coverage_readers, self.clock = coverage_readers or {}, clock or time.time
        self.failed_units_reader = failed_units_reader
        # A partial injected source set stays fully isolated from production.
        if paths is not None:
            if not paths or set(paths) - set(DEFAULTS['paths']):
                raise ValueError('管道数据来源参数无效')
            root = Path(next(iter(paths.values()))).resolve().parent
            self.paths = {key: Path(paths.get(key, root / ('disabled-' + key + '.sqlite3')))
                          for key in DEFAULTS['paths']}
            self.config_path = config_path if config_path is not None else root / 'disabled-pipeline-config.json'
            self.plan_path = plan_path if plan_path is not None else root / 'disabled-channel-plan.json'

    def read(self):
        now = self.clock()
        config, notes = resolve_config(self.config, self.config_path)
        paths = self.paths if self.paths is not None else config['paths']
        plan = resolve_plan(self.plan, self.plan_path)
        sources = []
        for key, (path_key, reader) in READERS.items():
            item = dict(key=key, channel=key.split('_')[0], label=LABELS[key], ready=False,
                        last_seen_at=None, last_arrival_at=None, count_1h=None, count_24h=None,
                        hourly=None, time_basis='message_time' if key == 'wechat_messages' else 'arrival',
                        mix=None, history_only=False, notes=[])
            if key in ('whatsapp_session', 'whatsapp_events'):
                item['time_basis'] = 'session_status' if key == 'whatsapp_session' else 'service_event'
            item['auxiliary'] = key == 'whatsapp_events'
            try:
                with read_db(paths[path_key], config['query_timeout_seconds']) as db:
                    snapshot = (reader(db, now, config['whatsapp_session_event_limit'])
                                if key == 'whatsapp_session' else reader(db, now))
                item.update(snapshot, ready=True)
                if key == 'alibaba_probe':
                    try:
                        intervals = windows(paths['maintenance'])
                        with read_db(paths['probe'], config['query_timeout_seconds']) as db:
                            item['continuity'] = heartbeat_continuity(db, now, intervals)
                    except (OSError, ValueError, TypeError, OverflowError):
                        item['continuity'] = None
                        item['notes'].append('维护窗口文件无法读取或格式无效，心跳连续性暂时未知；请管理员核对。')
            except (FileNotFoundError, MissingSchema):
                item.update(status='not_connected', reason='未找到可读取的数据文件或所需表，当前未接入这项统计。',
                            next_step='请管理员核对数据来源路径和表结构，接入后刷新；不要新建空库代替原库。')
            except PermissionError:
                item.update(status='unavailable', reason='没有权限读取这项数据。', next_step='请管理员核对控制台服务的只读权限，然后刷新。')
            except (sqlite3.Error, OSError, ValueError):
                item.update(status='unavailable', reason='统计暂时读不到，可能是库正在写入、结构不兼容或查询超时。',
                            next_step='稍后刷新；若持续出现，请管理员核对数据文件、表结构和查询耗时。')
            sources.append(item)
        wa_messages = next(item for item in sources if item['key'] == 'whatsapp_messages')
        wa_session = next(item for item in sources if item['key'] == 'whatsapp_session')
        new_whatsapp = (wa_messages['ready'] and wa_messages['total'] == 0 and wa_messages.get('no_contacts') is True
                        and wa_session['ready'] and wa_session.get('session_status') == 'WORKING'
                        and now - wa_session['last_seen_at'] <= config['whatsapp_session_max_age_seconds'])
        for item in sources:
            key = item['key']
            if item['ready']:
                item['status'], item['reason'], item['next_step'] = _classify(item, now, config, new_whatsapp)
                if key == 'wechat_messages' and item.get('pending_count') and item.get('pending_stalled_age_seconds') is not None:
                    if item['pending_stalled_age_seconds'] > config['wechat_pending_stuck_seconds']:
                        item.update(status='interrupted', reason=f"待处理消息积压卡住：还有 {item['pending_count']} 条待处理消息，最久未推进时间已超过阈值；新消息到达不能证明旧积压已处理。",
                                    next_step='请管理员核对微信消息处理作业和待处理队列；先确认停滞原因及处理结果，再决定如何恢复。')
                if item['time_basis'] == 'arrival':
                    item['last_arrival_at'] = item['last_seen_at']
                if item['invalid_timestamps']:
                    item['notes'].append(f"有 {item['invalid_timestamps']} 条记录的时间无效或在未来，未计入时间统计。")
            if key == 'wechat_messages':
                item['notes'].append('消息库没有到达时间；本项按消息发生时间统计，到达情况请看微信回调。')
            if key == 'alibaba_probe':
                item['notes'].append('心跳库只保留各账号最新状态，无法还原 1 小时、24 小时或 48 小时的心跳次数。')
            if key == 'whatsapp_events':
                item['notes'].append('只反映桥接服务仍在归档事件；包含会话状态事件，不算消息到达，也不参与渠道健康汇总。')
            if key == 'whatsapp_session':
                item['session_status_label'] = SESSION_LABELS.get(item.get('session_status'), '未知')
                item['notes'].append('只读最近的会话状态及其来源；轮询不计为消息到达或失败事件次数。')
                if item.get('sample_truncated'):
                    item['notes'].append('会话事件已达到采样上限；未覆盖完整时间窗口的失败次数显示未知，历史登录时间也可能在采样之外。')
            if not key.startswith('alibaba_'):
                item['notes'].append('来源没有可靠的实时／历史标记，暂不计算实时与历史占比。')
            item.pop('kinds', None)
            item.pop('no_contacts', None)
            item['status_label'] = STATUS[item['status']]
        by_key = {item['key']: item for item in sources}
        if by_key['wechat_messages']['status'] in ('slow', 'interrupted') and by_key['wechat_callbacks']['status'] == 'ok':
            by_key['wechat_messages']['reason'] += '回调仍有更新，请重点检查回调之后的消息处理和接入范围。'
        if by_key['whatsapp_messages']['status'] in ('slow', 'interrupted') and by_key['whatsapp_events']['status'] == 'ok':
            by_key['whatsapp_messages']['reason'] += '事件仍有更新，但事件不等于聊天消息。'
        coverage_readers = dict(self.coverage_readers)
        if not self.coverage_readers:
            coverage_readers[('wechat', 'attachments')] = lambda at: wechat_attachments(paths['wechat'], config['query_timeout_seconds'])
        channels = []
        for channel, label in CHANNELS.items():
            items = [item for item in sources if item['channel'] == channel and not item['auxiliary']]
            state = max((item['status'] for item in items), key=SEVERITY.get)
            channels.append(dict(channel=channel, label=label, status=state, status_label=STATUS[state],
                                 coverage=coverage(channel, coverage_readers, now)))
        for item in channels:
            if item['channel'] == 'wechat' and not self.coverage_readers and item['coverage'][0]['status'] == 'ready':
                item['coverage'][0]['reason'] = '全部微信消息中，按账号和消息编号关联到公司媒体表的消息数；不代表所有附件都已采集。'
        counts = Counter(item['status'] for item in channels)
        summary = '三个渠道：' + '，'.join(f'{counts[key]} {label}' for key, label in STATUS.items() if counts[key])
        if plan['whatsapp'] in ('not_started', 'planned'):
            notes.append('WhatsApp 尚在运营准备阶段；只有会话已登录可用且本地没有联系人和消息时才按新号等待处理，登录失败仍会报警。')
        notes.append('状态按本地记录判断，不能证明平台消息已收全；页面打开时每 60 秒自动检查，也可以手动刷新。')
        jobs_reader = self.failed_units_reader or (lambda at: read_failed_file(paths['failed_units'], at))
        return dict(generated_at=now, summary=summary, channels=channels, sources=sources, notes=notes,
                    scorecard=scorecard(by_key, paths, config, now), failed_jobs=failed_jobs(jobs_reader, now))
