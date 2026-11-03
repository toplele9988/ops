#!/usr/bin/env python3
"""在临时库里走查虚构生意；不启动服务、不调用真实渠道或模型。"""
import argparse
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import importlib.util
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import ModuleType
from urllib.parse import unquote, urlsplit

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

MISSING_MEDIA = importlib.util.find_spec('console.media') is None
if MISSING_MEDIA:
    # Some public snapshots omit this package. The walk never serves media;
    # this import-only fake must fail if any code tries to sign/read a token.
    def unavailable_media(*args, **kwargs):
        raise RuntimeError('走查禁用媒体签名，不能读取或创建媒体密钥')

    media = ModuleType('console.media')
    media.__path__ = []
    tokens = ModuleType('console.media.tokens')
    tokens.KEY_PATH = None
    tokens.make = tokens.read = unavailable_media
    media.tokens = tokens
    sys.modules['console.media'] = media
    sys.modules['console.media.tokens'] = tokens

from console.archive.service import Archive
from console.archive.store import Store, configure
from console.archive.validation import BadRequest, Conflict
from console.channels.alibaba import AlibabaAdapter
from console.channels.common import conversation
from console.deals.service import Deals
from console.directory.service import naming
from console.extract.service import Extract
from console.inbox.ai import make_prompt, parse_output
from console.quotes.calc import compute
from console.quotes.service import Quotes, children
from console.quotes.validation import parameters
from console.fulfil.service import Fulfil
from console.roles.service import Roles

WHO = '虚构走查员'
COUNTRY = '虚构南美国'


class WalkFailure(Exception):
    pass


def require(condition, reason):
    if not condition:
        raise WalkFailure(reason)


def refused(call):
    try:
        call()
    except (BadRequest, Conflict):
        return True
    return False


class OfflineGuard:
    """拒绝联网、子进程、非临时 SQLite，以及读取生产业务文件。"""
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.active = False
        self.violations = 0

    def inside(self, path):
        return path == self.root or self.root in path.parents

    def deny(self):
        self.violations += 1
        raise WalkFailure('离线隔离拦住了外部访问；请检查走查依赖，不能改用真实服务')

    def audit(self, event, args):
        if not self.active:
            return
        if event.startswith('socket.') or event in ('subprocess.Popen', 'os.system', 'os.exec', 'os.posix_spawn'):
            self.deny()
        if event == 'sqlite3.connect':
            target = os.fsdecode(args[0])
            if target != ':memory:':
                path = Path(unquote(urlsplit(target).path) if target.startswith('file:') else target).resolve()
                if not self.inside(path):
                    self.deny()
        if event == 'open' and not isinstance(args[0], int):
            path = Path(os.fsdecode(args[0])).resolve()
            flags = args[2] or 0
            if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
                if not self.inside(path):
                    self.deny()
            elif not self.inside(path):
                # The repository and interpreter may load code and timezone data.
                # No config, credential, cache or business-data file is needed.
                code = path.suffix in ('.py', '.pyc', '.so')
                source = path == ROOT or ROOT in path.parents
                zone = Path('/usr/share/zoneinfo') in path.parents
                if not (code or source or zone):
                    self.deny()


@contextmanager
def isolated():
    with tempfile.TemporaryDirectory(prefix='fictional-workflow-', dir='/tmp') as directory:
        guard = OfflineGuard(directory)
        sys.addaudithook(guard.audit)
        guard.active = True
        try:
            yield Path(directory), guard
        finally:
            guard.active = False


@dataclass
class Finding:
    step: str
    availability: str
    reason: str
    evidence: str
    gaps: tuple = ()

    @property
    def result(self):
        return '通过' if self.availability == '已可用' else '卡住'


class FakeInbox:
    def __init__(self):
        self.conversations = {}

    def read(self, now=None):
        return dict(ready=True, conversations=self.conversations)

    def detail(self, key, now=None):
        return self.conversations.get(key)

    def add(self, channel, name, role, now):
        conv = conversation(channel, '虚构渠道', 'fictional-account', name, name,
                            [dict(role='buyer', text='虚构资料待核对', sent_at=now)],
                            role=role, is_group=channel == 'wechat')
        self.conversations[conv['key']] = conv
        return conv['key']

    def observe(self, key, role, text, now):
        # Only add an invented observation. There is no send method or transport.
        conv = self.conversations[key]
        messages = list(conv['messages']) + [dict(role=role, text=text, sent_at=now)]
        messages.sort(key=lambda message: message['sent_at'])
        conv.update(messages=messages, n_messages=len(messages), last_seen=messages[-1]['sent_at'])


