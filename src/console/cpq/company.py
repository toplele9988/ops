'''Company information without invented company details or commercial terms.'''
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
import re
import yaml

DEFAULT_PROFILE = Path(__file__).resolve().parents[3] / 'config/company_profile.yaml'
FIELDS = 'company address contact email whatsapp website logo_path brand warranty price_includes payment_terms default_lead_time'.split()
REQUIRED = 'company address contact email warranty price_includes payment_terms default_lead_time'.split()
PLACEHOLDER = re.compile(r'^(?:<.*>|\[.*\]|\{\{.*\}\}|todo|tbd|placeholder|_+)$', re.I)


@dataclass(frozen=True)
class CompanyProfile:
    values: dict[str, str]
    incomplete_fields: tuple[str, ...]

    @property
    def seller(self):
        return {key: self.values[key] for key in 'company address contact email whatsapp website'.split()}


def payment_terms(config):
    entries = config.get('payment_terms', ())
    if not isinstance(entries, (list, tuple)):
        raise ValueError('Invalid payment terms configuration')
    result = {}
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise ValueError('Invalid payment terms configuration')
        key, text = entry.get('id'), entry.get('en')
        if not isinstance(key, str) or not key.strip() or key != key.strip():
            raise ValueError('Invalid payment terms id')
        if key in result or not isinstance(text, str) or not text.strip():
            raise ValueError('Invalid payment terms text')
        result[key] = text.strip()
    return result


def validate_company_profile(raw, config):
    if not isinstance(raw, Mapping) or set(raw) - set(FIELDS):
        raise ValueError('Invalid company profile fields')
    values = {}
    for key in FIELDS:
        text = raw.get(key, 'XL' if key == 'brand' else '')
        if not isinstance(text, str):
            raise ValueError('Company profile values must be strings')
        text = text.strip()
        values[key] = '' if PLACEHOLDER.fullmatch(text) else text
    values['brand'] = values['brand'] or 'XL'
    terms = payment_terms(config)
    if values['payment_terms'] and values['payment_terms'] not in terms:
        raise ValueError('Unknown company payment terms id')
    return CompanyProfile(values, tuple(key for key in REQUIRED if not values[key]))


def load_company_profile(path=DEFAULT_PROFILE, *, config):
    try:
        raw = yaml.safe_load(Path(path).read_text(encoding='utf-8'))
    except FileNotFoundError:
        raw = {}
    return validate_company_profile({} if raw is None else raw, config)
