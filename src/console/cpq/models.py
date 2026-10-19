"""Immutable calculation inputs and DTOs."""
from __future__ import annotations
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import date
from types import MappingProxyType
from typing import Any, Mapping


def freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({k: freeze(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(freeze(v) for v in value)
    return value


def json_value(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: json_value(getattr(value, f.name)) for f in fields(value) if f.metadata.get('serialize', True)}
    if isinstance(value, Mapping):
        return {k: json_value(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [json_value(v) for v in value]
    if isinstance(value, date):
        return value.isoformat()
    return value


@dataclass(frozen=True, slots=True)
class Accessory:
    cost_cny: float
    qty_per_unit: float


@dataclass(frozen=True, slots=True)
class Packing:
    units_per_pkg: int
    l_cm: float
    w_cm: float
    h_cm: float
    gw_kg: float
    case_cost_cny: float | None = None


@dataclass(frozen=True, slots=True)
class PackingProfiles:
    carton: Packing
    flight_case: Packing | None = None
    override: Packing | None = None


@dataclass(frozen=True, slots=True)
class LineInput:
    model_id: str
    qty: int
    packing_mode: str
    packing: PackingProfiles
    cost_basis_cny: float | None
    unit_price_usd: float | None = None
    accessories: tuple[Accessory, ...] = ()
    net_weight_kg: float | None = None
    packing_estimated: bool = False
    case_cost_missing: bool = False
    category: str = 'strobe'
    spec_complete: bool = True
    case_cost_cny: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, 'accessories', tuple(self.accessories))


@dataclass(frozen=True, slots=True)
class ExpressBand:
    upto_kg: float
    usd_per_kg: float


@dataclass(frozen=True, slots=True)
class Lane:
    lane_id: str
    mode: str
    valid_until: date
    dest_country: str = ''
    named_place: str = ''
    currency: str = 'USD'
    inland_cny_per_cbm: float = 0.0
    inland_min_cny: float = 0.0
    origin_fixed_usd: float = 0.0
    origin_per_cbm_usd: float = 0.0
    lcl_rate_usd_per_wm: float = 0.0
    lcl_min_wm: float = 1.0
    fcl_rate_usd: Mapping[str, float] = field(default_factory=lambda: {'20GP': 0.0, '40GP': 0.0, '40HQ': 0.0})
    fcl_origin_fixed_usd_per_container: float = 0.0
    fcl_dest_fixed_usd_per_container: float = 0.0
    dest_fixed_usd: float = 0.0
    dest_per_cbm_usd: float = 0.0
    last_mile_usd: float = 0.0
    duty_rate: float = 0.0
    duty_basis: str = 'CIF'
    import_vat_rate: float = 0.0
    include_import_vat: bool = True
    volumetric_divisor: float | None = None
    increment_kg: float | None = None
    bands: tuple[ExpressBand, ...] = ()
    min_charge_usd: float = 0.0
    fuel_surcharge_pct: float = 0.0
    ddp_fee_usd: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(self, 'fcl_rate_usd', freeze(self.fcl_rate_usd))
        object.__setattr__(self, 'bands', tuple(self.bands))


@dataclass(frozen=True, slots=True)
class ShippingInput:
    mode: str
    incoterm: str
    lane: Lane


@dataclass(frozen=True, slots=True)
class CalcInputs:
    lines: tuple[LineInput, ...]
    shipping: ShippingInput
    fx_rate: float
    fx_age_days: int
    fx_buffer: float | None
    target_margin: float
    pricing_mode: str
    config: Mapping[str, Any]
    today: date

    def __post_init__(self) -> None:
        object.__setattr__(self, 'lines', tuple(self.lines))
        object.__setattr__(self, 'config', freeze(self.config))


@dataclass(frozen=True, slots=True)
class GateContext:
    permissions: frozenset[str] = frozenset()
    acks: frozenset[str] = frozenset()
    override_reason: str = ''
    images_ok: Mapping[str, bool] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, 'permissions', frozenset(self.permissions))
        object.__setattr__(self, 'acks', frozenset(self.acks))
        if self.images_ok is not None:
            object.__setattr__(self, 'images_ok', freeze(self.images_ok))


@dataclass(frozen=True, slots=True)
class Alert:
    code: str
    level: str
    message: str
    suggestions: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Gate:
    can_save: bool
    can_export: bool
    requires_ack: list[str]
    requires_override: bool
    blocked_by: list[str]


@dataclass(frozen=True, slots=True)
class LineResult:
    model_id: str
    qty: int
    packages: int
    cbm: float
    gw_kg: float
    chargeable_kg: float
    unit_price_usd: float
    amount_usd: float
    logistics_alloc_usd: float
    unit_goods_value_cny: float
    cost_usd: float


@dataclass(frozen=True, slots=True)
class Totals:
    packages: int
    cbm: float
    gw_kg: float
    chargeable_kg: float
    price_usd: float
    cost_usd: float
    actual_margin: float | None
    goods_cost_usd: float
    packaging_cost_usd: float
    accessory_cost_usd: float
    freight_usd: float
    duty_vat_usd: float
    payment_fee_usd: float
    freight_ratio: float | None


@dataclass(frozen=True, slots=True)
class Breakdown:
    inland_usd: float
    origin_usd: float
    main_carriage_usd: float
    insurance_usd: float
    dest_usd: float
    duty_usd: float
    vat_usd: float


@dataclass(frozen=True, slots=True)
class FCLPlan:
    containers: Mapping[str, int]
    utilization_cbm: float
    cost_usd: float

    def __post_init__(self) -> None:
        object.__setattr__(self, 'containers', freeze(self.containers))

    @property
    def total_containers(self) -> int:
        return sum(self.containers.values())


@dataclass(frozen=True, slots=True)
class PackingComparison:
    packages: int | None
    cbm: float | None
    gw_kg: float | None
    total_cost_usd: float | None


@dataclass(frozen=True, slots=True)
class PackingCompare:
    carton: PackingComparison
    flight_case: PackingComparison


@dataclass(frozen=True, slots=True)
class CalcResult:
    lines: tuple[LineResult, ...]
    totals: Totals
    breakdown: Breakdown
    fcl_plan: FCLPlan | None
    packing_compare: PackingCompare
    alerts: tuple[Alert, ...]
    gate: Gate
    engine_version: str
    _raw_goods_values: tuple[float, ...] = field(default=(), repr=False, metadata={'serialize': False})

    def to_dict(self) -> dict[str, Any]:
        return json_value(self)
