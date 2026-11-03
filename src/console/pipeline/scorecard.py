"""Measured acceptance checks; absent evidence never becomes a pass."""
import sqlite3

from .readers import HEARTBEAT_SLOT, JOB_READERS, MissingSchema, job_counts, read_db

RESULTS = {'pass': '达标', 'fail': '不达标', 'unknown': '未知'}
SESSION_LABELS = {'WORKING': '已登录可用', 'FAILED': '登录失败', 'STARTING': '正在启动',
                  'STOPPED': '已停止', 'SCAN_QR_CODE': '等待扫码', 'PASSKEY_REQUIRED': '需要通行密钥',
                  'PASSKEY_CONFIRMATION_REQUIRED': '等待确认通行密钥'}


def age(seconds):
    if seconds is None:
        return '未知'
    if seconds < 60:
        return f'{seconds:.0f} 秒'
    if seconds < 3600:
        return f'{seconds / 60:.1f} 分钟'
    return f'{seconds / 3600:.1f} 小时'


def metric(key, name, target, actual, source, method, result='unknown', *, required=True):
    return dict(key=key, name=name, target=target, actual=actual, result=result,
                result_label=RESULTS[result], source=source, method=method, required=required)


def _age_metric(key, name, item, now, target, limit):
    last = item.get('last_seen_at')
    measured = now - last if last is not None else None
    result = ('pass' if measured <= limit else 'fail') if item['ready'] and measured is not None else 'unknown'
    return metric(key, name, target, age(measured) + ('前' if measured is not None else ''), item['label'],
                  '按服务器归档的最后接收时间计算实际空档，不套用夜间折扣。', result)


def _jobs(paths, config):
    result = {}
    for key, (path_key, table, column, successful, states) in JOB_READERS.items():
        item = dict(ready=False, reason='所需表或列未接入，请管理员核对只读来源。')
        try:
            with read_db(paths[path_key], config['query_timeout_seconds']) as db:
                snapshot = job_counts(db, table, column, successful, states)
            item.update(snapshot, ready=True, reason='按当前保留的全部登记作业统计；不包含未登记作业。')
        except (FileNotFoundError, MissingSchema):
            pass
        except (sqlite3.Error, OSError, ValueError):
            item['reason'] = '统计暂时读不到，请管理员核对只读权限、表结构和查询耗时。'
        result[key] = item
    return result


def _completion(key, name, item, source, method):
    known = item['ready'] and item['total'] > 0
    actual = f"{item['completed']} / {item['total']}（{item['ratio']:.1%}）" if known else '暂无登记作业' if item['ready'] else '未知'
    return metric(key, name, '已登记作业完成占比 100%', actual, source,
                  method + ' ' + item['reason'], ('pass' if item['completed'] == item['total'] else 'fail') if known else 'unknown')


def _a1(probe):
    target = '24 小时按 120 秒发送，到达率 ≥99%，不得连续漏 3 次'
    source = '阿里心跳 · heartbeat_log 逐次流水'
    method = ('按最近 24 小时划分 120 秒时间槽，就近归槽且每槽最多计一次到达；重复或突发心跳不能补足空槽。暂停状态的心跳也算到达。'
              '登记的 UTC 维护窗口仅扣除漏收槽，每日最多扣 30 分钟；重叠不重复扣除。不足 1 小时、不足 24 小时但没有漏收时保持未知。')
    cont = probe.get('continuity') if probe['ready'] else None
    if not cont or not cont['slots']:
        return metric('A1', 'A1 心跳连续性', target, '未知', source,
                      '还没有逐次心跳流水，或流水刚开始；最新快照不能计算到达率。', 'unknown')
    hours = cont['window_seconds'] / 3600
    actual = f"{hours:.1f} 小时内 {cont['present']}/{cont['slots']} 槽到达（{cont['rate'] * 100:.1f}%），最长连续漏 {cont['max_consecutive_missing']} 槽；维护窗口扣除 {cont.get('maintenance_slots', 0)} 槽"
    broken = cont['max_consecutive_missing'] >= 3 or cont['rate'] < 0.99
    if broken and hours >= 1:
        result = 'fail'
    elif not broken and cont['window_seconds'] >= 86400 - HEARTBEAT_SLOT:
        result = 'pass'
    else:
        result = 'unknown'
    return metric('A1', 'A1 心跳连续性', target, actual, source, method, result)


