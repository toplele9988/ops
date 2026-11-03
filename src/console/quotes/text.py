"""Deterministic customer text from an explicit allowlist, never internal notes."""
from .money import money


def render(round, lines, lang='en', terms=None):
    terms = terms or {}
    zh = lang == 'zh'
    currency = round['currency']
    out = [(f"报价单 · 第 {round['round_no']} 轮" if zh else f"Quotation — Round {round['round_no']}")]
    for index, line in enumerate(lines, 1):
        price = line.get('sell_unit_minor')
        each = money(price) if price is not None else '【待确认：销售单价】'
        subtotal = money(price * line['qty']) if price is not None else '【待确认：小计】'
        name = ' '.join(line['name'].split())
        unit = ' '.join((line.get('unit') or '').split())
        out.append(f"{index}. {name} | {line['qty']} {unit} | {currency} {each} | {currency} {subtotal}")
        low, high = line.get('lead_days_min'), line.get('lead_days_max')
        days = f'{low}–{high}' if low is not None and high is not None and low != high else str(high if high is not None else low)
        lead = (days + (' 天' if zh else ' days')) if low is not None or high is not None else '【待确认：交期】'
        out.append(('交期：' if zh else 'Lead time: ') + lead)
    destination = ' '.join((round.get('destination_country') or '【待确认：目的地】').split())
    out.append(('贸易术语与目的地：' if zh else 'Terms / Destination: ') + round['incoterm'] + ' / ' + destination)
    total = money(sum(l['sell_unit_minor'] * l['qty'] for l in lines)) if lines and all(l.get('sell_unit_minor') is not None for l in lines) else '【待确认：总价】'
    out.append(('总价：' if zh else 'Total: ') + currency + ' ' + total)
    out.append(('报价有效期：' if zh else 'Valid until: ') + (round.get('valid_until') or '【待确认：报价有效期】'))
    payment = ('30% 定金，70% 余款在发货前付清' if zh else '30% deposit, 70% balance before shipment') if terms.get('payment') == '30_70' else '【待确认：付款条件】'
    out.append(('付款条件：' if zh else 'Payment terms: ') + payment)
    out.append(('交付范围：' if zh else 'Delivery scope: ') + (terms.get('delivery_scope') or '【待确认：交付范围】'))
    return '\n'.join(out)


def pi_preview(round, lines, terms):
    missing = [label for field, label in (('seller_name','公司开票名称'),('seller_address','公司开票地址'),
               ('payment','付款条件'),('delivery_scope','交付范围')) if not terms.get(field)]
    header = ['形式发票 · 中文预览（非正式单据，未发送）',
              '开票公司：' + (terms.get('seller_name') or '【待确认：公司开票名称】'),
              '开票地址：' + (terms.get('seller_address') or '【待确认：公司开票地址】')]
    if all(line.get('sell_unit_minor') is not None for line in lines):
        total = sum(line['sell_unit_minor'] * line['qty'] for line in lines)
        deposit = (total * 30 + 50) // 100
        header.extend(('定金 30%：' + round['currency'] + ' ' + money(deposit),
                       '余款 70%：' + round['currency'] + ' ' + money(total - deposit)))
    return dict(text='\n'.join(header) + '\n' + render(round, lines, 'zh', terms), missing=missing,
                notice='仅供核对。请补齐公司开票资料并由负责人确认，不能作为正式 PI 发出。')
