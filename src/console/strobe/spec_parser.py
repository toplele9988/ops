from collections.abc import Mapping
import math
import re
from typing import Any

KEY_FIELDS = ('rgb_count', 'white_count', 'pixel_segments', 'dmx_modes', 'power_w',
              'ip_rating', 'net_weight_kg', 'dimensions_cm', 'carton', 'certifications')
FIELDS = ('rgb_count', 'white_count', 'led_type', 'pixel_segments', 'dmx_modes',
          'control_protocols', 'power_w', 'ip_rating', 'voltage', 'certifications')
_COUNT = r'(?<![\d.])(\d+)\s*(?:(?:pcs?|颗|粒)\s*)?'
_EMITTER_POWER = r'(?:(?:[×x*]\s*)?\d+(?:\.\d+)?\s*(?:w|瓦)\s*)?'
_RGB = re.compile(_COUNT + _EMITTER_POWER + r'(?:[×x]\s*)?(?:rgb|3\s*合\s*1|3\s*in\s*1)(?![a-z0-9])', re.I)
_WHITE = re.compile(_COUNT + _EMITTER_POWER + r'(?:[×x]\s*)?(?:白光|白灯|白色|white|cw|ww)(?![a-z0-9])', re.I)
_WHITE_W = re.compile(r'(?<![\d.])(\d+)\s*(?:pcs?|颗|粒)\s*' + _EMITTER_POWER + r'w(?![a-z0-9])', re.I)
_SEGMENTS = re.compile(r'(?<![\d.])(\d+)\s*(?:段|分区|segments?|zones?|sections?)(?![a-z])', re.I)
_DMX = (
    re.compile(r'(?<![\d.])(\d+)\s*(?:ch(?:annels?)?|通道)(?![a-z0-9])', re.I),
    re.compile(r'(?<![a-z0-9])ch\s*(\d+)(?!\d)', re.I),
)
_POWER = re.compile(r'(?<![\d.])(\d{2,4}(?:\.\d+)?)\s*(?:w(?![a-z])|瓦)', re.I)
_IP = re.compile(r'(?<![a-z0-9])ip\s*([0-6][0-9])(?!\d)', re.I)
_VOLTAGE = re.compile(r'(?:ac\s*)?\d{2,3}\s*[-~–至]\s*\d{2,3}\s*v(?:\s*[,，]?\s*\d{2}(?:\s*[/~\-]\s*\d{2})?\s*hz)?', re.I)
_PROTOCOLS = (
    ('DMX512', r'dmx\s*512'), ('RDM', r'(?<![a-z])rdm(?![a-z])'),
    ('Art-Net', r'art[\s-]?net'), ('sACN', r'(?<![a-z])sacn(?![a-z])'),
    ('Sound', r'(?<![a-z])sound(?![a-z])|声控'),
    ('Auto', r'(?<![a-z])auto(?![a-z])|自动运行'),
    ('Master/Slave', r'master\s*[/\-]\s*slave|主从'),
)


def empty_packing() -> dict[str, Any]:
    return {'source': 'none', 'units_per_pkg': None, 'l_cm': None,
            'w_cm': None, 'h_cm': None, 'gw_kg': None}


def _integer(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float) and math.isfinite(value) and value >= 0 and value.is_integer():
        return int(value)
    if isinstance(value, str) and re.fullmatch(r'\d+', value.strip()):
        return int(value.strip())
    return None


def _normalize(field, value):
    if value is None:
        return None
    if field in {'rgb_count', 'white_count', 'pixel_segments'}:
        return _integer(value)
    if field == 'power_w':
        if isinstance(value, bool):
            return None
        try:
            number = float(value)
        except (ValueError, TypeError, OverflowError):
            return None
        return number if math.isfinite(number) and number >= 0 else None
    if field == 'ip_rating':
        match = re.fullmatch(r'ip\s*([0-6][0-9])', value.strip(), re.I) if isinstance(value, str) else None
        return 'IP' + match.group(1) if match else None
    if field == 'dmx_modes':
        if not isinstance(value, (list, tuple)) or not value:
            return None
        numbers = [_integer(item) for item in value]
        return sorted(set(numbers)) if all(number is not None and number > 0 for number in numbers) else None
    if field in {'certifications', 'control_protocols'}:
        if not isinstance(value, (list, tuple)) or not value or any(not isinstance(item, str) or not item.strip() for item in value):
            return None
        return list(dict.fromkeys(item.strip() for item in value))
    if field in {'led_type', 'voltage'}:
        return value.strip() if isinstance(value, str) and value.strip() else None
    return None


def _provided(value):
    return value is not None and value != '' and value != [] and value != ()


