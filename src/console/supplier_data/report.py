"""Offline JSON and self-contained HTML reports. All source text is escaped."""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile

from .catalog_reader import read_products
from .completeness import (CONFLICT_REASON_LABELS, CONFLICT_TOLERANCE,
                           MULTI_MODEL_TOLERANCE, SCORE_WEIGHTS, aggregate_facts, assess)
from .followup import build_followups, product_label
from .packing_extractor import (INFER_MM_MIN, MAX_SIDE_CM, MAX_WEIGHT_KG,
                                MIN_SIDE_CM, MIN_WEIGHT_KG, extract_packing_facts)
from .volumetric import DEFAULT_VOLUMETRIC_DIVISOR
from .report_html import STATUS_LABELS, render_html


NOTICE = '抽取结果须人工核对，非已确认规格'


def supplier_coverage(records):
    """Lowest coverage first. Rates are fractions; conflict still has evidence."""
    grouped = {}
    for product in records:
        supplier = product['supplier']
        entry = grouped.setdefault(supplier, {
            'supplier': supplier, 'total': 0, 'with_packing': 0,
            'without_packing': 0, 'ddp_estimable': 0, 'ddp_ready': 0,
        })
        entry['total'] += 1
        present = bool(product['facts'])
        entry['with_packing'] += present
        entry['without_packing'] += not present
        for key in ('ddp_estimable', 'ddp_ready'):
            entry[key] += product['completeness'][key]
    for entry in grouped.values():
        entry['coverage_rate'] = entry['with_packing'] / entry['total']
    return sorted(grouped.values(), key=lambda entry: (entry['coverage_rate'], entry['supplier']))


def build_report(products):
    """Consume catalog products and produce a JSON-serializable report."""
    records = []
    supplier_products = defaultdict(list)
    for product in products:
        facts = []
        dropped = ambiguous = 0
        for chunk in product['chunks']:
            extracted = extract_packing_facts(chunk['text'], chunk_id=chunk['chunk_id'])
            facts.extend(extracted)
            dropped += extracted.dropped_out_of_range
            ambiguous += extracted.dropped_ambiguous
        aggregated = aggregate_facts(facts)
        completeness = assess(aggregated)
        supplier = str(product.get('supplier') or '未注明供应商')
        metadata = {key: value for key, value in product.items() if key != 'chunks'}
        metadata['supplier'] = supplier
        metadata['source_file'] = str(product.get('source_file') or '')
        metadata['source_type'] = str(product.get('source_type') or '')
        metadata['label'] = product_label(product)
        metadata['facts'] = {kind: asdict(group) for kind, group in aggregated.items()}
        # Full context is separate from each candidate's exact matched raw span.
        metadata['source_chunks'] = [dict(chunk) for chunk in product['chunks']]
        metadata['completeness'] = asdict(completeness)
        metadata['dropped_out_of_range'] = dropped
        metadata['dropped_ambiguous'] = ambiguous
        records.append(metadata)
        supplier_products[supplier].append({**product, 'facts': aggregated})
    statuses = Counter(record['completeness']['status'] for record in records)
    return {
        'schema_version': 2,
        'generated_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'notice': NOTICE,
        'rules': {
            'conflict_relative_tolerance': CONFLICT_TOLERANCE,
            'multi_model_relative_tolerance': MULTI_MODEL_TOLERANCE,
            'conflict_relative_base': 'smaller absolute value; ordered axes',
            'dimension_range_cm': [MIN_SIDE_CM, MAX_SIDE_CM],
            'weight_range_kg': [MIN_WEIGHT_KG, MAX_WEIGHT_KG],
            'missing_dimension_unit': f'all axes >= {INFER_MM_MIN:g}: mm; otherwise cm',
            'score_weights': SCORE_WEIGHTS,
            'volumetric_divisor_default': DEFAULT_VOLUMETRIC_DIVISOR,
            'volumetric_divisor_note': '仅为 M2 小工具默认参数，须按物流渠道确认；本报告不计算运费',
            'packing_scope': '按 equipment_id 聚合；外箱与航空箱不互相替代，需人工确认重量和装箱数属于同一包装方案',
        },
        'summary': {
            'total': len(records),
            'ddp_ready': sum(record['completeness']['ddp_ready'] for record in records),
            'ddp_estimable': statuses['estimable'],
            'with_missing': sum(bool(record['completeness']['missing']) for record in records),
            'with_conflict': statuses['conflict'],
            'by_conflict_reason': {
                reason: sum(record['completeness']['conflict_reason'] == reason for record in records)
                for reason in CONFLICT_REASON_LABELS
            },
            'by_status': {status: statuses[status] for status in STATUS_LABELS},
            'dropped_out_of_range': sum(record['dropped_out_of_range'] for record in records),
            'dropped_ambiguous': sum(record['dropped_ambiguous'] for record in records),
        },
        'products': records,
        'supplier_coverage': supplier_coverage(records),
        'followups': {supplier: build_followups(entries)
                      for supplier, entries in supplier_products.items()},
    }


def write_report(catalog_path, out_dir):
    """Read the catalog and atomically replace two report files, never the DB."""
    source = Path(catalog_path).expanduser().resolve()
    directory = Path(out_dir).expanduser().resolve()
    targets = [directory / 'packing_report.json', directory / 'packing_report.html']
    for target in targets:
        if target.resolve() == source or (target.exists() and source.exists()
                                          and os.path.samefile(source, target)):
            raise ValueError('报告输出与输入库为同一文件，拒绝覆盖输入库')
    report = build_report(read_products(source))
    # Generate both documents before creating or replacing any output file.
    documents = [json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n',
                 render_html(report)]
    directory.mkdir(parents=True, exist_ok=True)
    for target, document in zip(targets, documents):
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=directory,
                                             prefix='.m1-', delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(document)
            temporary.replace(target)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
    return report
