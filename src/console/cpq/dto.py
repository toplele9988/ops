'''Explicit, immutable customer quote projection with a fixed field whitelist.'''
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from math import isfinite
import re
from typing import Any, get_args, get_origin, get_type_hints


@dataclass(frozen=True, slots=True)
class Seller:
    company: str
    address: str
    contact: str
    email: str
    whatsapp: str
    website: str


@dataclass(frozen=True, slots=True)
class Buyer:
    company: str
    contact: str
    country: str


@dataclass(frozen=True, slots=True)
class Spec:
    label: str
    value: str


@dataclass(frozen=True, slots=True)
class Packing:
    type: str
    units_per_pkg: int | None
    pkg_size_cm: str
    pkg_gw_kg: float | None
    packages: int
    cbm: float
    gw_kg: float


@dataclass(frozen=True, slots=True)
class Line:
    line_no: int
    sku: str
    product_name: str
    highlights: tuple[str, ...]
    specs: tuple[Spec, ...]
    packing: Packing
    accessories_included: tuple[str, ...]
    image_refs: tuple[str, ...]
    qty: int
    unit_price: float
    amount: float
    lead_time: str


@dataclass(frozen=True, slots=True)
class Totals:
    total_amount: float
    packages: int
    cbm: float
    gw_kg: float


@dataclass(frozen=True, slots=True)
class Terms:
    payment: str
    lead_time: str
    warranty: str
    price_includes: str
    validity: str


@dataclass(frozen=True, slots=True)
class CustomerQuoteDTO:
    quote_no: str
    issue_date: str
    valid_until: str
    currency: str
    seller: Seller
    buyer: Buyer
    incoterm: str
    named_place: str
    shipping_mode_label: str
    lines: tuple[Line, ...]
    totals: Totals
    terms: Terms


_SHIPPING_LABELS = {
    'sea_lcl': 'Sea freight (LCL)',
    'sea_fcl': 'Sea freight (FCL)',
    'express': 'Express courier',
}
_PACKING_LABELS = {'carton': 'Carton', 'flight_case': 'Flight case'}
_UNKNOWN_TEXT = {'', 'none', 'null', 'unknown', 'n/a', 'na', '-', 'not available', 'not provided'}


def dataclass_field_tree(cls: type) -> dict[str, Any]:
    '''Return field names only: scalar leaves are None.

    Nested dataclasses become dictionaries; sequences of dataclasses become
    one-element lists containing their field tree. Other sequences are leaves.
    '''
    if not isinstance(cls, type) or not is_dataclass(cls):
        raise TypeError('Expected a dataclass type')
    annotations = get_type_hints(cls)
    result = {}
    for field in fields(cls):
        annotation = annotations[field.name]
        if is_dataclass(annotation):
            child = dataclass_field_tree(annotation)
        elif get_origin(annotation) in (tuple, list):
            arguments = get_args(annotation)
            element = arguments[0] if arguments else None
            child = [dataclass_field_tree(element)] if is_dataclass(element) else None
        else:
            child = None
        result[field.name] = child
    return result


