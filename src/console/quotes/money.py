"""Integer monetary units and Decimal rounding shared by the pure kernels."""
from decimal import Decimal, ROUND_HALF_UP


def rounded(value):
    return int(Decimal(value).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def money(value):
    return None if value is None else format(Decimal(value) / 100, '.2f')


def rate(value):
    return None if value is None else format(Decimal(value) / 10000, '.4f')
