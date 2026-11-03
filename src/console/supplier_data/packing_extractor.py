"""Conservative, deterministic packing extraction; no I/O or shared counters.

Bare dimensions are ignored. Generic ``Size``/``尺寸`` means product size,
never a shipping carton. A PackingFacts list carries per-call rejection counts
without mutable global state; callers can still use it as list[PackingFact].
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata


MIN_WEIGHT_KG = 0.05
MAX_WEIGHT_KG = 500.0
MIN_SIDE_CM = 2.0
MAX_SIDE_CM = 300.0
# Missing dimension units: all three >= 100 => mm, otherwise cm.
INFER_MM_MIN = 100.0


@dataclass(frozen=True)
class PackingFact:
    kind: str
    value: tuple[float, float, float] | float | int
    raw: str
    chunk_id: str | int | None = None
    unit_inferred: bool = False
    confidence: str = 'labeled'


class PackingFacts(list[PackingFact]):
    """Extraction result with local diagnostics (not additional facts)."""

    def __init__(self):
        super().__init__()
        self.dropped_out_of_range = 0
        self.dropped_ambiguous = 0


NUMBER = r'[+-]?\d+(?:\.\d+)?'
LENGTH_UNIT = r'(?:mm|cm|m)(?![a-z])'
SEPARATOR = r'\s*[xX×*乘]\s*'
OPEN = r'[（(\[]'
CLOSE = r'[）)\]]'
UNIT_HINT = rf'(?:{OPEN}\s*(?P<label_unit>{LENGTH_UNIT})\s*{CLOSE}\s*)?'
COLON = r'\s*[:=]?\s*'
DIMENSION = re.compile(
    r'(?<![a-z])(?P<label>'
    r'(?P<flight>航空箱|航包|flight\s*case)[\s]*(?:尺寸|size|dimensions?)?'
    r'|(?P<carton>外箱|纸箱|包装|箱规|carton|packing|packaging|package)[\s]*(?:尺寸|size|dimensions?)?'
    r'|(?P<product>产品(?:本体)?|本体|机身|product)[\s]*(?:尺寸|size|dimensions?)'
    r'|(?P<generic>尺寸|size|dimensions?))'
    + COLON + UNIT_HINT + COLON
    + rf'(?P<a>{NUMBER})\s*(?P<ua>{LENGTH_UNIT})?' + SEPARATOR
    + rf'(?P<b>{NUMBER})\s*(?P<ub>{LENGTH_UNIT})?' + SEPARATOR
    + rf'(?P<c>{NUMBER})(?:\s*(?P<uc>{LENGTH_UNIT}))?'
    + rf'(?:\s*{OPEN}\s*(?P<end_unit>{LENGTH_UNIT})\s*{CLOSE})?'
    + r'(?![\d.a-z])', re.IGNORECASE,
)
WEIGHT = re.compile(
    r'(?<![a-z])(?P<label>'
    r'(?P<case>(?:航空箱|航包|flight\s*case)\s*(?:自重|净重|重量|tare(?:\s*weight)?|weight))'
    r'|(?P<gross>毛重|gross(?:\s*weight)?|g\s*\.?\s*w\.?)'
    r'|(?P<net>净重|net(?:\s*weight)?|n\s*\.?\s*w\.?))'
    + COLON + r'(?P<value>' + NUMBER + r')\s*(?P<unit>kg|g)(?![a-z])',
    re.IGNORECASE,
)
COUNT = re.compile(
    r'(?:(?P<label>装箱(?:数|数量)?|每箱数量|每箱|units\s*per\s*carton|qty\s*/\s*ctn|packing\s*qty)'
    + COLON + r'(?P<labeled>' + NUMBER + r')\s*(?:pcs?|台|件|个)?(?:\s*/\s*(?:箱|carton|ctn))?'
    r'|(?P<bare>' + NUMBER + r')\s*(?:pcs?|台|件|个)\s*/\s*(?:箱|carton|ctn))'
    r'(?![\d.])', re.IGNORECASE,
)
RANGE_TAIL = re.compile(r'\s*(?:[-~～至到或/]|to\b|or\b)\s*[+-]?\d', re.IGNORECASE)
RANGE_PREFIX = re.compile(NUMBER + r'\s*(?:[-~～至到或/]|to|or)\s*$', re.IGNORECASE)
EXTRA_AXIS = re.compile(SEPARATOR + NUMBER, re.IGNORECASE)
UNSUPPORTED_LENGTH_UNIT = re.compile(r'\s*(?:in(?:ch(?:es)?)?|ft|feet|毫米|厘米|米)(?![a-z])', re.IGNORECASE)


def _normalize(text):
    """Normalize fullwidth characters while retaining exact original offsets."""
    chars, offsets = [], []
    for index, char in enumerate(text):
        replacement = unicodedata.normalize('NFKC', char)
        chars.append(replacement)
        offsets.extend([index] * len(replacement))
    return ''.join(chars), offsets


def extract_packing_facts(text, *, chunk_id=None) -> list[PackingFact]:
    """Extract labeled facts in source order, with cm/kg normalization.

    No weight unit is guessed. Missing dimension units use INFER_MM_MIN and
    mark both unit_inferred and confidence. Conflicting explicit dimension
    unit hints are rejected. Numeric range failures are counted, not raised.
    """
    result = PackingFacts()
    normalized, offsets = _normalize(text)
    found = []

    def add(match, kind, value, inferred=False):
        raw = text[offsets[match.start()]:offsets[match.end() - 1] + 1].strip()
        found.append((match.start(), PackingFact(
            kind, value, raw, chunk_id, inferred,
            'inferred' if inferred else 'labeled',
        )))

    for match in DIMENSION.finditer(normalized):
        tail = normalized[match.end():]
        if RANGE_TAIL.match(tail) or EXTRA_AXIS.match(tail) or UNSUPPORTED_LENGTH_UNIT.match(tail):
            result.dropped_ambiguous += 1
            continue
        values = [float(match[key]) for key in ('a', 'b', 'c')]
        hints = [match['label_unit'], match['end_unit']]
        hints = [unit.lower() for unit in hints if unit]
        if len(set(hints)) > 1:
            result.dropped_ambiguous += 1
            continue
        axis_units = [match[key].lower() if match[key] else None for key in ('ua', 'ub', 'uc')]
        # A final axis unit conventionally applies to the whole triple.
        default = hints[0] if hints else axis_units[-1]
        if default is None and any(axis_units):
            result.dropped_ambiguous += 1
            continue
        inferred = default is None
        if inferred:
            default = 'mm' if min(values) >= INFER_MM_MIN else 'cm'
        if hints and any(unit and unit != default for unit in axis_units):
            result.dropped_ambiguous += 1
            continue
        factors = {'mm': 0.1, 'cm': 1.0, 'm': 100.0}
        dimensions = tuple(round(value * factors[unit or default], 6)
                           for value, unit in zip(values, axis_units))
        if not all(MIN_SIDE_CM <= value <= MAX_SIDE_CM for value in dimensions):
            result.dropped_out_of_range += 1
            continue
        kind = ('flightcase_size' if match['flight'] else
                'carton_size' if match['carton'] else 'product_size')
        add(match, kind, dimensions, inferred)

    for match in WEIGHT.finditer(normalized):
        value = float(match['value']) / (1000 if match['unit'].lower() == 'g' else 1)
        if not MIN_WEIGHT_KG <= value <= MAX_WEIGHT_KG:
            result.dropped_out_of_range += 1
            continue
        kind = ('flightcase_weight_kg' if match['case'] else
                'gross_weight_kg' if match['gross'] else 'net_weight_kg')
        add(match, kind, round(value, 6))

    for match in COUNT.finditer(normalized):
        tail = normalized[match.end():]
        if (RANGE_TAIL.match(tail) or tail.lstrip().startswith('/')
                or RANGE_PREFIX.search(normalized[:match.start()])):
            result.dropped_ambiguous += 1
            continue
        value = float(match['labeled'] or match['bare'])
        if value < 1 or not value.is_integer():
            result.dropped_out_of_range += 1
            continue
        add(match, 'units_per_carton', int(value))

    result.extend(fact for _, fact in sorted(found, key=lambda item: item[0]))
    return result