class Walk:
    def __init__(self, root):
        self.root = root
        self.now = datetime(2030, 1, 1, 8, tzinfo=timezone.utc).timestamp()
        self.inbox = FakeInbox()
        self.db = sqlite3.connect(':memory:')
        configure(self.db)
        self.store = Store(root / 'fictional-archive.sqlite3', connection=self.db, target_version=9)
        self.store.initialize()
        fx = root / 'fictional-fx.json'
        fx.write_text('{"rate":"7.0000","source":"虚构固定汇率","fetched_at":1893484800}', encoding='utf8')
        self.archive = Archive(store=self.store, today=self.day)
        self.deals = Deals(store=self.store, inbox=self.inbox, clock=lambda: self.now)
        self.quotes = Quotes(store=self.store, today=self.day, clock=lambda: self.now, fx_path=fx)
        self.fulfil = Fulfil(store=self.store, deals=self.deals, today=self.day, clock=lambda: self.now)
        self.roles = Roles(store=self.store, inbox=self.inbox, runner=None, hub=object(),
                           clock=lambda: self.now, today=self.day)
        self.extract = Extract(store=self.store, inbox=self.inbox, runner=None, clock=lambda: self.now)
        self.findings = []

    def day(self):
        return datetime.fromtimestamp(self.now, timezone.utc).date()

    def record(self, step, availability, reason, evidence, *gaps):
        self.findings.append(Finding(step, availability, reason, evidence, gaps))

    def aw(self, op, row=None, **fields):
        return self.archive.write(op, row, fields, WHO)['id']

    def dw(self, op, row=None, **fields):
        return self.deals.write(op, row, fields, WHO)['id']

    def qw(self, op, row, child=None, **fields):
        return self.quotes.write(op, row, fields, WHO, child)

    def fw(self, op, row, child=None, **fields):
        return self.fulfil.write(op, row, fields, WHO, child)

    def detail(self):
        return self.deals.read('detail', row_id=self.did)

    def quote(self, rid):
        return self.quotes.read('detail', row_id=rid)

    def accept_price(self, key, **fields):
        scanned = self.extract.scan(WHO, conversation_key=key)
        require(len(scanned['items']) == 1, '虚构报价没有产生唯一待确认建议，请检查抽取规则')
        suggestion = scanned['items'][0]
        require(suggestion['status'] == 'pending', '报价扫描提前确认了价格')
        result = self.extract.write('accept', suggestion['id'], fields, WHO)
        again = self.extract.write('accept', suggestion['id'], fields, WHO)
        require(again['reused'] and again['record_id'] == result['record_id'], '重复接受报价没有复用原记录')
        return result

    def inquiry(self):
        payload = dict(account='fictional-account', conversation_id='fictional-led-inquiry',
                       buyer_name='CUS-9001 虚构南美舞台买家', country=COUNTRY,
                       content='虚构询盘：LED 摇头灯 WX-300，24 台，含配套线缆，海运到虚构目的港。',
                       role='buyer', message_id='fictional-inquiry-1',
                       sent_at=datetime.fromtimestamp(self.now, timezone.utc).isoformat())
        path = self.root / 'fictional-inbound.sqlite3'
        with sqlite3.connect(path) as db:
            # Match the existing Alibaba reader's id/payload contract, not a receiver migration.
            db.execute('CREATE TABLE inbound(id INTEGER PRIMARY KEY,payload TEXT)')
            db.executemany('INSERT INTO inbound(payload) VALUES(?)', [(json.dumps(payload),)] * 2)
        adapter = AlibabaAdapter(path)
        convs = adapter.list_conversations(self.now)
        require(adapter.ready and len(convs) == 1, '阿里本地投影没有读到唯一虚构询盘')
        self.customer_key, conv = next(iter(convs.items()))
        require(conv['n_messages'] == 1, '相同平台消息编号没有去重')
        self.inbox.conversations.update(deepcopy(convs))
        self.record('询盘进入', '部分可用', '本地阿里投影及重复消息去重通过；未验证 Win10 持续采集和附件，需 P2b/W1 接续。',
                    'src/console/channels/alibaba.py; docs/data-pipelines.md', 'WG-01')
        suggestions = self.roles.scan_triage(WHO)['items']
        suggestion = next(s for s in suggestions if s['conversation_key'] == self.customer_key)
        result = self.roles.write('triage_accept', suggestion['id'], {}, WHO)
        repeated = self.roles.write('triage_accept', suggestion['id'], {}, WHO)
        self.did, self.customer = result['deal_id'], result['party_id']
        require(self.did == repeated['deal_id'] and repeated['reused'], '重复接受分拣重复建案')
        require(self.detail()['customer']['id'] == self.customer and self.detail()['status'] == 'open', '建案没有正确关联客户')
        self.aw('party_update', self.customer, country=COUNTRY)
        self.record('建案并关联客户', '已可用', '人确认分拣后创建询盘案子，重复确认复用同一案子；目的国由演示人工补录。',
                    'POST /api/v1/triage/{id}/accept')

    def sourcing(self):
        self.suppliers, self.products, self.supplier_keys = [], [], []
        for index, channel in enumerate(('wechat', 'whatsapp'), 1):
            supplier = self.aw('party_create', kind='supplier', name=f'虚构灯厂{index}')
            product = self.aw('product_create', supplier_id=str(supplier), name='虚构 LED 摇头灯 WX-300',
                              model='WX-300', units_per_carton='1', carton_l_cm='50', carton_w_cm='40',
                              carton_h_cm='30', gross_kg='20')
            self.aw('product_confirm', product)
            key = self.inbox.add(channel, f'SUP-90{index} 虚构灯厂{index}', 'supplier', self.now)
            self.dw('conversation', self.did, conversation_key=key, party_id=str(supplier))
            self.suppliers.append(supplier)
            self.products.append(product)
            self.supplier_keys.append(key)
        self.dw('item', self.did, name='虚构 LED 摇头灯 WX-300', product_id=str(self.products[0]), qty='24')
        self.dw('update', self.did, stage='sourcing')
        self.record('提取产品需求', '部分可用', '演示人工建产品、补包装、将 24 台写入产品清单；现有报价抽取员只抽供应方价格，不抽客户需求。',
                    'POST /api/v1/deals/{id}/items; console.extract.service.Extract._party', 'WG-06')
        prompt = make_prompt(self.inbox.detail(self.supplier_keys[0])['messages'], 'wechat', 'supplier', knowledge='虚构公开资料')
        require('采购/核对资料' in prompt, '供应商起草没有使用采购口吻')
        draft = parse_output('语言：中文\n请核对虚构型号、价格、包装和交期。\n=====待确认=====\n请人工补入本案数量')
        require(draft['needs_confirmation'], '假草稿没有保留人工确认项')
        for key in self.supplier_keys:
            self.inbox.observe(key, 'seller', '虚构人工询价记录：请报 WX-300，24 台，确认外箱、毛重、交期和有效期。', self.now + 1)
        self.now += 49 * 3600
        state = self.detail()
        require('supplier' in state['waiting_on'] and 'supplier' in state['overdue'], '供应商等待或 48 小时超时没有显示')
        secretary = self.roles.run_secretary(self.did, WHO)['output_json']
        require(any(w['waited_hours'] == 48 or w['waited_hours'] == 49 for w in secretary['waiting_on'] if w['state'] == 'them'),
                '秘书没有给出供应商等待时长')
        self.roles.run_patrol(WHO)
        require(self.roles.alerts(self.did), '巡检没有生成长时间等待提醒')
        self.record('向产品供应商问价并查看等待', '部分可用', '采购口吻、两家供应商等待和巡检提醒通过；本案需求由演示人工抄入假会话，案内没有直接催价草稿入口。',
                    'console.inbox.ai.make_prompt; GET /api/v1/deals/{id}/secretary', 'WG-04')
        saved = deepcopy(self.inbox.detail(self.supplier_keys[0]))
        self.inbox.observe(self.supplier_keys[0], 'unknown', '虚构未知方向占位', self.now)
        participant = next(p for p in self.detail()['participants'] if p['party_id'] == self.suppliers[0])
        self.record('等待计时反例', '部分可用' if participant['state'] == 'none' else '已可用',
                    '最后一条方向未知时，案子丢失此前等待；秘书使用另一套方向规则。' if participant['state'] == 'none' else '未知方向没有覆盖已有等待状态。',
                    'console.deals.waiting.evaluate; console.roles.common.observation', *(['WG-04'] if participant['state'] == 'none' else []))
        self.inbox.conversations[self.supplier_keys[0]] = saved
        for index, key in enumerate(self.supplier_keys):
            price = ('1357.00', '1412.00')[index]
            self.inbox.observe(key, 'buyer', f'虚构 LED 摇头灯 WX-300 报价 {price} 元/台，交期 10-15 天，有效至 2030-02-01', self.now)
            self.accept_price(key, product_id=str(self.products[index]), valid_until='2030-02-01',
                              currency='CNY', unit='台', lead_days_min='10', lead_days_max='15')
        self.record('收到供应商报价并抽取', '已可用', '两条虚构文字报价经规则抽取、人工确认有效期后入档；重复确认没有重复报价，未调用真实模型。',
                    'POST /api/v1/extract/suggestions/{id}/accept')
        require(naming('SUP-901 虚构灯厂')[0] == 'supplier' and naming('CUS-9001 虚构买家')[0] == 'customer', 'SUP/CUS 分类没有识别')
        self.record('供应商比价与 SUP/CUS 关联', '部分可用', '两家供应商分别有产品和报价，案子有单位及会话关联；仍需人工对照，未提供同一需求的供应商并排表或独立编号投影。',
                    'console.quotes.service.Quotes._offer; console.roles.common.party_name_parts', 'WG-05')

    def freight_and_vendor(self):
        self.freights = []
        for index, channel in enumerate(('forwarder', 'platform_alibaba'), 1):
            party = self.aw('party_create', kind='forwarder', name=f'虚构海运货代{index}')
            key = self.inbox.add('wechat', f'SUP-92{index} 虚构海运货代', 'supplier', self.now)
            self.dw('conversation', self.did, conversation_key=key, party_id=str(party))
            self.inbox.observe(key, 'seller', '虚构人工询运费记录：24 箱，外箱 50×40×30 cm，每箱毛重 20 kg，询国内拖车及海运。', self.now)
            require('forwarder' in self.detail()['waiting_on'], '案子没有显示正在等货代')
            self.inbox.observe(key, 'buyer', f'海运报价 CNY {600 if index == 1 else 680}/cbm，有效至 2030-02-01', self.now + 1)
            self.now += 1
            result = self.accept_price(key, forwarder_id=str(party), mode='sea', unit='cbm',
                                       currency='CNY', origin='虚构起运港', destination_country=COUNTRY,
                                       service='虚构港到港海运', channel=channel, valid_until='2030-02-01',
                                       min_charge='900' if index == 1 else '950',
                                       transit_days_min='32' if index == 1 else '25',
                                       transit_days_max='38' if index == 1 else '30')
            self.freights.append(result['record_id'])
        self.record('向货代和平台物流询运费', '部分可用', '两条虚构海运价可抽取确认，来源区分外部货代和阿里物流；包装、线路、时效及国内拖车费仍由演示人工补录。',
                    'POST /api/v1/archive/freight; POST /api/v1/extract/suggestions/{id}/accept', 'WG-04', 'WG-07')
        self.rid = self.qw('create', self.did)['id']
        self.qw('update', self.rid, incoterm='CIF', destination_country=COUNTRY, fx_rate='7.0000', valid_until='2030-01-20')
        options = self.quotes.read('freight_options', row_id=self.rid)['items']
        require(len(options) == 2 and all(o['calculable'] for o in options), '两家海运价不能按整票计算')
        require({'forwarder', 'platform_alibaba'} == {o['channel'] for o in options}, '运价来源没有区分')
        require(any('cheapest' in o['recommendations'] for o in options) and any('fastest' in o['recommendations'] for o in options), '比价缺少最低价或最快提示')
        chosen = self.qw('freight_choose', self.rid, confirm='1', freight_quote_id=str(self.freights[0]), reason_type='cheapest')
        repeated = self.qw('freight_choose', self.rid, confirm='1', freight_quote_id=str(self.freights[0]), reason_type='cheapest')
        require(repeated['reused'] and chosen['id'] == repeated['id'], '重复选同一运价增加了费用')
        self.record('运费并排比价', '已可用', '现有 I2 对两家整票运价展示价格、时效、有效期及来源，并记录人工选择；重复选同一条幂等。仅验证本地候选。',
                    'GET /api/v1/quotes/{id}/freight-options; POST /api/v1/quotes/{id}/freight-options/choose')
        other = self.qw('freight_choose', self.rid, confirm='1', freight_quote_id=str(self.freights[1]), reason_type='fastest')
        international = [c for c in self.quote(self.rid)['costs'] if c['kind'] == 'freight']
        multiple = len(international) > 1
        self.record('改选另一家货代', '部分可用' if multiple else '已可用',
                    '改选后两家海运费同时计入成本；演示用现有移除接口删掉第二条后继续。' if multiple else '改选没有重复计入两家海运费。',
                    'console.quotes.service.Quotes._write(freight_choose)', *(['WG-07'] if multiple else []))
        if multiple:
            self.qw('cost_remove', self.rid, other['id'])
        self.vendor = self.aw('party_create', kind='vendor', name='虚构线缆配套商')
        key = self.inbox.add('wechat', 'SUP-930 虚构线缆配套商', 'supplier', self.now)
        self.dw('conversation', self.did, conversation_key=key, party_id=str(self.vendor))
        self.inbox.observe(key, 'buyer', '虚构配套线缆报价 25 元/套，有效至 2030-02-01', self.now)
        self.accept_price(key, create_product='1', product_name='虚构配套线缆', valid_until='2030-02-01', currency='CNY', unit='套')
        self.qw('cost', self.rid, kind='vendor', manual='1', party_id=str(self.vendor), amount='600',
                valid_until='2030-02-01', source_note='虚构配套报价，人工确认 24 套，每套 25 元')
        self.qw('cost', self.rid, kind='domestic', manual='1', amount='480', valid_until='2030-02-01',
                source_note='虚构国内拖车总价，人工确认')
        self.record('配套供应商与国内拖车费用', '部分可用', '线缆价格可抽取，但转换为 24 套费用仍由演示人工填写；接口可关联单位，当前费用表单未传单位。',
                    'POST /api/v1/quotes/{id}/costs; web/src/pages/QuotePage.tsx', 'WG-08')

    def quoting_and_payment(self):
        line = self.quote(self.rid)['lines'][0]['id']
        self.qw('line_update', self.rid, line, sell_unit_price='260')
        detail = self.quote(self.rid)
        require(not detail['blocking'] and detail['totals']['cost_cny'] == '34548.00', '虚构整单成本或发出检查不符')
        require(detail['totals']['profit_cny'] == '9132.00', '虚构首轮利润计算不符')
        require(refused(lambda: self.qw('status', self.rid, action='send')), '未确认就能标记报价发出')
        self.qw('line_update', self.rid, line, sell_unit_price='1')
        require(self.quote(self.rid)['inverted'] and refused(lambda: self.qw('status', self.rid, action='send', confirm='1')), '倒挂报价没有阻断')
        self.qw('line_update', self.rid, line, sell_unit_price='260')
        self.qw('update', self.rid, valid_until='')
        require(refused(lambda: self.qw('status', self.rid, action='send', confirm='1')), '没写有效期仍可发出')
        self.qw('update', self.rid, valid_until='2030-01-20')
        with self.store.read() as db:
            raw = dict(db.execute('SELECT * FROM quote_rounds WHERE id=?', (self.rid,)).fetchone())
            lines, costs = children(db, 'quote_lines', self.rid), children(db, 'quote_costs', self.rid)
        expired = compute(raw, lines, costs, self.day().replace(month=3))
        require({'offer_expired', 'cost_expired'} <= {b['code'] for b in expired['blocking']}, '过期产品成本或费用没有阻断')
        self.record('生成报价与核价', '已可用', '产品成本、海运、拖车、线缆、汇率与利润通过；倒挂、过期及缺有效期会阻断，标记发出须人工确认。',
                    'console.quotes.calc.compute; POST /api/v1/quotes/{id}/status')
        extended = compute(dict(raw, valid_until='2030-02-20'), lines, costs, self.day())
        self.record('客户报价期限覆盖检查', '部分可用' if not extended['blocking'] else '已可用',
                    '客户报价延长到 2 月 20 日，成本只保到 2 月 1 日，现有检查仍允许就绪；需人工缩短期限或重新锁价。' if not extended['blocking'] else '客户报价超出成本承诺期时已阻断。',
                    'console.quotes.calc.compute', *(['WG-07'] if not extended['blocking'] else []))
        self.qw('update', self.rid, destination_country='虚构另一目的国')
        require(not self.quotes.read('freight_options', row_id=self.rid)['items'], '比价没有按目的国过滤')
        mismatched = self.qw('cost', self.rid, kind='freight', freight_quote_id=str(self.freights[0]))['id']
        accepted_mismatch = not self.quote(self.rid)['blocking']
        self.record('绕过比价直接选运价', '部分可用' if accepted_mismatch else '已可用',
                    '比价会过滤目的国，但旧添加费用入口仍接受另一目的国运价且不阻断；演示移除该费用并恢复目的国后继续。' if accepted_mismatch else '直接添加运价也核对了目的国。',
                    'console.quotes.service.Quotes._write(cost); POST /api/v1/quotes/{id}/costs', *(['WG-07'] if accepted_mismatch else []))
        self.qw('cost_remove', self.rid, mismatched)
        self.qw('update', self.rid, destination_country=COUNTRY)
        language_missing = refused(lambda: parameters({'lang': 'es'}, 'text'))
        self.record('客户母语报价', '缺失' if language_missing else '部分可用',
                    '报价入口只接受中文/英文；虚构买家设定为西语，需 P1 提供母语、回译和人工锁定，再接报价文本。' if language_missing else '语言参数已扩展，仍需核对报价正文、回译及未知母语阻断。',
                    'console.quotes.validation.parameters; console.quotes.text.render', 'WG-03')
        text = self.quotes.read('text', {'lang': 'zh'}, self.rid)
        require(text['ready'] and all(marker not in text['text'] for marker in ('虚构灯厂', '虚构海运货代', '1357.00', '34548.00')), '客户报价包含内部成本或供应方信息')
        self.qw('status', self.rid, action='send', confirm='1')
        self.inbox.observe(self.customer_key, 'seller', text['text'], self.now)
        require('customer' in self.detail()['waiting_on'], '报价后案子没有显示正在等客户')
        self.record('报价人工确认并发给客户', '部分可用', '客户文本不含内部成本；这里只记录虚构已发出及假会话观察，没有发送。现有报价状态与渠道回执没有绑定。',
                    'GET /api/v1/quotes/{id}/text; POST /api/v1/quotes/{id}/status', 'WG-09')
        self.qw('negotiation', self.rid, side='customer', party_id=str(self.customer), price='250', currency='USD', note='虚构客户还价一次，希望单价降至 250 美元。')
        self.dw('update', self.did, stage='negotiating')
        self.record('记录客户还价', '已可用', '客户还价、单位和币种留在首轮谈判记录，未覆盖原售价。', 'POST /api/v1/quotes/{id}/negotiations')
        self.second = self.qw('create', self.did)['id']
        self.qw('update', self.second, valid_until='2030-01-20')
        line = self.quote(self.second)['lines'][0]['id']
        self.qw('line_update', self.second, line, sell_unit_price='250')
        self.qw('status', self.second, action='send', confirm='1')
        self.qw('status', self.second, action='accept')
        require(self.quote(self.rid)['status'] == 'superseded' and self.quote(self.rid)['lines'][0]['sell_unit_price'] == '260.00', '新轮覆盖了旧轮报价')
        require(self.detail()['stage'] == 'awaiting_payment' and self.detail()['status'] == 'open', '客户接受报价被误算成交')
        self.record('第二轮报价与客户接受', '已可用', '复制历史快照后重填有效期并降价；客户接受只进入待付款，尚未成交。虚构整单销售额 6000 美元。',
                    'POST /api/v1/deals/{id}/quotes; POST /api/v1/quotes/{id}/status')
        terms = self.quotes.read('text', {'lang': 'zh'}, self.second)
        placeholder = '【待确认：付款条件】' in terms['text']
        self.record('30% 定金条款与 PI', '缺失' if placeholder else '部分可用',
                    '算价已就绪但付款条件仍是占位；新报价轮没有关联 PI，30% 条件及单据需人工准备。' if placeholder else '付款条件已有文本，PI 与该报价轮的关联仍需人工复核。',
                    'console.quotes.text.render; console.quotes.validation.FIELDS', 'WG-09')
        self.payment = self.fw('payment_create', self.did, kind='deposit', amount_fen='180000', currency='USD',
                               received_on=self.day().isoformat(), method='wire', quote_round_id=str(self.second))['id']
        require(self.detail()['status'] == 'open', '仅登记待核对定金就标记了成交')
        require(refused(lambda: self.fw('payment_confirm', self.payment)), '缺少确认标志仍确认了收款')
        self.fw('payment_confirm', self.payment, confirm='1', qualifies='1')
        changes = self.db.total_changes
        self.fw('payment_confirm', self.payment, confirm='1', qualifies='1')
        require(self.db.total_changes == changes and self.detail()['status'] == 'won', '重复确认收款不幂等或没有成交')
        summary = self.fulfil.read('payments', row_id=self.did)['summary'][0]
        require(summary['received_fen'] == 180000 and summary['unreceived_fen'] == 420000, '定金或剩余应收金额不符')
        brief = self.roles.run_brief(WHO)['output_json']
        require(brief['results']['today']['count'] == 1 and brief['results']['today']['amounts']['USD'] == '1800.00', '简报没有以确认定金统计本日成交')
        self.record('收 30% 定金并确认成交', '已可用', '虚构到账 1800 美元，人工确认后成交；还差 4200 美元，重复确认不重复入账。未核验银行或支付平台。',
                    'POST /api/v1/deals/{id}/payments; POST /api/v1/payments/{id}/confirm')
        self.record('老板查看成交简报', '部分可用', '本日成交及已收净额通过规则汇总；不是现金流或订单总额。简报、巡检需主动运行，已有结果不会自行变新。',
                    'POST /api/v1/brief/run; web/src/ui/RolePanels.tsx', 'WG-11')

    def delivery(self):
        self.fw('node_update', self.did, 'po', status='done', responsible='supplier', party_id=str(self.suppliers[0]))
        self.fw('node_update', self.did, 'supplier_confirmation', status='done', promised_on='2030-01-15')
        self.fw('node_update', self.did, 'production', status='doing')
        self.record('下单与备货', '部分可用', '可人工记录下单、供应商和承诺交期；不生成采购订单、不实际通知供应商。付款后阶段模板没有补入待办。',
                    'POST /api/v1/deals/{id}/fulfilment/{node}/update; console.deals.rules.pending_templates', 'WG-10')
        self.now += 10 * 86400
        self.fw('node_update', self.did, 'production', status='done')
        self.fw('node_update', self.did, 'inspection', status='done', inspection_result='pass', inspector='虚构验货员')
        progress = self.fulfil.read('fulfilment', row_id=self.did)
        require(any(s['stage'] == 'shipped' and s['reason'] for s in progress['suggestions']), '验货通过没有给出阶段建议')
        require(self.detail()['stage'] == 'paid', '记录履约节点自动改了阶段')
        page = (ROOT / 'web/src/pages/DealPage.tsx').read_text(encoding='utf8')
        panel = (ROOT / 'web/src/ui/DealFulfil.tsx').read_text(encoding='utf8')
        broken = 'd.status === "open" && next' in page or ('s.text ?? s.message' in panel and 's.reason' not in panel)
        self.record('验货与付款后阶段推进', '部分可用',
                    '节点建议通过；静态发现已成交案子无下一阶段按钮或建议原因字段不匹配。后续由演示显式调用阶段接口绕行。' if broken else '旧界面断点模式已消失；后续仍以内部阶段接口演示，界面需浏览器另验。',
                    'console.fulfil.nodes.progress; web/src/pages/DealPage.tsx; web/src/ui/DealFulfil.tsx', 'WG-10')
        self.dw('update', self.did, stage='inspection')
        self.fw('node_update', self.did, 'booking', status='done', carrier='虚构船司', tracking_number='FICTIONAL-BL-001',
                etd=self.day().isoformat(), departed_on=self.day().isoformat(), eta='2030-02-15')
        self.fw('node_update', self.did, 'transit', status='doing')
        self.dw('update', self.did, stage='shipped')
        self.record('发货与在途', '部分可用', '虚构提单号、离港日和 ETA 可记录；通知、提单/发票/装箱单及物流查询仍需人工，阶段由内部接口绕行。',
                    'console.fulfil.nodes.write; web/src/ui/DealFulfil.tsx', 'WG-10')
        self.now += 32 * 86400
        for node in ('transit', 'customs', 'signoff'):
            self.fw('node_update', self.did, node, status='done')
        self.dw('update', self.did, stage='delivered')
        require(self.fulfil.read('fulfilment', row_id=self.did)['done'] == 8, '八个履约节点没有全部完成')
        self.record('客户签收', '部分可用', '八个节点全部人工记录完成，签收日期可用于返单；阶段仍由内部接口绕行，未核验真实物流签收。',
                    'POST /api/v1/deals/{id}/fulfilment/signoff/update', 'WG-10')
        self.dw('update', self.did, stage='aftersales')
        issue = self.fw('issue_create', self.did, kind='feedback', product_id=str(self.products[0]),
                        responsible='supplier', party_id=str(self.suppliers[0]), description='虚构意见：希望下批线缆增加颜色标记。')['id']
        changes = self.db.total_changes
        draft = self.fulfil.read('issue_draft', {'target': 'supplier'}, issue)
        require(draft['copy_only'] and draft['needs_manual_details'] and self.db.total_changes == changes, '售后草稿不再是纯读或缺人工补充标记')
        self.fw('issue_update', issue, status='waiting_party')
        self.fw('issue_note', issue, note='虚构内部记录：供应商同意下批核对颜色标记。')
        self.fw('issue_update', issue, status='resolved', resolution='虚构结论：下批打样确认。')
        self.fw('issue_update', issue, status='closed')
        require(self.fulfil.read('issue_summary')['unresolved'] == 0, '已关闭问题仍计入未解决')
        self.record('售后与反馈转供应商', '部分可用', '问题、处理记录、只读草稿和汇总接口通过；界面没有选择具体供应商/产品及汇总页，草稿需人工补事实并到渠道发送。',
                    'POST /api/v1/deals/{id}/issues; GET /api/v1/issues/summary', 'WG-12')
        self.now += 31 * 86400
        leads = self.fulfil.read('reorder_leads')['items']
        require(len(leads) == 1 and leads[0]['deal_id'] == self.did, '签收满 30 天没有产生返单线索')
        copied = self.fw('reorder', self.did)['id']
        new = self.deals.read('detail', row_id=copied)
        require(new['stage'] == 'inquiry' and new['status'] == 'open' and new['customer']['id'] == self.customer,
                '返单草稿未保持客户和询盘状态')
        require(all(i['target_price'] is None for i in new['items']) and not self.quotes.read('list', row_id=copied)['items'],
                '返单草稿错误复制了历史价格或报价轮')
        require(not self.fulfil.read('reorder_leads')['items'], '已建返单草稿仍重复提示旧线索')
        self.record('返单', '部分可用', '线索及人工复制接口通过，价格不会沿用；当前页面只有回旧案子的链接，没有复制按钮或上次报价、反馈、供应商对照。',
                    'GET /api/v1/reorder/leads; POST /api/v1/deals/{id}/reorder; web/src/pages/FulfilBoard.tsx', 'WG-12')

    def payment_counterexamples(self):
        # Separate fictional deals keep counterexamples out of the main customer history.
        did = self.dw('create', title='虚构阶段旁路检查')
        blocked = refused(lambda: self.dw('update', did, stage='paid'))
        unpaid_won = not blocked and self.deals.read('detail', row_id=did)['status'] == 'won' and not self.fulfil.read('payments', row_id=did)['items']
        self.record('成交旁路检查', '缺失' if unpaid_won else '已可用',
                    '没有任何收款记录，直接改到备货仍被标记为已成交；需要封住建案和阶段更新入口。' if unpaid_won else '阶段更新没有绕过付款确认。',
                    'console.deals.rules.transition; POST /api/v1/deals/{id}/update', *(['WG-02'] if unpaid_won else []))
        did = self.dw('create', title='虚构取消成交勾选检查')
        pid = self.fw('payment_create', did, kind='deposit', amount_fen='1', currency='USD',
                      received_on=self.day().isoformat(), method='other')['id']
        self.fw('payment_confirm', pid, confirm='1', qualifies='0')
        won = self.deals.read('detail', row_id=did)['status'] == 'won'
        self.record('取消达到成交条件的勾选', '部分可用' if won else '已可用',
                    '首次定金即使明确提交不计成交仍被标记成交；页面勾选文案与后端定金规则不一致。' if won else '取消成交勾选没有推进成交。',
                    'console.fulfil.payments.confirm; web/src/ui/DealFulfil.tsx', *(['WG-02'] if won else []))

    def run(self):
        try:
            if MISSING_MEDIA:
                self.record('快照依赖检查', '部分可用', '快照缺少 console.media；仅用禁止调用的导入替身，本走查不验证附件和媒体签名。',
                            'src/console/channels/wechat.py:7', 'WG-13')
            self.inquiry()
            self.sourcing()
            self.freight_and_vendor()
            self.quoting_and_payment()
            self.delivery()
            self.payment_counterexamples()
            require(self.db.execute('PRAGMA foreign_key_check').fetchone() is None, '临时库外键校验未通过')
            return self.findings
        finally:
            self.db.close()