def _object(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError('Invalid snapshot object')
    return value


def _optional_object(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> tuple[Any, ...]:
    if not isinstance(value, (tuple, list)):
        raise ValueError('Invalid snapshot sequence')
    return tuple(value)


def _text(value: Any, *, required: bool = False) -> str:
    if value is None and not required:
        return ''
    if not isinstance(value, str):
        raise ValueError('Invalid snapshot text')
    result = value.strip()
    if '/media/' in result.lower():
        raise ValueError('Invalid customer text')
    if required and not result:
        raise ValueError('Required customer text is missing')
    return result


def _decimal(value: Any) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise ValueError('Invalid snapshot number')
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError('Invalid snapshot number') from None
    if not number.is_finite():
        raise ValueError('Invalid snapshot number')
    return number


def round2(value: Any) -> float:
    '''Decimal(str(value)), rounded to two places using ROUND_HALF_UP.'''
    try:
        result = float(_decimal(value).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))
    except (InvalidOperation, OverflowError):
        raise ValueError('Invalid snapshot number') from None
    if not isfinite(result):
        raise ValueError('Invalid snapshot number')
    return result


def _nonnegative(value: Any) -> Decimal:
    number = _decimal(value)
    if number < 0:
        raise ValueError('Invalid snapshot number')
    return number


def _rounded(value: Any) -> float:
    return round2(_nonnegative(value))


def _unrounded(value: Any) -> float:
    result = float(_nonnegative(value))
    if not isfinite(result):
        raise ValueError('Invalid snapshot number')
    return result


def _count(value: Any, *, positive: bool = False) -> int:
    number = _nonnegative(value)
    if number != number.to_integral_value() or (positive and number == 0):
        raise ValueError('Invalid snapshot count')
    return int(number)


def _known_positive(value: Any) -> Decimal | None:
    try:
        number = _decimal(value)
    except ValueError:
        return None
    return number if number > 0 else None


def _known_count(value: Any) -> int | None:
    number = _known_positive(value)
    if number is None or number != number.to_integral_value():
        return None
    return int(number)


def _number_text(number: Decimal | int) -> str:
    result = format(number, 'f')
    return result.rstrip('0').rstrip('.') if '.' in result else result


def _english(value: Any) -> str:
    if not isinstance(value, str):
        return ''
    result = _text(value)
    if not result.isascii() or result.lower() in _UNKNOWN_TEXT:
        return ''
    if re.search(r'\b(?:none|null|unknown|n/a)\b', result, re.IGNORECASE):
        return ''
    if re.search(r'(?<![\d.])0(?:\.0+)?(?![\d.])', result):
        return ''
    return result


def _english_values(value: Any) -> tuple[str, ...]:
    entries = (value,) if isinstance(value, str) else value
    if not isinstance(entries, (tuple, list)):
        return ()
    return tuple(dict.fromkeys(text for entry in entries if (text := _english(entry))))


def _ip_rating(value: Any) -> str:
    text = _english(value).upper()
    return text if re.fullmatch(r'IP[0-9X]{2}[A-Z]?', text) else ''


def _size(value: Any, keys: tuple[str, str, str]) -> str:
    profile = _optional_object(value)
    dimensions = tuple(_known_positive(profile.get(key)) for key in keys)
    if any(number is None for number in dimensions):
        return ''
    return ' × '.join(_number_text(number) for number in dimensions)


def _highlights(item: Mapping[str, Any]) -> tuple[str, ...]:
    source = _optional_object(item.get('light_source'))
    rgb = _known_count(source.get('rgb_count'))
    white = _known_count(source.get('white_count'))
    result = []
    if rgb is not None and white is not None:
        result.append(f'{rgb} RGB + {white} White dual light source')
    elif rgb is not None:
        result.append(f'{rgb} RGB light source')
    elif white is not None:
        result.append(f'{white} White light source')
    segments = _known_count(item.get('pixel_segments'))
    if segments is not None:
        result.append(f'{segments}-segment pixel control')
    rating = _ip_rating(item.get('ip_rating'))
    if rating:
        result.append(rating)
    return tuple(result)


def _specs(item: Mapping[str, Any]) -> tuple[Spec, ...]:
    result = []
    modes = item.get('dmx_modes')
    if isinstance(modes, (tuple, list)):
        known_modes = sorted({mode for value in modes if (mode := _known_count(value)) is not None})
        if known_modes:
            result.append(Spec(label='DMX modes', value=' / '.join(str(mode) for mode in known_modes) + ' ch'))
    protocols = _english_values(item.get('control_protocols'))
    if protocols:
        result.append(Spec(label='Control protocols', value=' / '.join(protocols)))
    power = _known_positive(item.get('power_w'))
    if power is not None:
        result.append(Spec(label='Power', value=_number_text(power) + ' W'))
    rating = _ip_rating(item.get('ip_rating'))
    if rating:
        result.append(Spec(label='IP rating', value=rating))
    voltage = _english(item.get('voltage'))
    if voltage:
        result.append(Spec(label='Voltage', value=voltage))
    elif not isinstance(item.get('voltage'), str):
        volts = _known_positive(item.get('voltage'))
        if volts is not None:
            result.append(Spec(label='Voltage', value=_number_text(volts) + ' V'))
    weight = _known_positive(item.get('net_weight_kg'))
    if weight is not None and round2(weight) > 0:
        result.append(Spec(label='Net weight', value=f'{round2(weight):.2f} kg'))
    size = _size(item.get('dimensions_cm'), ('l', 'w', 'h'))
    if size:
        result.append(Spec(label='Dimensions', value=size + ' cm'))
    source = _optional_object(item.get('light_source'))
    led_type = _english(source.get('led_type'))
    if led_type:
        result.append(Spec(label='LED type', value=led_type))
    certifications = _english_values(item.get('certifications'))
    if certifications:
        result.append(Spec(label='Certifications', value=' / '.join(certifications)))
    return tuple(result)


def _accessories(value: Any) -> tuple[str, ...]:
    result = []
    for entry in _sequence(value):
        accessory = _object(entry)
        name = _english(accessory.get('name_en'))
        quantity = _known_positive(accessory.get('qty_per_unit'))
        if name and quantity is not None:
            result.append(_number_text(quantity) + ' × ' + name)
    return tuple(result)


def _image_refs(value: Any) -> tuple[str, ...]:
    result = []
    for entry in _sequence(value):
        uid = _text(entry, required=True)
        if any(character in uid for character in ('/', '\\', ':')) or any(character.isspace() for character in uid):
            raise ValueError('Invalid image identifier')
        result.append(uid)
    return tuple(result)


def _line(value: Any, line_no: int) -> Line:
    row = _object(value)
    item = _object(row.get('item'))
    result = _object(row.get('result'))
    mode = _text(row.get('packing_mode'), required=True)
    if mode not in _PACKING_LABELS:
        raise ValueError('Unknown packing mode')
    profile = _optional_object(item.get(mode))
    package_weight = _known_positive(profile.get('gw_kg'))
    packing = Packing(
        type=_PACKING_LABELS[mode],
        units_per_pkg=_known_count(profile.get('units_per_pkg')),
        pkg_size_cm=_size(profile, ('l_cm', 'w_cm', 'h_cm')),
        pkg_gw_kg=round2(package_weight) if package_weight is not None else None,
        packages=_count(result.get('packages')),
        cbm=_unrounded(result.get('cbm')),
        gw_kg=_rounded(result.get('gw_kg')),
    )
    return Line(
        line_no=line_no,
        sku=_text(item.get('internal_sku'), required=True),
        product_name=_text(item.get('product_name'), required=True),
        highlights=_highlights(item),
        specs=_specs(item),
        packing=packing,
        accessories_included=_accessories(row.get('accessories', ())),
        image_refs=_image_refs(row.get('image_uids', ())),
        qty=_count(row.get('qty'), positive=True),
        unit_price=_rounded(result.get('unit_price_usd')),
        amount=_rounded(result.get('amount_usd')),
        lead_time=_text(row.get('lead_time')),
    )


def build_customer_dto(snapshot: Mapping[str, Any]) -> CustomerQuoteDTO:
    '''Project a server-frozen public snapshot into the customer whitelist.

    Contract:
      {quote_no, issue_date, valid_until,
       seller: {company, address, contact, email, whatsapp, website},
       buyer: {company, contact, country},
       shipping: {mode, incoterm, named_place},
       terms: {payment, lead_time, warranty, price_includes, validity},
       lines: [{item, qty, packing_mode,
                accessories: [{name_en, qty_per_unit}], lead_time,
                result: {packages, cbm, gw_kg, unit_price_usd, amount_usd},
                image_uids: [...]}],
       totals: {price_usd, packages, cbm, gw_kg}}

    item contains the public /api/strobes fields, including internal_sku,
    product_name, light_source, pixel_segments, dmx_modes, control_protocols,
    power_w, ip_rating, voltage, net_weight_kg, dimensions_cm, certifications,
    carton and flight_case. internal_sku is the customer SKU. The freezer must
    place the effective selected packing profile in item[packing_mode], using
    units_per_pkg, l_cm, w_cm, h_cm and gw_kg. Unknown packing descriptions
    remain None or empty text; unknown specifications produce no entries.

    Dates and required numeric results must already be known. No prices,
    quantities, specifications, certifications or dates are inferred. Monetary
    values and weights use round2; supplied CBM values retain their precision.
    Images come exclusively from image_uids, whose customer_safe status must
    already have been checked by the caller. No asset URL is copied. Rendered
    output still requires the separate leak scan before delivery.
    '''
    root = _object(snapshot)
    seller = _object(root.get('seller'))
    buyer = _object(root.get('buyer'))
    shipping = _object(root.get('shipping'))
    terms = _object(root.get('terms'))
    totals = _object(root.get('totals'))
    mode = _text(shipping.get('mode'), required=True)
    if mode not in _SHIPPING_LABELS:
        raise ValueError('Unknown shipping mode')
    return CustomerQuoteDTO(
        quote_no=_text(root.get('quote_no'), required=True),
        issue_date=_text(root.get('issue_date'), required=True),
        valid_until=_text(root.get('valid_until'), required=True),
        currency='USD',
        seller=Seller(
            company=_text(seller.get('company')),
            address=_text(seller.get('address')),
            contact=_text(seller.get('contact')),
            email=_text(seller.get('email')),
            whatsapp=_text(seller.get('whatsapp')),
            website=_text(seller.get('website')),
        ),
        buyer=Buyer(
            company=_text(buyer.get('company')),
            contact=_text(buyer.get('contact')),
            country=_text(buyer.get('country')),
        ),
        incoterm=_text(shipping.get('incoterm'), required=True),
        named_place=_text(shipping.get('named_place')),
        shipping_mode_label=_SHIPPING_LABELS[mode],
        lines=tuple(_line(row, index) for index, row in enumerate(_sequence(root.get('lines')), 1)),
        totals=Totals(
            total_amount=_rounded(totals.get('price_usd')),
            packages=_count(totals.get('packages')),
            cbm=_unrounded(totals.get('cbm')),
            gw_kg=_rounded(totals.get('gw_kg')),
        ),
        terms=Terms(
            payment=_text(terms.get('payment')),
            lead_time=_text(terms.get('lead_time')),
            warranty=_text(terms.get('warranty')),
            price_includes=_text(terms.get('price_includes')),
            validity=_text(terms.get('validity')),
        ),
    )
