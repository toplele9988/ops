"""Read the existing local USD/CNY cache; never fetch or infer an exchange rate."""
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
from .money import rounded, rate

FX_PATH = Path('/var/lib/imgw/wechat/fx_usdcny_cache.json')


def read_fx(path=FX_PATH):
    empty = dict(usd_cny=None, source=None, as_of=None)
    try:
        with Path(path).open(encoding='utf8') as stream:
            raw = stream.read(16385)
        if len(raw) > 16384:
            return empty
        data = json.loads(raw, parse_float=Decimal)
        value = data['rate']
        if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
            return empty
        value = Decimal(value)
        if not value.is_finite() or not Decimal('0.0001') <= value <= 1000000:
            return empty
        stamp = data.get('fetched_at')
        if isinstance(stamp, bool) or not isinstance(stamp, (int, Decimal)) or not Decimal(stamp).is_finite() or stamp < 0:
            stamp = None
        source = data.get('source')
        if not isinstance(source, str) or not source.strip() or len(source) > 200:
            return empty
        return dict(usd_cny=rate(rounded(value * 10000)), source=source, as_of=int(stamp) if stamp is not None else None)
    except (OSError, ValueError, TypeError, KeyError, InvalidOperation):
        return empty