def run_walk():
    with isolated() as (root, guard):
        walk = Walk(root)
        try:
            findings = walk.run()
            require(guard.violations == 0, '走查依赖曾尝试访问外部资源，结果不能当作通过')
            return dict(findings=[dict(asdict(f), result=f.result) for f in findings], errors=0)
        except Exception as error:
            reason = str(error) if isinstance(error, WalkFailure) else '内部调用与当前源码不符，请检查走查脚本和对应模块'
            return dict(findings=[dict(asdict(f), result=f.result) for f in walk.findings], errors=1,
                        error=reason, error_type=type(error).__name__)


def main(argv=None):
    parser = argparse.ArgumentParser(description='只在自动创建的临时库中走查虚构生意；无真实渠道、模型或收款')
    parser.add_argument('--json', action='store_true', help='输出结构化走查结果')
    parser.add_argument('--strict', action='store_true', help='仍有流程缺口时返回退出码 2；内部执行错误返回 1')
    args = parser.parse_args(argv)
    result = run_walk()
    result['blocked'] = sum(f['result'] == '卡住' for f in result['findings'])
    result['passed'] = sum(f['result'] == '通过' for f in result['findings'])
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print('全程虚构数据：临时 SQLite、假会话、规则模式。没有联网、发送、部署或核验真实到账。')
        for index, finding in enumerate(result['findings'], 1):
            print(f"{index:02d} {finding['result']}｜{finding['availability']}｜{finding['step']}：{finding['reason']}")
            print('   依据：' + finding['evidence'])
        if result['errors']:
            print('卡住｜执行错误：' + result['error'] + '（' + result['error_type'] + '）')
        print(f"通过 {result['passed']} 项，卡住 {result['blocked']} 项，执行错误 {result['errors']} 项；临时库已清理。")
        print('已用内部函数或人工补录绕行的步骤仍算缺口；通过不代表真实数据、界面或渠道可用。')
    return 1 if result['errors'] else 2 if args.strict and result['blocked'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
