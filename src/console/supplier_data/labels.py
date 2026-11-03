"""Conservative display names for extracted entries, never catalog hashes."""
import math
import re
import unicodedata


DESCRIPTION_LIMIT = 36
_SERIAL = re.compile(r'^\d+(?:(?:\.\d+)*[.、:：)）]|\s|$)')
_FORMULA = re.compile(r'^\s*=|_xlfn\.|DISPIMG\s*\(', re.IGNORECASE)
_HEADERS = {'型号', '品名', '名称', '产品名称', '功率', '数量', '序号', '单价',
            '规格', '图片', '备注', '产品型号', '价格', '单位'}
_FIXTURES = {'wash': '染色灯', 'strobe': '频闪灯', 'spot': '图案灯',
             'beam': '光束灯', 'par': '帕灯', 'moving_head': '摇头灯',
             'moving head': '摇头灯', 'led': '灯具'}


def _description_label(description):
    for line in str(description or '').splitlines():
        line = unicodedata.normalize('NFKC', line).strip()
        if not line or _FORMULA.search(line) or _SERIAL.match(line):
            continue
        # Keep useful table cells, discard headers, empty separators and numbers.
        cells = re.split(r'[|｜]', line)
        words = [re.sub(r'\s+', ' ', cell).strip() for cell in cells]
        words = [word for word in words if word and re.sub(r'\s*\([^)]*\)$', '', word) not in _HEADERS
                 and re.search(r'[a-zA-Z\u3400-\u9fff]', word)]
        if not words:
            continue
        label = ' '.join(words)
        return label if len(label) <= DESCRIPTION_LIMIT else label[:DESCRIPTION_LIMIT - 1] + '…'
    return ''


def product_label(product):
    model = str(product.get('model') or '').strip()
    if model and not _FORMULA.search(unicodedata.normalize('NFKC', model)):
        return re.sub(r'\s+', ' ', model)
    description = _description_label(product.get('description'))
    if description:
        return description
    fixture = str(product.get('fixture_type') or '').strip()
    fixture = _FIXTURES.get(fixture.lower(), fixture if re.search(r'[\u3400-\u9fff]', fixture) else '灯具')
    try:
        wattage = float(product.get('wattage'))
        power = f'{wattage:g}W' if math.isfinite(wattage) and wattage > 0 else ''
    except (TypeError, ValueError, OverflowError):
        power = ''
    return fixture + (f' {power}' if power else '')


def product_reference(product):
    label = product_label(product)
    filename = str(product.get('source_file') or '').strip()
    return f'{filename} 中的「{label}」' if filename else f'「{label}」（未注明来源文件）'
