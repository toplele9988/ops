'''Leak inspection tests independent of quote computation and rendering engines.'''
from contextlib import closing
from dataclasses import FrozenInstanceError, asdict, fields
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import sqlite3
from zipfile import ZipFile

import pytest
from PIL import Image
from pypdf import PdfWriter

from console.cpq import leak_guard
from console.cpq.leak_guard import Hit, LeakContext, load_leak_context, scan, scan_pdf_bytes, scan_zip_bytes


pytestmark = pytest.mark.export

# All identifiers and amounts in this context are synthetic detection vectors.
CTX = LeakContext(
    supplier_names=('Example Lighting Co., Ltd',),
    factory_models=('FACTORY-4400', 'ABC'),
    cost_values=(280.0, 1280.0, 6.5),
)


def _pdf(metadata):
    output = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.add_metadata(metadata)
    writer.write(output)
    return output.getvalue()


def _jpeg(exif=None):
    output = BytesIO()
    with Image.new('RGB', (8, 8), 'white') as picture:
        if exif is None:
            picture.save(output, format='JPEG')
        else:
            picture.save(output, format='JPEG', exif=exif)
    return output.getvalue()


def _zip(entries):
    output = BytesIO()
    with ZipFile(output, 'w') as archive:
        for filename, data in entries:
            archive.writestr(filename, data)
    return output.getvalue()


@pytest.mark.parametrize('category,text', [
    ('supplier_name', 'Example Lighting Co., Ltd'),
    ('factory_model', 'factory-4400'),
    ('cny_marker', 'CNY'),
    ('cost_value', 'USD 280.00'),
    ('internal_key', 'cost_basis'),
    ('pdf_meta', ''),
], ids=['supplier_name', 'factory_model', 'cny_marker', 'cost_value', 'internal_key', 'pdf_meta'])
def test_scan_detects_each_category(category, text):
    hits = scan_pdf_bytes(_pdf({'/Author': CTX.supplier_names[0]}), CTX) if category == 'pdf_meta' else scan(text, CTX)
    assert any(hit.category == category for hit in hits)
    if category != 'pdf_meta':
        assert any(hit.offset == (4 if category == 'cost_value' else 0) for hit in hits if hit.category == category)
    if category == 'supplier_name':
        for variant in ('examplelightingco.,ltd', 'EXAMPLE   LIGHTING\tCO., LTD'):
            assert any(hit.category == category and hit.offset == 2 for hit in scan('! ' + variant, CTX))
    if category == 'factory_model':
        assert scan('ABC', CTX) == []
        overlap = LeakContext((), ('ABAB',), ())
        assert scan('ABABAB', overlap) == [Hit('factory_model', 0), Hit('factory_model', 2)]
    if category == 'cny_marker':
        for marker in ('¥', '￥', 'CNY', 'RMB', '元', '人民币'):
            assert any(hit.category == category for hit in scan(marker, CTX))
    if category == 'internal_key':
        for key in ('supplier_id', 'offer_id', '底价', '出厂', '成本', '供应商', '毛利', '/media/a/example'):
            assert any(hit.category == category for hit in scan(key, CTX))


def test_scan_hits_have_no_original_text():
    assert tuple(field.name for field in fields(Hit)) == ('category', 'offset')
    text = 'prefix factory-4400'
    hits = scan(text, CTX)
    assert hits == [Hit('factory_model', 7)]
    for hit in hits:
        assert set(asdict(hit)) == {'category', 'offset'}
        assert not hasattr(hit, '__dict__')
        assert 'factory-4400' not in repr(hit)
        assert isinstance(hit.offset, int)
        with pytest.raises(FrozenInstanceError):
            hit.offset = 0
    shared = LeakContext(('FACTORY-4400',), ('FACTORY-4400',), ())
    assert {(hit.category, hit.offset) for hit in scan('factory-4400', shared)} == {
        ('supplier_name', 0), ('factory_model', 0),
    }


