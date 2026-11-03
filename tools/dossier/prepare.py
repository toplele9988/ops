#!/usr/bin/env python3
"""Local-only read-only intake. All business output must be private.

No application store imports, network calls, macros, or formula execution.
Columns are inspected before reading; absent tables fail instead of guessing.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import zipfile

sys.dont_write_bytecode = True


def sha(data):
    return hashlib.sha256(data).hexdigest()


def file_sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1048576), b''):
            h.update(chunk)
    return h.hexdigest()


def qi(name):
    return '"' + name.replace('"', '""') + '"'


def readonly(path):
    p = Path(path).resolve(strict=True)
    wal, shm = Path(str(p) + '-wal'), Path(str(p) + '-shm')
    live = wal.exists() and wal.stat().st_size > 0
    if live and not shm.exists():
        raise RuntimeError('live_wal_without_existing_shm')
    db = sqlite3.connect(p.as_uri() + '?mode=ro' + ('' if live else '&immutable=1'), uri=True)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    db.execute('PRAGMA temp_store=MEMORY')
    db.execute('BEGIN')
    return db


def schema(db, table):
    rows = [dict(r) for r in db.execute('PRAGMA table_info(' + qi(table) + ')')]
    if not rows:
        raise RuntimeError('missing_table:' + table)
    return rows


def dump(path, obj):
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')


def parse_file(path):
    """Preserve page, paragraph/table, cells and cached values; never infer facts."""
    ext = path.suffix.lower()
    data = path.read_bytes()
    rows, limitations, images = [], [], []
    coverage = {}
    if ext == '.doc' and data[:2] == b'PK':
        ext = '.docx'
        limitations.append('doc_suffix_is_docx')
    if ext == '.txt':
        try:
            text = data.decode('utf-8-sig')
        except UnicodeError:
            text = data.decode('gb18030')
        rows = [{'locator': f'line:{i}', 'text': line} for i, line in enumerate(text.splitlines(), 1) if line.strip()]
    elif ext == '.docx':
        from docx import Document
        from docx.oxml.ns import qn
        from docx.table import Table
        from docx.text.paragraph import Paragraph
        doc = Document(path)
        for n, child in enumerate(doc.element.body, 1):
            if child.tag == qn('w:p'):
                text = Paragraph(child, doc).text
                if text.strip():
                    rows.append({'locator': f'body:{n}:paragraph', 'text': text})
            elif child.tag == qn('w:tbl'):
                table = Table(child, doc)
                for r, row in enumerate(table.rows, 1):
                    cells = [{'coordinate': f'col:{c}', 'text': cell.text} for c, cell in enumerate(row.cells, 1)]
                    rows.append({'locator': f'body:{n}:table:row:{r}', 'text': ' | '.join(x['text'] for x in cells), 'cells': cells})
        with zipfile.ZipFile(path) as z:
            images = [{'member': n, 'bytes': z.getinfo(n).file_size} for n in z.namelist() if n.startswith('word/media/')]
        if images:
            limitations.append('embedded_images_pending_visual_review')
        limitations.append('word_has_no_reliable_page_numbers_use_body_table_locator')
    elif ext == '.doc':
        import olefile
        with olefile.OleFileIO(path) as ole:
            if not ole.exists('WordDocument'):
                raise ValueError('not_word_ole')
        p = subprocess.run(['antiword', '-w', '0', str(path)], capture_output=True, timeout=60)
        if p.returncode:
            p = subprocess.run(['catdoc', '-d', 'utf-8', str(path)], capture_output=True, timeout=60)
            if p.returncode or not p.stdout.strip():
                raise ValueError('antiword_and_catdoc_failed')
            converter = 'catdoc'
            limitations.append('antiword_failed_catdoc_fallback')
            if p.stderr.strip():
                limitations.append('catdoc_warning_partial_conversion')
                coverage['conversion_warning'] = p.stderr.decode('utf-8', errors='replace')[:300]
        else:
            converter = 'antiword'
        rows = [{'locator': f'{converter}-line:{i}', 'text': t} for i, t in enumerate(p.stdout.decode('utf-8').splitlines(), 1) if t.strip()]
        limitations.extend(['binary_doc_embedded_images_not_extracted', 'antiword_table_layout_requires_review'])
    elif ext in ('.xls', '.xlsx'):
        from openpyxl.utils import get_column_letter
        sheets = []
        if ext == '.xlsx':
            import openpyxl
            formulas = openpyxl.load_workbook(path, read_only=False, data_only=False, keep_links=False)
            cached = openpyxl.load_workbook(path, read_only=False, data_only=True, keep_links=False)
            for ws in formulas.worksheets:
                sheets.append({'name': ws.title, 'merged': [str(x) for x in ws.merged_cells.ranges], 'rows_total': ws.max_row})
                for rr in ws.iter_rows():
                    cells = []
                    for c in rr:
                        if c.value is None:
                            continue
                        v = c.value
                        cv = cached[ws.title][c.coordinate].value if c.data_type == 'f' else v
                        cells.append({'coordinate': c.coordinate, 'text': str(v), 'cached': str(cv) if cv is not None else None, 'formula': c.data_type == 'f'})
                    if cells:
                        rows.append({'locator': f'sheet:{ws.title}:row:{rr[0].row}', 'text': ' | '.join(c['coordinate'] + '=' + c['text'] for c in cells), 'cells': cells})
            with zipfile.ZipFile(path) as z:
                images = [{'member': n, 'bytes': z.getinfo(n).file_size} for n in z.namelist() if n.startswith('xl/media/')]
            formulas.close()
            cached.close()
        else:
            import xlrd
            book = xlrd.open_workbook(path, on_demand=True, formatting_info=True)
            for ws in book.sheets():
                sheets.append({'name': ws.name, 'merged': ws.merged_cells, 'rows_total': ws.nrows})
                for ri in range(ws.nrows):
                    cells = [{'coordinate': f'{get_column_letter(ci + 1)}{ri + 1}', 'text': str(v)} for ci, v in enumerate(ws.row_values(ri)) if v != '']
                    if cells:
                        rows.append({'locator': f'sheet:{ws.name}:row:{ri + 1}', 'text': ' | '.join(c['coordinate'] + '=' + c['text'] for c in cells), 'cells': cells})
            book.release_resources()
            limitations.append('xls_formula_cached_values_only_embedded_images_pending')
        coverage['sheets'] = sheets
        if images:
            limitations.append('embedded_images_pending_visual_review')
    elif ext == '.pdf':
        from pypdf import PdfReader
        reader = PdfReader(path)
        if reader.is_encrypted:
            raise ValueError('encrypted_pdf')
        coverage['pages_total'] = len(reader.pages)
        coverage['pages_without_text'] = []
        for i, page in enumerate(reader.pages, 1):
            text = page.extract_text(extraction_mode='layout') or ''
            if not text.strip():
                coverage['pages_without_text'].append(i)
            rows.append({'locator': f'page:{i}', 'text': text})
        if coverage['pages_without_text']:
            limitations.append('image_pages_pending_visual_review')
        limitations.append('pdf_embedded_images_pending_visual_review')
    else:
        raise ValueError('unsupported_format')
    return {'rows': rows, 'limitations': limitations, 'coverage': coverage, 'embedded_images': images, 'parser': 'local-v1'}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--api', required=True)
    ap.add_argument('--archive', required=True)
    ap.add_argument('--catalog', required=True)
    ap.add_argument('--nas', type=Path, required=True)
    ap.add_argument('--out', type=Path, required=True)
    a = ap.parse_args()
    if 'private_out' not in a.out.resolve().parts:
        raise ValueError('output_must_be_private_out')
    os.umask(0o077)
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / 'parsed').mkdir(exist_ok=True)
    inspection = {'observed_at': dt.datetime.now(dt.timezone.utc).isoformat(), 'databases': {}}
    with readonly(a.api) as d:
        tables = ['messages', 'message_scopes', 'company_media', 'company_note_assets', 'received_callback_archive', 'outbound_callback_archive']
        inspection['databases'][a.api] = {t: schema(d, t) for t in tables}
        messages = []
        for r in d.execute("SELECT m.rowid AS intake_rowid,m.*,s.profile_id,s.scope_id,s.own_wxid AS scoped_own_wxid FROM messages m JOIN message_scopes s USING(app_id,message_id) WHERE s.business_role='supplier' ORDER BY m.group_id,m.created_at,m.message_id"):
            item = dict(r)
            p = json.loads(item.pop('payload'))
            # Do not copy download credentials or attachment XML into the dossier intake.
            item['text'] = p.get('text', '')
            own = p.get('own_wxid') or item['scoped_own_wxid']
            item['direction'] = 'outgoing' if own and item['sender_id'] == own else 'incoming_participant'
            item['attachments'] = [{k: v for k, v in x.items() if k in {'kind', 'name', 'size_bytes', 'msg_type', 'appmsg_type'}} for x in p.get('attachments', [])]
            item['payload_sha256'] = sha(r['payload'].encode())
            messages.append(item)
        dump(a.out / 'messages.private.json', messages)
        for t in ['company_media', 'company_note_assets']:
            cols = [x['name'] for x in inspection['databases'][a.api][t] if x['name'] not in {'original', 'provenance_json'}]
            items = [dict(r) for r in d.execute('SELECT ' + ','.join('c.' + qi(c) for c in cols) + ' FROM ' + qi(t) + " c JOIN message_scopes s USING(app_id,message_id) WHERE s.business_role='supplier'")]
            dump(a.out / (t + '.private.json'), items)
    with readonly(a.archive) as d:
        tables = ['parties', 'products', 'offers', 'product_sources']
        inspection['databases'][a.archive] = {t: schema(d, t) for t in tables}
        dump(a.out / 'archive.private.json', {t: [dict(r) for r in d.execute('SELECT * FROM ' + qi(t))] for t in tables})
    with readonly(a.catalog) as d:
        tables = ['equipment_specs', 'multimedia_assets', 'cat_supplier', 'cat_supplier_model', 'cat_supplier_offer', 'kb_supplier_profile']
        inspection['databases'][a.catalog] = {t: schema(d, t) for t in tables}
        dump(a.out / 'catalog.private.json', {
            'specs': [dict(r) for r in d.execute("SELECT e.* FROM equipment_specs e JOIN multimedia_assets a USING(asset_id) WHERE a.source_type='supplier'")],
            'assets': [dict(r) for r in d.execute("SELECT * FROM multimedia_assets WHERE source_type='supplier'")],
            **{t: [dict(r) for r in d.execute('SELECT * FROM ' + qi(t))] for t in tables[2:]},
        })
    dump(a.out / 'input-schemas.private.json', inspection)
    files = []
    for i, p in enumerate(sorted(a.nas.rglob('*')), 1):
        if not p.is_file() or p.is_symlink():
            continue
        item = {'path': str(p), 'relative_path': str(p.relative_to(a.nas)), 'extension': p.suffix.lower()}
        try:
            before = p.stat()
            item.update({'bytes': before.st_size, 'sha256': file_sha(p)})
            after = p.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise ValueError('changed_during_read')
            item['id'] = 'F' + str(len(files) + 1).zfill(4)
            if p.suffix.lower() in {'.txt', '.doc', '.docx', '.pdf', '.xls', '.xlsx'}:
                target = a.out / 'parsed' / (item['sha256'] + '.json')
                if not target.exists():
                    dump(target, parse_file(p))
                parsed = json.loads(target.read_text())
                item['status'] = 'text_parsed'
                item['text_chars'] = sum(len(r['text']) for r in parsed['rows'])
                item['limitations'] = parsed['limitations']
            elif p.suffix.lower() in {'.jpg', '.jpeg', '.png'}:
                item['status'] = 'image_pending'
            else:
                item['status'] = 'registered_only'
        except Exception as e:
            item['status'] = 'failed'
            item['error'] = type(e).__name__ + ':' + str(e)[:300]
        files.append(item)
        dump(a.out / 'files.private.json', files)
    print(json.dumps({'messages': len(messages), 'files': len(files), 'text_chars': sum(f.get('text_chars', 0) for f in files)}))


if __name__ == '__main__':
    main()
