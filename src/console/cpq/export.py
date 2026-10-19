'''Customer documents from the public DTO and registered images.'''
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit, unquote, quote
from zipfile import ZipFile, ZIP_DEFLATED
import base64
import json
import os
import re
import warnings
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from PIL import Image, ImageOps
from pypdf import PdfReader, PdfWriter
from weasyprint.urls import URLFetcherResponse
from weasyprint import HTML
from weasyprint.text.fonts import FontConfiguration
from .dto import CustomerQuoteDTO, Seller, Buyer, Line, Packing, Spec, Totals, Terms
from console.security.media import MediaError, valid_uid

# Metadata placeholder only; the owner must complete the company profile.
DEFAULT_COMPANY = 'XL Lighting'
ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / 'templates/quote'
FONTS = ROOT / 'static/fonts'
PDF_BASE_URL = TEMPLATES.as_uri() + '/'
FONT_NAMES = ('DejaVuSans.ttf', 'DejaVuSans-Bold.ttf')
FONT_SLOT = '<style id="quote-fonts"></style>'


def _footer_note(dto) -> str:
    return ''


def load_customer_dto(raw):
    d = json.loads(raw)
    lines = []
    for row in d['lines']:
        row['packing'] = Packing(**row['packing'])
        row['specs'] = tuple(Spec(**s) for s in row['specs'])
        for key in ('highlights', 'accessories_included', 'image_refs'):
            row[key] = tuple(row[key])
        lines.append(Line(**row))
    dto = CustomerQuoteDTO(**(d | {'seller': Seller(**d['seller']), 'buyer': Buyer(**d['buyer']), 'totals': Totals(**d['totals']), 'terms': Terms(**d['terms']), 'lines': tuple(lines)}))
    if dto.currency != 'USD' or not dto.lines or not re.fullmatch(r'Q[0-9]{6}-[0-9]{3,}', dto.quote_no):
        raise ValueError('Invalid customer document')
    return dto


def known(v):
    if v is None:
        return False
    if isinstance(v, str):
        return v.strip().casefold() not in {'', 'none', 'null', 'unknown', 'n/a', 'na', '-', '0', '0.0'}
    return v > 0 if isinstance(v, (int, float, Decimal)) else bool(v)


def money(v):
    return format(Decimal(str(v)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP), ',.2f')


def number(v):
    text = format(Decimal(str(v)), 'f')
    return text.rstrip('0').rstrip('.') if '.' in text else text


def jpeg(source):
    with warnings.catch_warnings():
        warnings.simplefilter('error', Image.DecompressionBombWarning)
        with Image.open(BytesIO(source)) as original:
            original.seek(0)
            with ImageOps.exif_transpose(original) as oriented, oriented.convert('RGB') as rgb:
                rgb.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
                # Fresh pixels discard EXIF, ICC, XMP and comments.
                with Image.frombytes('RGB', rgb.size, rgb.tobytes()) as clean:
                    result = BytesIO()
                    clean.save(result, 'JPEG', quality=85)
                    return result.getvalue()


@dataclass(frozen=True)
class Resolver:
    repository: object
    store: object
    media: object
    logo_path: Path | None = None


def prepare_images(dto, resolver):
    images, flags = {}, resolver.store.asset_flags()
    for uid in dict.fromkeys(uid for line in dto.lines for uid in line.image_refs):
        if not valid_uid(uid):
            raise MediaError(404, 'NOT_FOUND')
        asset = resolver.repository.asset_by_uid(uid)
        if asset is None:
            raise MediaError(404, 'NOT_FOUND')
        if flags.get(asset.asset_id, True):
            images[uid] = jpeg(resolver.media.render(uid, asset, None).body)
    path = resolver.logo_path
    logo = jpeg(path.read_bytes()) if path is not None and path.is_file() else None
    return images, logo


def photos(line, images):
    return [images[0][uid] for uid in dict.fromkeys(line.image_refs) if uid in images[0]][:3]


def data_uri(data):
    text = base64.b64encode(data).decode('ascii')
    # Base64 whitespace preserves pixels while avoiding incidental URL-like text.
    for token in ('http', '/media/'):
        text = re.sub(re.escape(token), lambda m: m[0][:2] + '\n' + m[0][2:], text, flags=re.I)
    return 'data:image/jpeg;base64,' + text


def packing_rows(p):
    rows = [('Packing type', p.type, ''), ('Units per package', p.units_per_pkg, ''), ('Package size', p.pkg_size_cm, ' cm'), ('G.W.', p.pkg_gw_kg, ' kg/package'), ('Packages', p.packages, ''), ('CBM', p.cbm, ' CBM')]
    return [(label, (v if isinstance(v, str) else number(v)) + suffix) for label, v, suffix in rows if known(v)]


