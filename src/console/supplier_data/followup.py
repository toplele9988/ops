"""Chinese supplier follow-up drafts. This module never sends anything."""
from collections import defaultdict

from .completeness import KIND_LABELS, assess
from .labels import product_label, product_reference


MISSING_PREVIEW_LIMIT = 8
MISSING_LABEL_LIMIT = 100


def format_value(kind, value):
    if isinstance(value, (tuple, list)):
        return ' × '.join(f'{part:g}' for part in value) + ' cm'
    if kind == 'units_per_carton':
        return f'{value:g} 台/箱'
    return f'{value:g} kg'


def build_followups(products) -> str:
    """Accept product mappings with supplier/model/name/id and aggregated facts.

    Returns one plain-text string, grouped by supplier. A caller rendering one
    copy button per supplier can call this with each supplier's product list.
    """
    grouped = defaultdict(list)
    for product in products:
        completeness = assess(product['facts'])
        if not completeness.ddp_ready:
            grouped[str(product.get('supplier') or '未注明供应商')].append(
                (product, completeness))
    drafts = []
    for supplier, entries in grouped.items():
        lines = [f'供应商：{supplier}', '您好，为核算运费，请补充或确认以下资料条目的包装信息：']
        missing_products = [product for product, comp in entries if comp.status == 'missing']
        if missing_products:
            preview = [product_reference(product)[:MISSING_LABEL_LIMIT]
                       for product in missing_products[:MISSING_PREVIEW_LIMIT]]
            suffix = '；……其余省略' if len(missing_products) > MISSING_PREVIEW_LIMIT else ''
            lines.append(f'\n以下 {len(missing_products)} 条资料暂无任何包装信息：'
                         + '；'.join(preview) + suffix + '。')
            lines.append('请补充外箱或航空箱尺寸（三边）、整箱毛重、每箱数量，并注明单位。')
        for product, completeness in entries:
            if completeness.status == 'missing':
                continue
            lines.append(f'\n资料条目：{product_reference(product)}')
            if completeness.ddp_estimable:
                lines.append('仅可估算：' + '；'.join(completeness.assumptions) + '。')
                lines.append('待确认每箱数量：请确认包装是否单台一箱，如不是请告知每箱数量。')
            elif completeness.missing:
                lines.append('请补充：' + '、'.join(completeness.missing) + '。')
            multi_model = completeness.conflict_reason == 'multi_model_suspected'
            if multi_model:
                lines.append('疑似一条资料混入多个型号，请按型号分别提供尺寸、整箱毛重和每箱数量，拆分后再核对。')
            for kind in completeness.conflicts:
                values = dict.fromkeys(format_value(kind, fact.value)
                                       for fact in product['facts'][kind].candidates)
                if multi_model:
                    lines.append(f'{KIND_LABELS[kind]}候选：' + '；'.join(values)
                                 + '，请分别注明所属型号及包装方案。')
                else:
                    lines.append(f'请确认{KIND_LABELS[kind]}：资料中出现 '
                                 + '；'.join(values) + '，请确认当前正确值（如有版本/批次差异请注明）。')
            lines.append('请说明尺寸、整箱毛重和每箱数量是否属于同一包装方案，并注明单位。')
        lines.append('\n以上为追问草稿，仅供人工审核，尚未发送。')
        drafts.append('\n'.join(lines))
    return '\n\n'.join(drafts)
