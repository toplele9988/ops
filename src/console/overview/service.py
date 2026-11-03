"""Boss-facing projection of independent read-only source snapshots."""
from pathlib import Path
import time

from console.pipeline.config import DEFAULTS, validate
from console.pipeline.coverage import coverage, wechat_attachments
from console.pipeline.readers import READERS
from console.pipeline.service import NEXT, SEVERITY, STATUS, _classify

from .budget import read_budget
from .readers import alibaba_history, alibaba_knowledge, pause_since, wechat_knowledge, whatsapp_knowledge
from .safety import UNREADABLE, check_db, database, iso

PATHS = dict(DEFAULTS['paths'],
    calls='/var/lib/imgw/wechat/kb-model-runtime/calls.sqlite3',
    analysis='/var/lib/imgw/icbu/conversation-analysis/analysis.sqlite3',
    physical='/var/lib/imgw/ops-app/kb-recovery-coordinator/physical.sqlite3',
    free_snapshot='/etc/imgw/claude-free-model-snapshot.json',
    cpq='/var/lib/imgw/cpq-batch-qwen/ledger.json',
    wechat_usage='/var/lib/imgw/wechat/kb-model-runtime/usage.json',
    whatsapp_usage='/var/lib/imgw/whatsapp/model-runtime/usage.json',
    drafts_usage='/var/lib/imgw/icbu/live-drafts/usage.json',
    analysis_usage='/var/lib/imgw/icbu/conversation-analysis/quota-pool-20261024/usage.json',
    media_usage='/var/lib/imgw/video_knowledge_auto/equipment/qwen-free-quota-protocol-20261024.json')
CHANNELS = (('wechat', '微信'), ('alibaba', '阿里国际站'), ('whatsapp', 'WhatsApp'))
STAGES = dict(connected='接入', collecting='实时采集', stored='保存', knowledge='进知识库')
KB_NEXT = '请管理员核对知识处理账本、既有作业与停止原因；保留未知请求，不自动重试。'
HISTORY_DETAIL = '历史完整性未证明：没有已核验的逐会话到顶证据；消息数量、月份分布和最早时间都不能证明收全。'


def worst(states):
    return max(states, key=SEVERITY.__getitem__)


def stage(key, state='unavailable', detail=UNREADABLE, **values):
    item = dict(key=key, label=STAGES[key], state=state, detail=detail,
                last_arrival_at=None, age_seconds=None, count_24h=None,
                total=None, processed=None, pending=None, last_processed_at=None)
    item.update(values)
    return item


def source_state(source):
    if not source['ready'] or not source.get('total') or source.get('invalid_timestamps'):
        return 'unavailable'
    return source['status'] if source['status'] not in ('never', 'not_connected') else 'unavailable'