@pytest.mark.parametrize('text,expected', [
    ('Qty 280 pcs', False),
    ('USD 280.00', True),
    ('¥1,280', True),
    ('USD 1,280.00', True),
    ('USD1280', True),
    ('$280', True),
    ('280USD', True),
    ('US$280.00', True),
    ('€6.50', True),
    ('USD 280.00, delivered', True),
    ('USD 280.000', True),
    ('USD 280.001', False),
    ('USD 12800', False),
    ('USD 12,280.00', False),
    ('USD 1,280,000', False),
    ('USD 1280.50', False),
    ('USD' + 'x' * 12 + '280.00', True),
    ('USD' + 'x' * 13 + '280.00', False),
    ('280.00' + 'x' * 12 + 'USD', True),
    ('280.00' + 'x' * 13 + 'USD', False),
])
def test_cost_value_requires_currency_context(text, expected):
    assert any(hit.category == 'cost_value' for hit in scan(text, CTX)) is expected


def test_incoterm_words_allowed():
    for text in (
        'DDP Hamburg, EXW terms apply',
        'FOB Shanghai',
        'CIF Hamburg; DAP Berlin; EXW',
        'Samsung lighting; margin notes; margins',
        'preexw: 412; premargin: notes',
    ):
        assert scan(text, CTX) == []
    for text in (
        'exw_cny: 412', 'EXW: 412', 'exw 412', 'exw412',
        'margin_pct', 'margin: 20', 'margin %', 'gross margin', 'profit margin',
    ):
        assert any(hit.category == 'internal_key' for hit in scan(text, CTX))


@pytest.mark.parametrize('key', ['/Title', '/Author', '/Creator', '/Producer', '/Subject', '/Keywords'])
def test_scan_pdf_metadata(key):
    supplier = CTX.supplier_names[0]
    hits = scan_pdf_bytes(_pdf({key: supplier}), CTX)
    assert hits
    assert {hit.category for hit in hits} == {'pdf_meta'}
    assert all(hit.offset >= 1 for hit in hits)
    assert supplier not in repr(hits)
    assert scan_pdf_bytes(_pdf({'/Author': 'Customer export', '/Title': 'DDP Hamburg'}), CTX) == []
    assert any(hit.category == 'pdf_meta' for hit in scan_pdf_bytes(_pdf({key: 'USD 280.00'}), CTX))
    with pytest.raises(ValueError, match='^PDF inspection failed$'):
        scan_pdf_bytes(b'not a PDF', CTX)


def test_scan_zip_filenames_and_exif():
    clean_image = _jpeg()
    assert scan_zip_bytes(_zip([('images/customer.jpg', clean_image)]), CTX) == []
    filename_hits = scan_zip_bytes(_zip([(CTX.supplier_names[0] + '.jpg', clean_image)]), CTX)
    assert any(hit.category == 'supplier_name' and hit.offset == 0 for hit in filename_hits)
    # Non-image members and directory names must also be inspected.
    for entry in (('supplier_id.txt', b'plain text'), ('RMB/', b'')):
        assert scan_zip_bytes(_zip([entry]), CTX)
    chinese_supplier = '测试灯光有限公司'
    chinese_context = LeakContext((chinese_supplier,), (), ())
    examples = (
        (315, CTX.supplier_names[0], CTX),
        (270, 'factory-4400', CTX),
        (37510, b'ASCII\x00\x00\x00' + CTX.supplier_names[0].encode('ascii'), CTX),
        (40092, chinese_supplier.encode('utf-16-le') + b'\x00\x00', chinese_context),
    )
    for tag, value, context in examples:
        exif = Image.Exif()
        exif[tag] = value
        # The extension is deliberately unrelated to the image format.
        artifact = _zip([('images/customer.bin', _jpeg(exif))])
        hits = scan_zip_bytes(artifact, context)
        assert any(hit.category in {'supplier_name', 'factory_model'} for hit in hits)
        assert all(hit.offset >= len('images/customer.bin') + 1 for hit in hits)
    with pytest.raises(ValueError, match='^ZIP inspection failed$'):
        scan_zip_bytes(b'not a ZIP', CTX)
    with pytest.raises(ValueError, match='^ZIP inspection failed$'):
        scan_zip_bytes(_zip([('images/customer.jpg', b'broken image')]), CTX)


