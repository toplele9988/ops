"""Small standalone helpers for M2; no CPQ integration."""
import math


DEFAULT_VOLUMETRIC_DIVISOR = 5000


def _number(value, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError('重量、尺寸和除数必须是有限数字')
    if not math.isfinite(value) or (value <= 0 if positive else value < 0):
        raise ValueError('尺寸和除数必须大于零，重量不能为负数')
    return value


def volumetric_weight_kg(l_cm, w_cm, h_cm, divisor=DEFAULT_VOLUMETRIC_DIVISOR):
    values = [_number(value, positive=True) for value in (l_cm, w_cm, h_cm, divisor)]
    result = values[0] * values[1] * values[2] / values[3]
    if not math.isfinite(result):
        raise ValueError('体积重计算超出有限数字范围')
    return result


def chargeable_weight_kg(gross_kg, vol_kg):
    return max(_number(gross_kg), _number(vol_kg))