def _parse_text(snapshot):
    lights = snapshot.get('light_sources', [])
    lights = [lights] if isinstance(lights, str) else lights
    lights = [item for item in lights if isinstance(item, str)] if isinstance(lights, list) else []
    descriptions = [snapshot[key] for key in ('description_en', 'spec_zh_full') if isinstance(snapshot.get(key), str)]
    text, description = '\n'.join(lights + descriptions), '\n'.join(descriptions)
    candidates = {
        'rgb_count': [int(m.group(1)) for m in _RGB.finditer(text)],
        'white_count': [int(m.group(1)) for p in (_WHITE, _WHITE_W) for m in p.finditer(text)],
        'pixel_segments': [int(m.group(1)) for m in _SEGMENTS.finditer(text)],
        'ip_rating': ['IP' + m.group(1) for m in _IP.finditer(text)],
        'voltage': [re.sub(r'\s+', '', m.group(0)).upper() for m in _VOLTAGE.finditer(text)],
    }
    parsed, conflicts = {}, set()
    for field, values in candidates.items():
        distinct = list(dict.fromkeys(values))
        parsed[field] = distinct[0] if len(distinct) == 1 else None
        if len(distinct) > 1:
            conflicts.add(field)
    packages = set(re.findall(r'(?<!\d)(5050|3535|2835)(?!\d)', text))
    packages.update(re.findall(r'smd\s*(\d{4})(?!\d)', text, re.I))
    if len(packages) > 1:
        parsed['led_type'] = None
        conflicts.add('led_type')
    elif packages:
        package = next(iter(packages))
        smd = re.search(r'(?:smd\s*' + package + r'|' + package + r'\s*smd)', text, re.I)
        parsed['led_type'] = package + ' SMD' if smd else package
    else:
        # Combined RGBW emitters cannot establish separate RGB/white counts.
        parsed['led_type'] = 'RGBW' if re.search(r'(?<![a-z])rgbw(?![a-z])', text, re.I) else None
    parsed['dmx_modes'] = sorted({int(m.group(1)) for p in _DMX for m in p.finditer(text) if int(m.group(1)) > 0}) or None
    parsed['control_protocols'] = [name for name, pattern in _PROTOCOLS if re.search(pattern, text, re.I)] or None
    powers = [float(m.group(1)) for m in _POWER.finditer(description)]
    parsed['power_w'] = max(powers) if powers else None
    # A free-text mention does not establish a certification claim.
    parsed['certifications'] = None
    return parsed, conflicts


def _complete(value):
    if value is None:
        return False
    if isinstance(value, dict):
        parts = [part for key, part in value.items() if key != 'source']
        return bool(parts) and all(part is not None for part in parts)
    return bool(value) if isinstance(value, (str, list, tuple)) else True


def parse_specs(snapshot: Mapping | None, columns: Mapping | None = None,
                overrides: Mapping | None = None) -> dict[str, Any]:
    snapshot = snapshot if isinstance(snapshot, Mapping) else {}
    columns, overrides = columns or {}, overrides or {}
    parsed, parsed_conflicts = _parse_text(snapshot)
    dedicated = {field: snapshot.get(field) for field in FIELDS}
    dedicated.update(pixel_segments=snapshot.get('segments'),
                     dmx_modes=snapshot.get('channel_modes'), power_w=snapshot.get('wattage'))
    dedicated.update({key: value for key, value in columns.items() if _provided(value)})
    values, sources, conflicts = {}, {}, set()
    for field in FIELDS:
        if field in overrides:
            raw = overrides[field]
            values[field], sources[field] = _normalize(field, raw), 'override'
            if raw is not None and values[field] is None:
                conflicts.add(field)
        elif _provided(dedicated.get(field)):
            values[field], sources[field] = _normalize(field, dedicated[field]), 'column'
            if values[field] is None:
                conflicts.add(field)
        else:
            values[field] = parsed.get(field)
            sources[field] = 'parsed' if values[field] is not None or field in parsed_conflicts else 'none'
            if field in parsed_conflicts:
                conflicts.add(field)
    # Explicit batch restriction: no supplied physical/packaging evidence.
    values.update(net_weight_kg=None, dimensions_cm={'l': None, 'w': None, 'h': None},
                  carton=empty_packing(), flight_case=empty_packing())
    sources.update({field: 'none' for field in ('net_weight_kg', 'dimensions_cm', 'carton', 'flight_case')})
    rgb, white = values['rgb_count'], values['white_count']
    values['dual'] = (rgb > 0 and white > 0) if rgb is not None and white is not None else None
    labels = ([f'{rgb} RGB'] if rgb is not None else []) + ([f'{white} W'] if white is not None else [])
    values['label'] = ' + '.join(labels) or None
    evidence = {sources[field] for field in ('rgb_count', 'white_count') if values[field] is not None}
    derived_source = next(iter(evidence)) if len(evidence) == 1 else ('parsed' if evidence else 'none')
    sources.update({field: derived_source if values[field] is not None else 'none' for field in ('dual', 'label')})
    missing = [field for field in KEY_FIELDS if not _complete(values[field])]
    return {'values': values, 'sources': sources, 'conflicts': sorted(conflicts),
            'spec_completeness': {'score': (len(KEY_FIELDS) - len(missing)) / len(KEY_FIELDS), 'missing': missing}}