def test_breaker_has_no_bypass():
    source = Path(leak_guard.__file__).read_text(encoding='utf-8').lower()
    for forbidden in ('bypass', 'skip_scan', 'force', 'allow_leak', 'disable'):
        assert forbidden not in source


def test_load_leak_context_synthetic_snapshot(catalog_copy, work_db):
    catalog_path = Path(catalog_copy)
    work_path = Path(work_db)
    from console.strobe.repository import SUPPLIER_LEGAL_NAME
    supplier = SUPPLIER_LEGAL_NAME
    # Only this per-test work database is modified. The catalog is read only.
    with closing(sqlite3.connect(work_path)) as work:
        with work:
            work.execute('''
                INSERT INTO cpq_accessory(accessory_id,name_en,name_zh,cost_cny,active)
                VALUES (?,?,?,?,?)
            ''', ('C1-SYNTHETIC-INACTIVE', 'Synthetic test accessory', None, 6.5, 0))
    uri = catalog_path.absolute().as_uri() + '?mode=ro'
    with closing(sqlite3.connect(uri, uri=True)) as catalog:
        catalog.execute('PRAGMA query_only=ON')
        row = catalog.execute('''
            SELECT e.equipment_id, o.raw_cost_rmb_fen, o.fc_upgrade_rmb_fen
            FROM equipment_specs e
            JOIN cat_supplier s ON s.legal_name=e.supplier
            JOIN cat_supplier_model sm
              ON sm.supplier_id=s.supplier_id AND sm.factory_model_code=e.model
            JOIN cat_supplier_offer o
              ON o.supplier_model_id=sm.supplier_model_id AND o.supplier_id=sm.supplier_id
            WHERE e.fixture_type=? AND s.legal_name=? AND o.raw_cost_rmb_fen IS NOT NULL
            ORDER BY e.equipment_id, o.offer_id
            LIMIT 1
        ''', ('strobe', supplier)).fetchone()
    assert row is not None, 'The synthetic snapshot needs a strobe offer with a known cost'
    model_id, raw_fen, flight_fen = row
    catalog_before = sha256(catalog_path.read_bytes()).digest()
    work_before = sha256(work_path.read_bytes()).digest()
    context = load_leak_context(catalog_path, work_path, [model_id])
    assert supplier in context.supplier_names
    assert supplier in context.supplier_names
    assert len(context.factory_models) >= 30
    assert all(len(model) >= 4 for model in context.factory_models)
    assert raw_fen / 100 in context.cost_values
    if flight_fen is not None:
        assert flight_fen / 100 in context.cost_values
    assert 6.5 in context.cost_values
    assert tuple(field.name for field in fields(LeakContext)) == ('supplier_names', 'factory_models', 'cost_values')
    assert isinstance(context.supplier_names, tuple)
    assert isinstance(context.factory_models, tuple)
    assert isinstance(context.cost_values, tuple)
    assert sha256(catalog_path.read_bytes()).digest() == catalog_before
    assert sha256(work_path.read_bytes()).digest() == work_before
    with pytest.raises(ValueError, match='^Leak context unavailable$'):
        load_leak_context(catalog_path, work_path, ['equipment:c1-nonexistent-test-model'])
    missing_work = work_path.parent / 'c1-nonexistent-work.sqlite3'
    assert not missing_work.exists()
    with pytest.raises(ValueError, match='^Leak context unavailable$'):
        load_leak_context(catalog_path, missing_work, [model_id])
    assert not missing_work.exists()