def environment(html):
    env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=html, undefined=StrictUndefined, trim_blocks=True, lstrip_blocks=True)
    env.filters.update(money=money, number=number, inline=lambda v: ' '.join(str(v).split()))
    env.globals.update(known=known, packing_rows=packing_rows)
    return env


def render_html(dto, images) -> str:
    return environment(True).get_template('customer_quote.html').render(dto=dto, photos={line.line_no: [data_uri(data) for data in photos(line, images)] for line in dto.lines}, logo=data_uri(images[1]) if images[1] else '', subtotal=sum(Decimal(str(line.amount)) for line in dto.lines), footer_note=_footer_note(dto))


def render_whatsapp(dto) -> str:
    return environment(False).get_template('whatsapp.txt.j2').render(dto=dto, footer_note=_footer_note(dto)).strip() + '\n'


class Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.hidden = [], 0
    def handle_starttag(self, tag, attrs):
        if tag in ('style', 'script'):
            self.hidden += 1
        if not self.hidden:
            self.parts.extend(v for k, v in attrs if v and (k in ('alt', 'title', 'href') or (k == 'src' and not v.startswith('data:image/'))))
    def handle_endtag(self, tag):
        if tag in ('style', 'script') and self.hidden:
            self.hidden -= 1
    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def html_text(html):
    parser = Text()
    parser.feed(html)
    parser.close()
    return '\n'.join(parser.parts)


def validate_html(html):
    if 'http' in html.lower() or '/media/' in html.lower() or re.search(r'<\s*(script|iframe|object|embed|link|base)\b', html, re.I):
        raise ValueError('Invalid document resources')


def fetch(url):
    if url.startswith('data:image/jpeg;base64,'):
        return URLFetcherResponse(url, body=base64.b64decode(''.join(url.split(',', 1)[1].split()), validate=True), headers={'Content-Type': 'image/jpeg'})
    p = urlsplit(url)
    if p.scheme == 'file' and not p.netloc and not p.query and not p.fragment:
        path = Path(unquote(p.path)).resolve(strict=True)
        if path in {(FONTS / name).resolve(strict=True) for name in FONT_NAMES}:
            return URLFetcherResponse(url, body=path.read_bytes(), headers={'Content-Type': 'font/ttf'})
    raise ValueError('Document resource unavailable')


def render_pdf(html, base_url, dto) -> bytes:
    validate_html(html)
    p = urlsplit(str(base_url))
    if p.scheme not in ('', 'file') or p.netloc or p.query or p.fragment:
        raise ValueError('Local base required')
    base = Path(unquote(p.path) if p.scheme else base_url).resolve(strict=True)
    if not base.is_dir() or html.count(FONT_SLOT) != 1:
        raise ValueError('Invalid document base')
    rules = []
    for name, weight in zip(FONT_NAMES, (400, 700), strict=True):
        relative = quote(Path(os.path.relpath((FONTS / name).resolve(strict=True), base)).as_posix(), safe='/.-')
        rules.append("@font-face {font-family:QuoteSans;font-weight:%d;src:url('%s') format('truetype')}" % (weight, relative))
    html = html.replace(FONT_SLOT, '<style>' + '\n'.join(rules) + '</style>')
    raw = HTML(string=html, base_url=base.as_uri() + '/', url_fetcher=fetch).write_pdf(font_config=FontConfiguration())
    reader, writer = PdfReader(BytesIO(raw)), PdfWriter()
    # Page-only copying excludes document-level renderer metadata and XMP.
    for page in reader.pages:
        writer.add_page(page)
    company = dto.seller.company.strip() or DEFAULT_COMPANY
    writer.add_metadata({'/Title': dto.quote_no, '/Author': company, '/Creator': company, '/Producer': company})
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def build_images_zip(dto, images) -> bytes:
    output = BytesIO()
    with ZipFile(output, 'w', compression=ZIP_DEFLATED) as archive:
        for line in dto.lines:
            sku = re.sub(r'[^A-Za-z0-9]+', '-', line.sku).strip('-')
            if not sku:
                raise ValueError('Invalid customer SKU')
            for i, data in enumerate(photos(line, images)):
                role = 'main' if i == 0 else f'detail-{i}'
                archive.writestr(f'{line.line_no:02d}_{sku}_{role}.jpg', data)
    return output.getvalue()
