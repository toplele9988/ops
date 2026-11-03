"""Pure, Decimal freight arithmetic. Packaging is required for every auto unit."""
from decimal import Decimal, InvalidOperation
from console.supplier_data.volumetric import DEFAULT_VOLUMETRIC_DIVISOR
from .money import rounded

PACKING = ('units_per_carton', 'carton_l_cm', 'carton_w_cm', 'carton_h_cm', 'gross_kg')


def calculate(lines, quote):
    details = []
    missing = []
    for index, line in enumerate(lines, 1):
        try:
            p = {k: Decimal(str(line.get(k))) for k in PACKING}
            qty = line['qty']
            if (any(not v.is_finite() or v <= 0 for v in p.values()) or
                    p['units_per_carton'] != p['units_per_carton'].to_integral_value() or
                    type(qty) is not int or qty <= 0 or not line.get('qty_confirmed', 1)):
                raise ValueError
            units = int(p['units_per_carton'])
            cartons = (qty + units - 1) // units
            cm3 = cartons * p['carton_l_cm'] * p['carton_w_cm'] * p['carton_h_cm']
            details.append(dict(line_no=index, cartons=cartons,
                gross_kg=cartons * p['gross_kg'], cbm=cm3 / 1000000,
                volumetric_kg=cm3 / DEFAULT_VOLUMETRIC_DIVISOR))
        except (InvalidOperation, ValueError, TypeError, KeyError):
            missing.append(index)
    result = dict(unit=quote['unit'], mode=quote['mode'], unit_price_fen=quote['price_fen'],
                  min_charge_fen=quote.get('min_charge_fen') or 0, divisor=DEFAULT_VOLUMETRIC_DIVISOR)
    if missing or not lines:
        return dict(result, amount_fen=None, missing_lines=missing, blocking=[dict(code='freight_packing_missing',
            message='运费资料不全，无法自动计算；请补齐外箱尺寸、毛重、每箱数量及采购数量，或填写有有效期和来源说明的人工总价')])
    gross = sum((r['gross_kg'] for r in details), Decimal(0))
    cbm = sum((r['cbm'] for r in details), Decimal(0))
    vol = sum((r['volumetric_kg'] for r in details), Decimal(0))
    chargeable = max(gross, vol) if quote['mode'] in ('air', 'express') else gross
    basis = {'kg': chargeable, 'cbm': cbm, 'ticket': Decimal(1), 'container': Decimal(1)}[quote['unit']]
    amount = max(rounded(basis * quote['price_fen']), result['min_charge_fen'])
    return dict(result, cartons=sum(r['cartons'] for r in details), gross_kg=str(gross), cbm=str(cbm),
                volumetric_kg=str(vol), chargeable_kg=str(chargeable), basis=str(basis),
                lines=[{k: str(v) if isinstance(v, Decimal) else v for k, v in row.items()} for row in details],
                amount_fen=amount, blocking=[])
