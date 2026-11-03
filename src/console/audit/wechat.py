"""Content-free WeChat coverage checks over existing directory and queue metadata."""
import sqlite3

from .common import finding, read_db, sample_id
from console.channels.common import timestamp
from console.directory import WechatDirectory

DAY = 86400
DIRECTORY = '系统后台 → 渠道接入 → 微信通讯录；核对准确群和用途后走现有授权接入流程'
MEDIA = '系统后台 → 渠道接入 → 微信 → 附件采集；核对下载、归档和解析状态后人工处理'


def _directory(ctx, required):
    if 'wechat.directory' not in ctx.memo:
        directory = ctx.directory or WechatDirectory(discovery_path=ctx.discovery_path,
            config_path=ctx.wechat_config_path, state_path=ctx.wechat_path, clock=lambda: ctx.now)
        ctx.memo['wechat.directory'] = directory.audit_snapshot(ctx.now, database=read_db)
    result = ctx.memo['wechat.directory']
    for source in required:
        if not result['sources'].get(source):
            kind = result.get('source_errors', {}).get(source, 'format')
            error = {'missing': FileNotFoundError, 'permission': PermissionError, 'timeout': TimeoutError,
                     'database': sqlite3.DatabaseError, 'format': ValueError, 'unavailable': OSError}.get(kind, ValueError)
            raise error from None
    return (row for row in result['items'] if row['kind'] == 'group')


def _groups(ctx, key, title, required, predicate, level, meaning, remedy=DIRECTORY):
    count, samples = 0, []
    for row in _directory(ctx, required):
        if predicate(row):
            count += 1
            if len(samples) < 5:
                samples.append(dict(id=sample_id(row['id']), last_message_at=timestamp(row['last_message_at'])))
    return finding(key, title, level if count else 'ok', count, meaning if count else '当前可读取的目录中未发现此类缺口。',
                   remedy, samples)


def check_not_enrolled(ctx):
    return _groups(ctx, 'wechat.not_enrolled', '微信未接入采集的群', ('discovery', 'config'),
                   lambda row: row['in_directory'] and row['registered'] is False, 'medium',
                   '通讯录已发现这些群，但没有群采集登记；历史记录可能仍在，新消息是否持续收到无法保证。')


def check_silent_groups(ctx):
    return _groups(ctx, 'wechat.silent_groups', '微信已接入但近七天无消息的群', ('config', 'messages'),
                   lambda row: row['collect_state'] in ('enrolled_silent', 'enrolled_no_messages'), 'medium',
                   '这些群已登记并启用，但近七天没有归档消息；可能群内安静，也可能采集有缺口，需核对。')


def check_role_conflicts(ctx):
    return _groups(ctx, 'wechat.role_conflicts', '微信群角色与命名规则冲突', ('config', 'messages'),
                   lambda row: row['role_conflict'], 'high',
                   '群的明确配置用途与名称线索不一致，或名称同时含 SUP/CUS，可能影响资料分类与可见范围。')


def check_unclassified_groups(ctx):
    return _groups(ctx, 'wechat.unclassified_groups', '微信仍未分类的群', ('discovery', 'config', 'messages', 'inbox'),
                   lambda row: row['role'] == 'unclassified', 'low',
                   '这些群的有效目录角色仍是未分类，系统尚不能确定应按客户、供应商还是其他用途整理。')


_ARCHIVED = "(archive_status='ready' AND length(archive_sha256)=64 AND archive_sha256 NOT GLOB '*[^0-9a-f]*' AND archive_bytes>0)"


def check_media_archive(ctx):
    with read_db(ctx.wechat_path) as db:
        total, ready = db.execute('SELECT COUNT(*),COALESCE(SUM(CASE WHEN ' + _ARCHIVED +
                                 ' THEN 1 ELSE 0 END),0) FROM company_media').fetchone()
        count = total - ready
        samples = [dict(id=sample_id((app, mid, ordinal))) for app, mid, ordinal in db.execute(
            'SELECT app_id,message_id,ordinal FROM company_media WHERE NOT COALESCE(' + _ARCHIVED +
            ',0) ORDER BY app_id,message_id,ordinal LIMIT 5')]
    return finding('wechat.media_archive', '微信附件原文件归档缺口', 'medium' if count else 'ok', count,
                   f'已登记附件 {total} 个，其中 {ready} 个具有完整归档元数据；归档不等于已解析或已索引。', MEDIA,
                   samples, total=total, archived=ready, archive_rate=ready / total if total else None,
                   scope='company_media 登记项；不包含未登记的附件，也未逐文件校验磁盘内容')


def _jobs(ctx, key, title, where, args, level, meaning):
    with read_db(ctx.wechat_path) as db:
        count = db.execute('SELECT COUNT(*) FROM receive_collection_jobs WHERE ' + where, args).fetchone()[0]
        samples = [dict(id=sample_id(job), updated_at=timestamp(updated)) for job, updated in db.execute(
            'SELECT job_id,updated_at FROM receive_collection_jobs WHERE ' + where +
            ' ORDER BY updated_at,job_id LIMIT 5', args)]
    return finding(key, title, level if count else 'ok', count,
                   meaning if count else '当前队列未发现此类问题。', MEDIA + '；结果不明的任务先核对，勿直接清队列或盲目重试', samples)


