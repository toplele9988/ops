#!/usr/bin/env python3
"""Materialize reviewed private decisions; never automatically promote old guesses."""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from decimal import Decimal
from prepare import dump, readonly


def canonical(x):
    return json.dumps(x, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def identifier(prefix, x):
    return prefix + hashlib.sha256(canonical(x).encode()).hexdigest()[:16]


def insert(db, table, values):
    columns = ','.join('"' + k + '"' for k in values)
    db.execute(f'INSERT INTO "{table}" ({columns}) VALUES ({",".join("?" for _ in values)})', list(values.values()))


def materialize(out, decisions):
    os.umask(0o077)
    out.mkdir(parents=True, exist_ok=True)
    target = out / 'dossiers.sqlite3'
    staging = out / 'dossiers.building.sqlite3'
    if staging.exists():
        staging.unlink()
    db = sqlite3.connect(staging)
    db.executescript((Path(__file__).parent / 'schema.sql').read_text())
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    with db:
        insert(db, 'metadata', {'key': 'schema_revision', 'value': 'dossier-v1'})
        insert(db, 'metadata', {'key': 'built_at', 'value': now})
        insert(db, 'metadata', {'key': 'reviewer', 'value': 'Codex; private review decisions, no remote OCR'})
        for table in ['source','supplier','product','supplier_product','offer','evidence','gap','legacy_map','merge_event']:
            for row in decisions.get(table, []):
                vals = dict(row)
                for k, v in vals.items():
                    if k.endswith('_json') and not isinstance(v, str):
                        vals[k] = canonical(v)
                if table == 'offer' and vals['amount_decimal'] is not None:
                    if not Decimal(vals['amount_decimal']).is_finite() or Decimal(vals['amount_decimal']) <= 0:
                        raise ValueError('invalid_amount')
                insert(db, table, vals)
        failures = list(db.execute('PRAGMA foreign_key_check'))
        if failures:
            raise ValueError('foreign_key_failure')
    if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
        raise ValueError('integrity_failure')
    db.close()
    staging.replace(target)
    return target


def render(target, out):
    manifest_path = out / 'dossier-manifest.json'
    previous = json.loads(manifest_path.read_text()) if manifest_path.exists() else []
    def read_json(x):
        return json.loads(x)
    def display(x):
        return '未知' if x is None or x == '' else str(x).replace('|','／').replace('\n',' ')
    with readonly(target) as db:
        suppliers = [dict(r) for r in db.execute('SELECT * FROM supplier ORDER BY supplier_id')]
        names = {s['supplier_id']: s['name'] for s in suppliers}
        src = {r['source_id']: dict(r) for r in db.execute('SELECT * FROM source')}
        def ref(source_id, locator=None):
            r = src[source_id]
            label = r['kind'] + ':' + r['external_key']
            if r['file_sha256']:
                label += ' SHA256=' + r['file_sha256']
            return '`' + label + ((' @ ' + locator) if locator else '') + '`'
        def facts(kind, ident):
            return [dict(r) for r in db.execute('SELECT * FROM evidence WHERE entity_kind=? AND entity_id=? ORDER BY field_path,evidence_id', (kind, ident))]
        def ev_lines(kind, ident):
            return ['- ' + e['field_path'] + '：' + display(e['excerpt']) + '。出处：' + ref(e['source_id'], e['locator']) + '；状态：' + e['evidence_state'] for e in facts(kind, ident)]
        (out / 'suppliers').mkdir(exist_ok=True)
        (out / 'products').mkdir(exist_ok=True)
        products = [dict(r) for r in db.execute('SELECT * FROM product ORDER BY product_family,product_id')]
        for s in suppliers:
            lines = ['# ' + s['supplier_id'] + ' ' + s['name'], '', '私有／供应商资料；历史事实，业务使用前核对报价条件。', '',
                '类型：' + s['supplier_type'], '主营品类：' + '、'.join(read_json(s['categories_json'])),
                '别名：' + '、'.join(read_json(s['aliases_json'])), '可靠度备注：' + s['reliability_notes'], '', '## 身份、联系渠道及依据', '']
            lines += ev_lines('supplier', s['supplier_id'])
            lines += ['', '## 产品与报价', '', '|产品族|型号／名称|配置|报价数|', '|---|---|---|---|']
            for p in db.execute('SELECT p.*,sp.model_aliases_json FROM product p JOIN supplier_product sp USING(product_id) WHERE sp.supplier_id=? ORDER BY p.product_family,p.product_id', (s['supplier_id'],)):
                n = db.execute('SELECT count(*) FROM offer WHERE supplier_id=? AND product_id=?',(s['supplier_id'],p['product_id'])).fetchone()[0]
                lines.append(f"|[{p['product_family']}](../products/{p['product_family']}.md)|{display(p['display_name'])}|{display(p['variant_label'])}|{n if n else '未报价'}|")
            lines += ['', '## 待补资料', '']
            for g in db.execute('SELECT * FROM gap WHERE supplier_id=? ORDER BY priority,gap_id', (s['supplier_id'],)):
                lines.append('- ' + g['ask_text'] + '（' + g['impact'] + '）')
            (out / 'suppliers' / (s['supplier_id'] + '.md')).write_text('\n'.join(lines) + '\n')
        families = {}
        for p in products:
            families.setdefault(p['product_family'], []).append(p)
        for family, variants in families.items():
            lines = ['# ' + family + ' ' + variants[0]['display_name'], '', '品类：' + variants[0]['category'], '',
                '配置分别保存；未知参数不作为相同配置的证据。标注候选的旧记录不可视为已核实规格。', '']
            for p in variants:
                lines += ['## ' + p['product_id'] + ' ' + p['variant_label'], '', '型号：' + display(p['canonical_model']), '证据状态：' + p['evidence_state'], '', '配置原文：', '', '```json', json.dumps(read_json(p['config_json']),ensure_ascii=False,indent=2), '```', '']
                lines += ev_lines('product',p['product_id'])
                lines += ['', '供应商型号别名：', '']
                for sp in db.execute('SELECT * FROM supplier_product WHERE product_id=?', (p['product_id'],)):
                    lines.append('- ' + names[sp['supplier_id']] + '：' + '、'.join(read_json(sp['model_aliases_json'])))
                lines += ['', '报价条目：', '', '|编号|供应商|金额|币种|税制|MOQ|数量条件|交期|日期依据／有效期|状态|出处|', '|---|---|---|---|---|---|---|---|---|---|---|']
                offers = list(db.execute('SELECT * FROM offer WHERE product_id=? ORDER BY supplier_id,quoted_on,offer_id', (p['product_id'],)))
                if not offers:
                    lines += ['|—|—|未报价|未知|未知|未知|未知|未知|未知|未报价|—|']
                for o in offers:
                    date = display(o['quoted_on']) + '（' + o['quoted_on_basis'] + '）／' + display(o['valid_until'])
                    lines.append('|' + '|'.join(display(x) for x in [o['offer_id'],names[o['supplier_id']],o['amount_raw'],o['currency'],o['tax_basis'],o['moq_text'],o['quantity_condition'],o['lead_time_text'],date,o['quote_state'],ref(o['source_id'],o['locator'])]) + '|')
                for o in offers:
                    lines += ['', '#### ' + o['offer_id'], '']
                    lines += ['', '包装：' + o['packing_json'], '条件：' + o['conditions_json'], ''] + ev_lines('offer',o['offer_id']) + ['']
            (out / 'products' / (family + '.md')).write_text('\n'.join(lines) + '\n')
        counts = {t: db.execute('SELECT count(*) FROM ' + t).fetchone()[0] for t in ['source','supplier','product','offer','gap','evidence','legacy_map','merge_event']}
        counts['product_families'] = len(families)
        dump(out / 'counts.json', counts)
        current = ['suppliers/' + s['supplier_id'] + '.md' for s in suppliers]
        current += ['products/' + family + '.md' for family in families]
        for relative in set(previous) - set(current):
            path = out / relative
            if path.parent in {out / 'suppliers', out / 'products'} and path.suffix == '.md':
                path.unlink(missing_ok=True)
        dump(manifest_path, sorted(current))
    return counts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--decisions', type=Path, required=True)
    ap.add_argument('--out', type=Path, required=True)
    a = ap.parse_args()
    if 'private_out' not in a.out.resolve().parts or 'private_out' not in a.decisions.resolve().parts:
        raise ValueError('private_paths_required')
    d = json.loads(a.decisions.read_text())
    target = materialize(a.out, d)
    print(canonical(render(target, a.out)))


if __name__ == '__main__':
    main()
