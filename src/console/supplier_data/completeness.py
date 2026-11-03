"""Merge evidence without selecting a winner, then apply the M1 DDP gate."""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations

from .packing_extractor import PackingFact


CONFLICT_TOLERANCE = 0.02
MULTI_MODEL_TOLERANCE = 0.30
CONFLICT_REASON_LABELS = {
    'multi_model_suspected': '疑似一条资料混入多个型号，需拆分后再核对',
    'value_mismatch': '同一项数值不一致，需向供应商确认',
}
SINGLE_UNIT_ASSUMPTION = '每箱数量未注明，按每箱 1 台估算，须向供应商确认'
KIND_LABELS = {
    'product_size': '产品尺寸',
    'carton_size': '外箱尺寸',
    'flightcase_size': '航空箱尺寸',
    'net_weight_kg': '净重',
    'gross_weight_kg': '毛重',
    'flightcase_weight_kg': '航空箱自重',
    'units_per_carton': '每箱数量',
}
SHIPPING_SIZE_LABEL = '外箱或航空箱尺寸（三边）'
# Required fields contribute 80 points; optional product size/net weight 20.
SCORE_WEIGHTS = {'shipping_size': 30, 'gross_weight_kg': 30,
                 'units_per_carton': 20, 'net_weight_kg': 10, 'product_size': 10}


@dataclass(frozen=True)
class AggregatedFact:
    kind: str
    candidates: tuple[PackingFact, ...]
    conflict: bool
    conflict_reason: str | None = None


@dataclass(frozen=True)
class Completeness:
    status: str
    missing: list[str]
    score: int
    ddp_ready: bool
    optional_missing: list[str]
    conflicts: list[str]
    ddp_estimable: bool = False
    assumptions: list[str] = field(default_factory=list)
    conflict_reason: str | None = None


def _different(left, right, tolerance=CONFLICT_TOLERANCE):
    left = left if isinstance(left, tuple) else (left,)
    right = right if isinstance(right, tuple) else (right,)
    if len(left) != len(right):
        return True
    # Compare each ordered axis. The smaller value is the conservative base.
    return any(abs(a - b) > tolerance * min(abs(a), abs(b)) + 1e-9
               for a, b in zip(left, right))


def aggregate_facts(facts) -> dict[str, AggregatedFact]:
    """Group ONE product's facts; keep every candidate and its source.

    Relative difference >2% flags conflict; >30% suggests mixed models.
    Both thresholds use the smaller absolute value and ordered axes. The
    reason is a routing hint, never proof of a model or a selected value.
    """
    grouped = {}
    for fact in facts:
        if fact.kind in KIND_LABELS:
            grouped.setdefault(fact.kind, []).append(fact)
    result = {}
    for kind, values in grouped.items():
        reason = None
        for a, b in combinations(values, 2):
            if _different(a.value, b.value, MULTI_MODEL_TOLERANCE):
                reason = 'multi_model_suspected'
                break
            if _different(a.value, b.value):
                reason = 'value_mismatch'
        result[kind] = AggregatedFact(kind, tuple(values), reason is not None, reason)
    return result


def assess(product_facts) -> Completeness:
    """Readiness is a data gate only; extracted specifications need review."""
    present = {kind for kind, group in product_facts.items() if group.candidates}
    conflicts = [kind for kind in KIND_LABELS
                 if kind in present and product_facts[kind].conflict]
    usable = present - set(conflicts)
    has_size = bool(present & {'carton_size', 'flightcase_size'})
    missing = []
    if not has_size:
        missing.append(SHIPPING_SIZE_LABEL)
    for kind in ('gross_weight_kg', 'units_per_carton'):
        if kind not in present:
            missing.append(KIND_LABELS[kind])
    optional_missing = [KIND_LABELS[kind] for kind in ('net_weight_kg', 'product_size')
                        if kind not in present]
    score = sum(points for kind, points in SCORE_WEIGHTS.items() if (
        bool(usable & {'carton_size', 'flightcase_size'}) if kind == 'shipping_size'
        else kind in usable
    ))
    estimable = not conflicts and has_size and missing == ['每箱数量']
    status = ('conflict' if conflicts else 'missing' if not present else
              'estimable' if estimable else 'partial' if missing else 'ready')
    reasons = {product_facts[kind].conflict_reason for kind in conflicts}
    reason = ('multi_model_suspected' if 'multi_model_suspected' in reasons else
              'value_mismatch' if conflicts else None)
    return Completeness(status, missing, score, not missing and not conflicts,
                        optional_missing, conflicts, estimable,
                        [SINGLE_UNIT_ASSUMPTION] if estimable else [], reason)