def check_jobs_raw_no_text(ctx):
    return _jobs(ctx, 'wechat.jobs_raw_no_text', '微信原文件已存但没有可用正文', "state='RAW_ARCHIVED_NO_TEXT'", (),
                 'medium', '原文件已被保存并核验，但未得到可用正文；可能等待云解析配置或额度，也可能格式不支持，不是原文件丢失。')


def check_jobs_failed(ctx):
    # The worker can finish with state=complete AND result_status=download_failed.
    # Conversely RAW_ARCHIVED_NO_TEXT is not an archive failure.
    where = "state IN ('interrupted','source_changed') OR (state!='RAW_ARCHIVED_NO_TEXT' AND (result_status LIKE '%failed%' OR result_status IN ('source_revoked','source_changed')))"
    return _jobs(ctx, 'wechat.jobs_failed', '微信附件采集失败或中断', where, (), 'high',
                 '附件任务记录了下载、解析失败、中断或来源变化；部分下载失败仍在等待重试，complete 也不保证处理成功。')


def check_jobs_stuck(ctx):
    where = "(state='processing' AND claimed_at<?) OR (state='retry_wait' AND not_before<?) OR (state='RAW_ARCHIVED_NO_TEXT' AND result_status IN ('waiting_cloud_configuration','waiting_cloud_budget','waiting_cloud_quota') AND not_before<?) OR (state='saved_original_needs_review' AND updated_at<?)"
    return _jobs(ctx, 'wechat.jobs_stuck', '微信附件采集卡住或逾期', where,
                 (ctx.now - 600, ctx.now - 3600, ctx.now - 3600, ctx.now - 3600), 'high',
                 '处理占用已超过十分钟，或重试、云解析等待、已存原文件待复核超过一小时；需检查任务与额度，不代表允许重跑。')


def check_callback_drop(ctx):
    with read_db(ctx.wechat_path) as db:
        recent, previous = db.execute('''SELECT
            COALESCE(SUM(CASE WHEN received_at>=? THEN 1 ELSE 0 END),0),
            COALESCE(SUM(CASE WHEN received_at<? THEN 1 ELSE 0 END),0)
            FROM received_callback_archive WHERE received_at>=? AND received_at<=?
            AND typeof(received_at) IN ('integer','real')''',
            (ctx.now - DAY, ctx.now - DAY, ctx.now - 8 * DAY, ctx.now)).fetchone()
    baseline = previous / 7
    dropped = baseline > 0 and recent < baseline * .3
    meaning = (f'近二十四小时收到 {recent} 条回调，前七天日均 {baseline:.1f} 条；明显下降可能是业务变少或回调链路中断。'
               if baseline else '前七天没有回调可作基线，暂不能判断下降；回调数也不等于消息数或完整覆盖。')
    return finding('wechat.callback_drop', '微信近二十四小时回调异常下降', 'high' if dropped else 'ok', int(dropped),
                   meaning, '系统后台 → 渠道接入 → 微信；核对接收服务、回调配置和正常业务量',
                   recent_24h=recent, previous_7d=previous, baseline_daily=baseline,
                   ratio=recent / baseline if baseline else None, threshold=.3)


def check_discovery_stale(ctx):
    # finish_poll() stores observed_unverified for accepted complete-shaped
    # responses. partial_source intentionally does not reset the success clock.
    with read_db(ctx.discovery_path) as db:
        last = db.execute("SELECT MAX(finished_at) FROM snapshot_runs WHERE state='observed_unverified' "
                          "AND typeof(finished_at) IN ('integer','real') AND finished_at>0 "
                          'AND finished_at>=started_at AND finished_at<=?', (ctx.now,)).fetchone()[0]
    stale = last is None or ctx.now - last > DAY
    return finding('wechat.discovery_stale', '微信联系人发现快照过旧', 'medium' if stale else 'ok', int(stale),
                   '最近一次成功接收的联系人列表已超过二十四小时或从未成功，目录可能漏掉新群；成功接收也不代表成员身份或全量完整性已验证。'
                   if stale else '二十四小时内成功接收过联系人列表；该状态只代表来源响应通过校验，不代表全部群或成员身份已验证。',
                   '系统后台 → 渠道接入 → 微信通讯录；检查联系人发现任务的最后结果及定时器，恢复只读发现后复查',
                   last_success_at=last, age_seconds=ctx.now - last if last is not None else None, threshold_seconds=DAY)


CHECKS = [('wechat.' + function.__name__.removeprefix('check_'), title, function) for function, title in (
    (check_not_enrolled, '微信未接入采集的群'),
    (check_silent_groups, '微信已接入但近七天无消息的群'),
    (check_role_conflicts, '微信群角色与命名规则冲突'),
    (check_unclassified_groups, '微信仍未分类的群'),
    (check_media_archive, '微信附件原文件归档缺口'),
    (check_jobs_raw_no_text, '微信原文件已存但没有可用正文'),
    (check_jobs_failed, '微信附件采集失败或中断'),
    (check_jobs_stuck, '微信附件采集卡住或逾期'),
    (check_callback_drop, '微信近二十四小时回调异常下降'),
    (check_discovery_stale, '微信联系人发现快照过旧'),
)]
