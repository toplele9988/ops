"""YAML configuration validation."""
from __future__ import annotations
from math import isfinite
from pathlib import Path
from typing import Any, Mapping
import yaml
from .models import freeze


class ConfigError(ValueError):
    """Invalid configuration with a field-specific diagnostic."""


def _get(data: Mapping[str, Any], path: str) -> Any:
    value: Any = data
    for key in path.split('.'):
        if not isinstance(value, Mapping) or key not in value:
            raise ConfigError(f'{path}: required')
        value = value[key]
    return value


def _number(data: Mapping[str, Any], path: str, low: float = 0, high: float | None = None, *, positive: bool = False, exclusive: bool = False, integer: bool = False) -> float:
    value = _get(data, path)
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not isfinite(value):
        raise ConfigError(f'{path}: finite number required')
    if integer and not isinstance(value, int):
        raise ConfigError(f'{path}: integer required')
    if value < low or (positive and value == low) or (high is not None and (value > high or (exclusive and value == high))):
        raise ConfigError(f'{path}: out of range')
    return float(value)


def config_from_dict(data: Mapping[str, Any]) -> Mapping[str, Any]:
    """Validate and return a detached immutable configuration."""
    minimum = _number(data, 'thresholds.min_margin', high=1, exclusive=True)
    fuse = _number(data, 'thresholds.fuse_margin', low=-1, high=1, exclusive=True)
    if fuse >= minimum:
        raise ConfigError('thresholds.fuse_margin: must be below min_margin')
    ratios = [_number(data, f'thresholds.freight_ratio.{k}') for k in ('sensitive', 'heavy', 'inverted')]
    if not ratios[0] < ratios[1] <= ratios[2]:
        raise ConfigError('thresholds.freight_ratio: invalid ordering')
    floors = _get(data, 'thresholds.price_floor_cny_per_unit')
    if not isinstance(floors, Mapping):
        raise ConfigError('thresholds.price_floor_cny_per_unit: mapping required')
    for key in floors:
        if not isinstance(key, str) or not key or '.' in key:
            raise ConfigError('thresholds.price_floor_cny_per_unit: invalid category')
        _number(data, f'thresholds.price_floor_cny_per_unit.{key}')
    stale = _number(data, 'thresholds.fx_stale_days', integer=True)
    expired = _number(data, 'thresholds.fx_expired_days', positive=True, integer=True)
    if stale >= expired:
        raise ConfigError('thresholds.fx_stale_days: must be below fx_expired_days')
    _number(data, 'thresholds.override_reason_min_chars', positive=True, integer=True)
    for key in ('fx_buffer_pct', 'payment_fee_pct'):
        _number(data, f'pricing.{key}', high=1, exclusive=True)
    _number(data, 'pricing.insurance_rate', high=1)
    _number(data, 'pricing.insured_markup', low=1)
    _number(data, 'pricing.unit_price_step_usd', positive=True)
    _number(data, 'pricing.quote_validity_days', positive=True, integer=True)
    containers = _get(data, 'containers')
    if not isinstance(containers, Mapping) or set(containers) != {'20GP', '40GP', '40HQ'}:
        raise ConfigError('containers: require 20GP, 40GP and 40HQ')
    for name in containers:
        for key in ('usable_cbm', 'max_kg'):
            _number(data, f'containers.{name}.{key}', positive=True)
    for key in ('fc_pad_cm', 'fc_tare_kg'):
        _number(data, f'packing_estimate.{key}')
    pattern = _get(data, 'public_sku.pattern')
    if not isinstance(pattern, str) or not pattern:
        raise ConfigError('public_sku.pattern: string required')
    try:
        pattern.format(brand='TEST', seq=1)
    except (KeyError, IndexError, ValueError, AttributeError) as exc:
        raise ConfigError('public_sku.pattern: invalid format') from exc
    terms = _get(data, 'payment_terms')
    if not isinstance(terms, (list, tuple)) or not terms:
        raise ConfigError('payment_terms: nonempty list required')
    ids: set[str] = set()
    for index, term in enumerate(terms):
        if not isinstance(term, Mapping) or any(not isinstance(term.get(k), str) or not term[k].strip() for k in ('id', 'en')):
            raise ConfigError(f'payment_terms[{index}]: id/en required')
        if term['id'] in ids:
            raise ConfigError(f'payment_terms[{index}].id: duplicate')
        ids.add(term['id'])
    return freeze(data)


def load_config(path: str | Path = 'config/cpq.yaml') -> Mapping[str, Any]:
    try:
        with Path(path).open(encoding='utf-8') as stream:
            data = yaml.safe_load(stream)
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f'{path}: {exc}') from exc
    return config_from_dict(data)
