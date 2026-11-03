"""Stable, ordered workflow vocabulary and task templates (pure data/functions)."""
STAGES = (
    ('inquiry', '询盘', ('弄清要什么产品、数量、目的国、价格预期',)),
    ('sourcing', '询价核实', ('向供应商确认型号、尺寸、重量、价格、交期', '向货代要运价', '有特殊要求时联系配套供应商')),
    ('quoting', '报价', ('核对成本报价有效期', '算到手价并检查倒挂', '发出报价')),
    ('negotiating', '谈判', ('记录客户还价', '回供应商和货代协商', '更新报价')),
    ('awaiting_payment', '待付款', ('发 PI', '确认付款条件', '确认收款')),
    ('paid', '已成交·备货', ('下单给供应商', '确认交期', '通知客户生产进度')),
    ('inspection', '验货', ('安排验货', '确认外箱、唛头、数量')),
    ('shipped', '已发货', ('订舱', '发提单和发票给客户', '确认预计到达日')),
    ('delivered', '已签收', ('确认签收', '跟进使用反馈')),
    ('aftersales', '售后', ('记录问题', '转给供应商或货代', '跟进解决')),
    ('reorder', '返单', ('询问复购', '对照上次价格和反馈')),
)
KEYS = tuple(s[0] for s in STAGES)
LABELS = {s[0]: s[1] for s in STAGES}
STATUSES = ('open', 'won', 'lost', 'paused')
STATUS_LABELS = dict(open='进行中', won='已成交', lost='已丢单', paused='暂停')


def templates(stage):
    return [dict(stage=stage, title=title, auto=1, template_key=f'{stage}:{n}')
            for n, title in enumerate(STAGES[KEYS.index(stage)][2], 1)]


def progress(stage):
    current = KEYS.index(stage)
    return [dict(key=key, label=label, index=i, current=i == current, done=i < current)
            for i, (key, label, _) in enumerate(STAGES)]