def _alibaba(sources, now):
    incoming, probe = sources['alibaba_inbound'], sources['alibaba_probe']
    live = (incoming.get('mix') or {}).get('live')
    live_last = live['last_arrival_at'] if live else None
    gap = now - live_last if live_last is not None else None
    daytime = 8 <= ((now + 8 * 3600) % 86400) / 3600 < 22
    day_start = ((now + 8 * 3600) // 86400) * 86400
    day_gap = now - max(live_last or day_start, day_start)
    a5 = ('fail' if day_gap > 7200 else 'pass' if live_last is not None and live_last >= day_start else 'unknown') if daytime and incoming['ready'] else 'unknown'
    items = [metric('live_24h', '近 24 小时实时标记消息数', '供核对业务量，无最低消息数',
                    str(live['count_24h']) + ' 条' if live else '未知', '阿里入站 · inbound.created_at / history',
                    '滚动 24 小时，仅计 history=false 且不是 history_import 的记录；实时标记不等于来源验收。', required=False),
             metric('last_live_age', '最后一次实时到达距今', '供核对最后接收时间',
                    age(gap) + ('前' if gap is not None else ''), '阿里入站 · 实时标记记录的 created_at',
                    '按实际墙上时间计算，不套用作息折扣；当前无实时记录时为未知。', required=False),
             metric('A5', 'A5 实时到达空档', '北京时间 08:00–22:00 持续到达；连续 2 小时无实时需说明',
                    '当前白天空档 ' + age(day_gap) if daytime and incoming['ready'] else '未知；当前不在白天验收时段或来源未接入',
                    '阿里入站 · 实时标记记录的 created_at',
                    '仅检查当前白天，从当天 08:00 与最后实时到达的较晚者开始计时；超过 2 小时需说明业务安静或排查停采。夜间或白天尚无消息且不足 2 小时为未知。', a5),
             metric('probe_state', '心跳状态与原因', '采集未暂停且状态可识别',
                    probe['status_label'] + '；' + probe['reason'], '阿里心跳 · probe_state 最新快照',
                    '网关心跳在线不能证明扩展已配对；已知错误也会报警。',
                    ('fail' if probe['status'] == 'interrupted' else 'pass' if probe['status'] == 'ok' and
                     probe.get('probe_state') in ('running', 'idle') and not probe.get('probe_error') else 'unknown')),
             _age_metric('probe_age', '心跳最近接收距今', probe, now, '不超过 5 分钟', 300)]
    items.append(_a1(probe))
    for key, name, target, missing in (
        ('A2', '实时延迟', '页面出现至持久确认：P95 ≤120 秒，最大 ≤10 分钟', '缺少校准后的页面出现时间与持久确认配对样本、时钟校准证据。'),
        ('A3', '每日对账', '页面新消息数与实时入库数相等，连续 3 天差异为 0，差异须说明', '缺少页面侧每日新消息计数和连续三天的对账记录。'),
        ('A4', '去重', '消息标识或指纹零重复，重放同批消息不增加行数', '缺少受控重放前后计数和去重验收记录；当前保留行数不能证明重放通过。'),
        ('A6', '断点恢复', '网关重启、扩展重载、网络短断分别在 10 分钟内恢复，无丢失、重复、游标回退，保留回执', '缺少三类现场演练与服务器读回结果。'),
        ('A7', '时钟', '时钟偏差 <30 秒；偏差 ≥300 秒暂停并报时钟异常', '缺少可独立核对的时钟偏差及暂停验证；服务器时间字段本身不能证明客户端已校时。'),
        ('A8', '连续运行', '满足 A1–A6 后连续 72 小时无需人工干预，包含完整白天业务时段', '缺少已通过的 A1–A6 和连续 72 小时巡检记录。'),
    ):
        items.append(metric(key, key + ' ' + name, target, '未知', '尚缺验收记录', missing))
    return items


def _wechat(sources, jobs, config, now):
    messages, callbacks = sources['wechat_messages'], sources['wechat_callbacks']
    message_count, callback_count = messages['count_24h'], callbacks['count_24h']
    ratio = callback_count / message_count if callback_count is not None and message_count else None
    actual = (f'回调 {callback_count} 条 / 消息 {message_count} 条；比值 {ratio:.2f}' if ratio is not None else
              f'回调 {callback_count} 条 / 消息 {message_count} 条；消息数为零，无比值' if message_count == 0 and callback_count is not None else '未知')
    items = [metric('callbacks_to_messages', '近 24 小时回调与消息对比', '仅供参考，不作为丢失证据', actual,
                    '微信消息库 · received_callback_archive / messages',
                    '回调按 received_at，消息按 created_at；时间口径、事件范围和去重方式不同，不能一一对应。', required=False),
             metric('last_arrival', '消息最后到达', '能够独立核对消息接收时间',
                    '未知；最近消息发生于 ' + age(now - messages['last_seen_at']) + '前' if messages['last_seen_at'] is not None else '未知；没有可核对的消息接收时间',
                    '微信消息库 · messages.created_at', '现有消息表没有接收时间，不能把发生时间或最后一条回调时间当作消息到达。')]
    limit = config['wechat_pending_stuck_seconds']
    pending = messages.get('pending_count')
    stalled = messages.get('pending_stalled_age_seconds')
    known = messages.get('pending_ready') and (pending == 0 or stalled is not None and not messages['pending_invalid_timestamps'])
    result = ('fail' if pending and stalled is not None and stalled > limit else 'pass' if known else 'unknown')
    actual = f"{pending} 条；最老消息年龄 {age(messages.get('pending_oldest_age_seconds'))}；最久未推进 {age(stalled)}" if pending is not None else '未知'
    items.append(metric('pending', '待处理积压', '没有停滞超过 ' + age(limit) + ' 的待处理消息', actual,
                        '微信消息库 · messages.status / created_at / updated_at',
                        '只计 pending；最老年龄按消息发生时间，停滞按 updated_at，缺失时用 created_at，均不是到达年龄。', result))
    for key, name, source, method in (
        ('wechat_video', '视频号媒体索引占比', '微信消息库 · video_card_jobs.state', 'indexed / 全部登记作业。'),
        ('wechat_collection', '附件采集完成占比', '微信消息库 · receive_collection_jobs.state', 'complete / 全部登记作业；complete 只表示作业结束，不保证解析、索引或原件可用。'),
        ('wechat_notes', '笔记附件就绪占比', '微信消息库 · company_note_assets.status', 'ready / 全部登记资源；不以 archive_status 代替正文就绪状态。'),
    ):
        items.append(_completion(key, name, jobs[key], source, method))
    video = jobs['wechat_video']
    for state, name in (('blocked', '视频号阻塞作业'), ('download_unknown', '视频号下载结果未知作业')):
        count = video['states'][state] if video['ready'] else None
        items.append(metric('video_' + state, name, '0 条', str(count) + ' 条' if count is not None else '未知',
                            '微信消息库 · video_card_jobs.state', '单独列出，不自动重试结果未知的下载。' + video['reason'],
                            ('fail' if count else 'pass') if count is not None and video['total'] else 'unknown'))
    return items


def _whatsapp(sources, jobs, config, now):
    session, messages = sources['whatsapp_session'], sources['whatsapp_messages']
    state = session.get('session_status')
    failed = session.get('failed_count_24h')
    lower = session.get('failed_count_24h_lower_bound', 0)
    working = session.get('last_working_at')
    media = jobs['whatsapp_media']
    labels = {'archived': '已归档', 'none': '无媒体', 'pending_media_url': '等待媒体地址', 'deferred': '暂缓处理', 'other': '其他或未识别'}
    actual = '；'.join(f'{labels[key]} {count} 条' for key, count in media.get('states', {}).items()) if media['ready'] else '未知'
    items = [metric('session_working', '会话是否已登录可用', '会话为已登录可用，且状态记录不超过 ' + age(config['whatsapp_session_max_age_seconds']),
                    SESSION_LABELS.get(state, '未知') + '；' + session['reason'], 'WhatsApp 事件库 · 最近会话状态',
                    '仅提取 payload.status 和是否有 me；不是实时探测。',
                    'pass' if session['status'] == 'ok' and state == 'WORKING' else 'fail' if state and state != 'WORKING' else 'unknown'),
             metric('last_working', '最后一次已登录可用', '用于核对会话恢复时间',
                    age(now - working) + '前' if working is not None else '最近采样中没有记录',
                    'WhatsApp 事件库 · 最近会话状态', '只在有界采样中查找；超出采样的历史不能补算。', required=False),
             metric('failed_24h', '近 24 小时登录失败次数', '0 次',
                    str(failed) + ' 次' if failed is not None else f'未知；已采样至少 {lower} 次' if session.get('sampled_events') else '未知；没有可用的会话状态样本',
                    'WhatsApp 事件库 · 会话状态为登录失败', '滚动 24 小时，采样未覆盖整个窗口时不报精确计数。',
                    ('fail' if failed else 'pass') if failed is not None else 'fail' if lower else 'unknown'),
             _age_metric('last_arrival', '消息最后到达', messages, now, '不超过 24 小时；已登录的新号无消息时可等待', 86400),
             metric('media_jobs', '媒体作业状态分布', '已登记的媒体作业全部归档，无等待或暂缓', actual,
                    'WhatsApp 消息库 · media_jobs.status',
                    '此表来自既有知识处理作业，含无媒体消息；不能证明 P2a 入站媒体链路已接通。' + media['reason'],
                    ('pass' if media['states']['archived'] + media['states']['none'] == media['total'] else 'fail')
                    if media['ready'] and media['total'] > media['states']['none'] else 'unknown')]
    if messages.get('new_account_waiting'):
        items[3].update(actual='会话已登录，本地没有联系人和消息，等待正常业务', result='pass', result_label=RESULTS['pass'])
    return items


def scorecard(sources, paths, config, now):
    jobs = _jobs(paths, config)
    result = []
    for channel, label, items in (
        ('alibaba', '阿里国际站', _alibaba(sources, now)),
        ('wechat', '微信', _wechat(sources, jobs, config, now)),
        ('whatsapp', 'WhatsApp', _whatsapp(sources, jobs, config, now)),
    ):
        required = [item['result'] for item in items if item['required']]
        status = 'fail' if 'fail' in required else 'unknown' if not required or 'unknown' in required else 'pass'
        result.append(dict(channel=channel, label=label, result=status, result_label=RESULTS[status], metrics=items))
    return result
