"""Rules-only scans, with independent failures and an explicit persisted run."""
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
import time
from zoneinfo import ZoneInfo

from console.archive.store import ARCHIVE_PATH, Store
from console.archive.validation import BadRequest, Missing, whitelist
from console.channel_plan import CHANNEL_PLAN_PATH, public_plan, resolve_plan
from console.directory.sources import CONFIG_PATH, DISCOVERY_PATH, STATE_PATH
from console.inbox.store import INBOUND_PATH
from console.channels.probe import PROBE_PATH
from console.channels.whatsapp import CONFIG_PATH as WA_CONFIG_PATH
from console.roles.common import fingerprint, stamp
from console.roles.storage import save_run, serialized
from .common import LEVELS, failure_reason, finding, query_budget

WHATSAPP_PATH = Path('/var/lib/imgw/whatsapp/messages.sqlite3')
CHANNELS = ('wechat', 'alibaba', 'whatsapp', 'archive')


def default_checks():
    from . import wechat, alibaba, whatsapp, archive
    return {name: module.CHECKS for name, module in
            zip(CHANNELS, (wechat, alibaba, whatsapp, archive))}


class Audit:
    def __init__(self, archive_path=ARCHIVE_PATH, *, inbound_path=None, probe_path=None,
                 wechat_path=None, discovery_path=None, wechat_config_path=None,
                 whatsapp_path=None, whatsapp_config_path=None, directory=None,
                 session_status=None, clock=None, today=None, checks=None,
                 plan=None, plan_path=None):
        self.archive_path = Path(archive_path)
        self.store = Store(self.archive_path, target_version=6)
        self.clock = clock or time.time
        self.today = today
        self.directory, self.session_status = directory, session_status
        # Supplying any source isolates unspecified sources from production.
        isolated = self.archive_path != Path(ARCHIVE_PATH) or any(p is not None for p in (
            inbound_path, probe_path, wechat_path, discovery_path, wechat_config_path,
            whatsapp_path, whatsapp_config_path))
        root = self.archive_path.parent
        self.paths = {}
        for name, value, default in (
            ('inbound_path', inbound_path, INBOUND_PATH), ('probe_path', probe_path, PROBE_PATH),
            ('wechat_path', wechat_path, STATE_PATH), ('discovery_path', discovery_path, DISCOVERY_PATH),
            ('wechat_config_path', wechat_config_path, CONFIG_PATH),
            ('whatsapp_path', whatsapp_path, WHATSAPP_PATH),
            ('whatsapp_config_path', whatsapp_config_path, WA_CONFIG_PATH)):
            self.paths[name] = Path(value) if value is not None else root / ('disabled-' + name) if isolated else Path(default)
        for source in self.paths.values():
            if (source.resolve() == self.archive_path.resolve() or
                    source.exists() and self.archive_path.exists() and source.samefile(self.archive_path)):
                raise BadRequest('体检运行记录库必须独立于渠道来源')
        self.checks = checks if checks is not None else default_checks()
        self.plan = plan
        self.plan_path = (Path(plan_path) if plan_path is not None else
                          root / 'disabled-channel-operation-plan.json' if isolated else CHANNEL_PLAN_PATH)

    def scan(self, channel=None):
        if channel is not None and channel not in CHANNELS:
            raise Missing('未找到该体检渠道')
        now = self.clock()
        plan = resolve_plan(self.plan, self.plan_path)
        day = self.today() if callable(self.today) else self.today
        day = str(day or datetime.fromtimestamp(now, ZoneInfo('Asia/Shanghai')).date())
        ctx = SimpleNamespace(now=now, today=day, archive_path=self.archive_path,
            directory=self.directory, session_status=self.session_status, memo={}, plan=plan, **self.paths)
        selected = (channel,) if channel else CHANNELS
        # The directory snapshot serves four checks; Alibaba's single SQL
        # aggregation serves five. Give that work its share up front instead of
        # timing it as an ordinary scalar count. Unused prior time may carry
        # forward; later checks always retain their own allowance.
        weights = {'wechat.not_enrolled': 25, 'alibaba.accounts': 82.5}
        total_weight = sum(weights.get(key, 1) for name in selected for key, _, _ in self.checks.get(name, ()))
        items, skipped = [], []
        remaining, carry = 2.65, 0
        for name in selected:
            for key, title, check in self.checks.get(name, ()):
                budget = 2.65 * weights.get(key, 1) / max(1, total_weight)
                started = time.monotonic()
                try:
                    with query_budget(min(remaining, budget + carry)):
                        item = check(ctx)
                    if item is None:
                        skipped.append(key)
                        continue
                    if item['key'] != key or item['level'] not in LEVELS:
                        raise ValueError('invalid check result')
                except Exception as error:
                    reason = failure_reason(error)
                    item = finding(key, title, 'medium', 0, '读取失败：' + reason,
                        '系统后台 → 渠道接入或数据源维护：核对本项来源的权限、结构和运行状态后重新体检。',
                        read_failed=True, error=reason)
                finally:
                    elapsed = time.monotonic() - started
                    remaining = max(.001, remaining - elapsed)
                    carry = max(0, carry + budget - elapsed)
                item_plan = (public_plan(plan)['alibaba_accounts'] if name == 'alibaba' else
                             plan[name] if name in ('wechat', 'whatsapp') else None)
                items.append(dict(item, channel=name, plan=item_plan))
        items.sort(key=lambda item: (LEVELS[item['level']], item['channel'], item['key']))
        counts = {level: sum(item['level'] == level for item in items) for level in LEVELS}
        attention = sum(counts[level] for level in ('high', 'medium'))
        planned = counts['low']
        summary = f'{attention} 个需要处理' + (f'；计划中 {planned} 项' if planned else '')
        return dict(items=items, generated_at=stamp(now), source='rules', level='L0', plan=public_plan(plan),
            summary=summary, attention_count=attention, planned_count=planned,
            counts=counts, total=len(items), skipped=skipped,
            skipped_reasons={key: '本地未安装或未发现价格抽取表' if key == 'archive.pending_prices'
                             else '此可选来源未安装' for key in skipped},
            failed_count=sum(bool(item.get('read_failed')) for item in items))

    def read(self, channel=None, params=None):
        whitelist(params or {}, ())
        return self.scan(channel)

    @serialized
    def run(self, fields, who):
        whitelist(fields, ())
        output = self.scan()
        now = self.clock()
        with self.store.write() as db:
            run = save_run(db, 'audit', 'day', output['generated_at'][:10], fingerprint(output),
                output, output['summary'], [], who, now, source='rules',
                status='fallback' if output['failed_count'] else 'ok')
        return dict(output, run=run)