class Overview:
    def __init__(self, *, paths=None, config=None, clock=None):
        self.config = validate(config or {})
        self.clock = clock or time.time
        defaults = dict(PATHS, **self.config['paths'])
        if paths is not None:
            if not paths or set(paths) - set(PATHS):
                raise ValueError('总览数据来源参数无效')
            if any(not Path(path).is_absolute() for path in paths.values()):
                raise ValueError('总览来源须为服务端绝对路径')
            root = Path(next(iter(paths.values()))).parent
            # Partial injection disables EVERY omitted source, including budget JSON.
            defaults = {key: paths.get(key, root / ('disabled-overview-' + key)) for key in PATHS}
        self.paths = {key: Path(value) for key, value in defaults.items()}

    def _sources(self, now):
        result = {}
        seconds = self.config['query_timeout_seconds']
        for key, (path_key, reader) in READERS.items():
            if key == 'whatsapp_events':
                continue  # service events are not arrivals or login evidence
            item = dict(key=key, ready=False, total=None, last_seen_at=None,
                        count_24h=None, invalid_timestamps=0, status='unavailable', reason=UNREADABLE)
            try:
                with database(self.paths[path_key], seconds) as db:
                    value = (reader(db, now, self.config['whatsapp_session_event_limit'])
                             if key == 'whatsapp_session' else reader(db, now))
                    if key == 'alibaba_probe':
                        value['pause_since'] = None
                        if value.get('probe_state') == 'paused':
                            try:
                                value['pause_since'] = pause_since(db, now, value['last_seen_at'])
                            except Exception:
                                pass  # latest heartbeat still usable; pause duration is unknown
                item.update(value, ready=True)
            except Exception:
                pass
            result[key] = item
        for item in result.values():
            if item['ready']:
                try:
                    item['status'], item['reason'], _ = _classify(item, now, self.config, False)
                except Exception:
                    item.update(status='unavailable', reason=UNREADABLE)
        return result

    def _arrival(self, key, source, now, *, message_time=False):
        state = source_state(source)
        at = source['last_seen_at']
        detail = source['reason'] if state != 'unavailable' or not source['ready'] else '未知：没有有效、完整的时间记录，无法判断。'
        if message_time:
            detail += ' 微信库只有消息发生时间，不能当作到达时间。'
        return stage(key, state, detail, total=source['total'], count_24h=source['count_24h'],
                     last_arrival_at=iso(at) if not message_time else None,
                     age_seconds=now - at if at is not None and not message_time else None)

    def _probe(self, source, now):
        if not source['ready'] or not source.get('total') or source.get('last_seen_at') is None:
            return stage('connected', detail='阿里心跳状态未知。' + UNREADABLE)
        at = source['last_seen_at']
        age = now - at
        state = ('interrupted' if age > 300 else 'unavailable'
                 if source.get('probe_state') is None or source.get('invalid_timestamps') else 'ok')
        detail = f'最近心跳距今 {age:.0f} 秒；超过 300 秒判断流。心跳不证明消息收全或发送可用。'
        if source.get('probe_state') is None or source.get('invalid_timestamps'):
            detail += ' 心跳状态或部分时间未知。'
        return stage('connected', state, detail,
                     last_arrival_at=iso(at), age_seconds=age)

    def _alibaba_collecting(self, inbound, probe, now):
        item = self._arrival('collecting', inbound, now)
        live = inbound.get('kinds', {}).get('live')
        if live is not None:
            at = live['last_seen_at']
            item.update(last_arrival_at=iso(at), age_seconds=now - at if at is not None else None,
                        count_24h=live['count_24h'], total=live['total'])
            if not live['total']:
                item.update(state='unavailable', detail='未知：没有可识别的实时来源；历史入库不能证明实时采集正常。')
        connection = self._probe(probe, now)
        probe_state, detail = connection['state'], connection['detail']
        if probe_state != 'unavailable' and probe.get('probe_state') == 'paused':
            since = probe.get('pause_since')
            duration = now - since if since is not None else None
            error = probe.get('probe_error') or '未提供或不符合安全错误码格式'
            if duration is None:
                paused_state = 'unavailable'
                detail += f' state=paused；暂停时长未知，缺少连续流水证据；last_error={error}。'
            else:
                # An observed short pause is also not green. Mandatory thresholds
                # are strict >10min yellow, >30min red; no night discount applies.
                paused_state = 'interrupted' if duration > 1800 else 'slow'
                detail += f' state=paused；连续暂停至少 {duration:g} 秒（{duration / 60:.1f} 分钟）；last_error={error}。'
                if duration > 1800:
                    detail += ' 暂停超过 30 分钟。'
                elif duration > 600:
                    detail += ' 暂停超过 10 分钟。'
            probe_state = worst((probe_state, paused_state))
            if probe.get('probe_error') == 'pairing_reconnect':
                probe_state = 'interrupted'
                detail += ' ' + probe['reason']
        elif probe_state == 'ok' and probe.get('probe_error'):
            # Keep pipeline's known collector error handling, including pairing_reconnect.
            probe_state = probe['status']
            detail += ' ' + probe['reason']
        item['state'] = worst((item['state'], probe_state))
        item['detail'] += ' ' + detail
        return item

    def _stored(self, source, now, *, wechat=False):
        item = self._arrival('stored', source, now, message_time=wechat)
        if source['ready'] and source.get('total') and source.get('last_seen_at') is not None and not source.get('invalid_timestamps'):
            item.update(state='ok', detail=f"本地消息表已保存 {source['total']} 条记录；这不证明平台历史完整。")
            if wechat:
                item['detail'] += f" 最新消息发生于 {iso(source['last_seen_at'])}；24 小时数按发生时间计。"
                if not source.get('pending_ready'):
                    item.update(state='unavailable', detail=item['detail'] + ' 消息处理积压未知：缺少状态或更新时间列。')
                elif source.get('pending_invalid_timestamps'):
                    item.update(state='unavailable', detail=item['detail'] + ' pending 时间无效，处理积压未知。')
                elif (source.get('pending_stalled_age_seconds') is not None
                        and source['pending_stalled_age_seconds'] > self.config['wechat_pending_stuck_seconds']):
                    item.update(state='interrupted', detail=item['detail'] +
                        f" 仍有 {source['pending_count']} 条 pending，最久未推进 {source['pending_stalled_age_seconds']:.0f} 秒。")
        return item

    def _knowledge(self, channel, source, now):
        path_key, reader = {'wechat': ('calls', wechat_knowledge),
                            'alibaba': ('analysis', alibaba_knowledge),
                            'whatsapp': ('whatsapp', whatsapp_knowledge)}[channel]
        try:
            with database(self.paths[path_key], self.config['query_timeout_seconds']) as db:
                value = reader(db, now)
        except Exception:
            return stage('knowledge')
        total, processed, pending = (value[key] for key in ('total', 'processed', 'pending'))
        last = value['last_processed_at']
        state = 'slow' if pending else 'ok'
        detail = f'已登记 {total}，成功处理 {processed}，未完成 {pending}。'
        if not total or value['invalid'] or value.get('other'):
            state = 'unavailable'
            detail += ' 未知：零分母、无效时间或未识别状态，不能判正常。'
        if value.get('blocked'):
            state = 'interrupted'
            detail += f" 有 {value['blocked']} 个作业被阻断或需修复。"
        if channel == 'wechat':
            detail += ' 计数单位为模型调用；complete 是调用成功，不等于逐消息索引覆盖。'
            message = source.get('last_seen_at') if source['ready'] else None
            if last is None or message is None or not source.get('total') or source.get('invalid_timestamps'):
                state = 'unavailable'
                detail += ' 微信最新消息或最后成功处理时间未知，无法计算滞后。'
            else:
                lag = max(0, message - last)
                detail += f' 最后成功处理 {iso(last)}，最新消息 {iso(message)}，处理滞后 {lag:g} 秒（{lag / 3600:.2f} 小时）。'
                if lag > 86400:
                    state = 'interrupted'
                    detail += ' 超过 24 小时。'
        elif channel == 'alibaba':
            detail += ' 仅当前版本 windows；validated 为窗口处理完成，未完成含 pending/retry_wait/infra_blocked/needs_repair。不是发布索引或历史完整性证明。'
        else:
            detail += ' 仅 knowledge_jobs；indexed_claims/indexed_no_supported_claims 为完成，raw_indexed 和媒体待办不算完成。'
        if total and processed and last is None:
            state = 'unavailable'
            detail += ' 最后成功处理时间未知。'
        return stage('knowledge', state, detail, total=total, processed=processed, pending=pending,
                     last_processed_at=iso(last), age_seconds=now - last if last is not None else None)

    def _history(self, now):
        result = dict(messages=None, conversations=None, earliest_sent_at=None, latest_sent_at=None,
                      by_month=[], completeness='unproven', detail=HISTORY_DETAIL)
        try:
            with database(self.paths['inbound'], self.config['query_timeout_seconds']) as db:
                value = alibaba_history(db, now)
            invalid, missing = value.pop('invalid'), value.pop('missing')
            value['earliest_sent_at'] = iso(value['earliest_sent_at'])
            value['latest_sent_at'] = iso(value['latest_sent_at'])
            result.update(value)
            result['detail'] += ' 统计当前 inbound 的归档消息行，含实时、历史和未标明来源；未按客户正文去重。月份按消息 sent_at 的 UTC 月份计。'
            if not value['messages']:
                result['detail'] += ' 零样本，时间跨度与会话数未知。'
            if invalid or missing:
                result['detail'] += f' {invalid} 条发送时间无效或缺失，{missing} 条缺少会话元数据；相应统计未知或仅含有效时间。'
        except Exception:
            result['detail'] += ' ' + UNREADABLE
        return result

    def _attachments(self, now):
        def read(_):
            check_db(self.paths['wechat'])
            return wechat_attachments(self.paths['wechat'], self.config['query_timeout_seconds'])
        # Existing coverage validates counts, zero denominators and per-source failures.
        value = coverage('wechat', {('wechat', 'attachments'): read}, now)[0]
        if value['status'] == 'ready' and value['denominator']:
            return f"附件关联 {value['numerator']}/{value['denominator']}；复用 pipeline coverage，仅为公司媒体关联，不代表全部附件已采集。"
        return '附件关联覆盖率未知：来源未就绪、零分母或读取失败。'

    def read(self):
        now = self.clock()
        sources = self._sources(now)
        channels = []
        next_steps = {}
        for key, label in CHANNELS:
            message = sources[key + ('_inbound' if key == 'alibaba' else '_messages')]
            notes = ['各来源为独立只读快照，不保证跨库原子一致；只依据本地记录，不是渠道在线或全量历史验收。']
            if key == 'alibaba':
                connected = self._probe(sources['alibaba_probe'], now)
                collecting = self._alibaba_collecting(message, sources['alibaba_probe'], now)
                notes.append(HISTORY_DETAIL)
            elif key == 'wechat':
                callback = sources['wechat_callbacks']
                connected = self._arrival('connected', callback, now)
                connected['detail'] += ' 接入证据来自本地回调，不是实时登录探测。'
                collecting = self._arrival('collecting', callback, now)
                collecting['detail'] += ' 回调到达不等于消息已保存或已进知识库。'
                notes.append(self._attachments(now))
            else:
                session = sources['whatsapp_session']
                connected = stage('connected', source_state(session), session['reason'],
                                  age_seconds=now - session['last_seen_at'] if session['last_seen_at'] is not None else None)
                connected['detail'] += ' 静止期不产生状态事件，未必是故障。'
                collecting = self._arrival('collecting', message, now)
                notes.append('会话沿用 pipeline 的 WORKING 时限、连续失败与重启风暴规则；归档状态不是实时探测。')
            stages = [connected, collecting, self._stored(message, now, wechat=key == 'wechat'),
                      self._knowledge(key, message, now)]
            state = worst(item['state'] for item in stages)
            problem = next((item for item in stages if item['state'] == state), stages[0])
            headline = f'{label}四个环节正常' if state == 'ok' else f"{label}：{problem['label']}{STATUS[state]}"
            channels.append(dict(key=key, label=label, state=state, headline=headline, stages=stages, notes=notes))
            next_steps[key] = NEXT[key + ('_inbound' if key == 'alibaba' else '_messages')]
        budget = read_budget(self.paths, self.config['query_timeout_seconds'], now)
        history = self._history(now)
        problems = [dict(channel=channel['key'], text=f"{item['label']}：{item['detail']}",
                         next_step=KB_NEXT if item['key'] == 'knowledge' else next_steps[channel['key']])
                    for channel in channels for item in channel['stages'] if item['state'] != 'ok']
        states = [channel['state'] for channel in channels]
        if (budget['deepseek_official'] is None or not budget['free_pools']
                or any(item['remaining_tokens'] is None for item in budget['free_pools'])):
            states.append('unavailable')
            problems.append(dict(channel='model_budget', text='模型预算部分未知；' + budget['note'],
                                 next_step='请管理员核对协调器已落盘余额与 ai-task 账本和观察时间；本接口不请求模型或余额 API。'))
        elif (budget['deepseek_official']['balance_micros'] == 0
              or any(item['remaining_tokens'] == 0 for item in budget['free_pools'])):
            states.append('slow')
            problems.append(dict(channel='model_budget', text='部分模型预算的本地观察余额为零。',
                                 next_step='核对预算账本与观察时间，由既有协调器决定停止的预算桶。'))
        states.append('unavailable')  # no per-conversation end-of-history evidence exists
        problems.append(dict(channel='alibaba', text=HISTORY_DETAIL,
                             next_step='补齐并核对每个会话的到顶证据；不要用消息数量或最早月份推断完整。'))
        state = worst(states)
        headline = ('有环节断流，请优先处理红色问题。' if state == 'interrupted' else
                    '部分证据未知，请核对来源与历史完整性。' if state == 'unavailable' else
                    '有环节变慢，请核对积压。' if state == 'slow' else '已核对的环节正常。')
        return dict(generated_at=iso(now), summary=dict(state=state, headline=headline, problems=problems),
                    channels=channels, model_budget=budget, history=dict(alibaba=history))
