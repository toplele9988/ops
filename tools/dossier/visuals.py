#!/usr/bin/env python3
"""Prepare private contact sheets and local OCR candidates, never network OCR."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

sys.dont_write_bytecode = True


def contact_sheets(items, out, prefix, batch=24):
    from PIL import Image, ImageOps, ImageDraw, ImageFont
    font = ImageFont.truetype('/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc', 16)
    out.mkdir(exist_ok=True, parents=True)
    for offset in range(0, len(items), batch):
        group = items[offset:offset + batch]
        canvas = Image.new('RGB', (1600, 6 * 245), 'white')
        draw = ImageDraw.Draw(canvas)
        for i, x in enumerate(group):
            col, row = i % 4, i // 4
            try:
                with Image.open(x['path']) as img:
                    thumb = ImageOps.contain(img.convert('RGB'), (390, 210))
                    canvas.paste(thumb, (col * 400 + (400 - thumb.width) // 2, row * 245))
            except Exception:
                pass
            draw.text((col * 400 + 4, row * 245 + 212), x['id'], font=font, fill='black')
        canvas.save(out / f'{prefix}-{offset // batch + 1:02}.jpg', quality=90)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--intake', type=Path, required=True)
    ap.add_argument('--ocr', action='store_true')
    ap.add_argument('--manifest', type=Path)
    ap.add_argument('--prefix', default='nas')
    a = ap.parse_args()
    if 'private_out' not in a.intake.resolve().parts:
        raise ValueError('private_output_required')
    os.umask(0o077)
    if a.manifest:
        items = json.loads(a.manifest.read_text())
        for item in items:
            item.setdefault('sha256', hashlib.sha256(Path(item['path']).read_bytes()).hexdigest())
    else:
        files = json.loads((a.intake / 'files.private.json').read_text())
        items = [x for x in files if x['status'] == 'image_pending']
    contact_sheets(items, a.intake / 'visual', a.prefix)
    if a.ocr:
        from rapidocr_onnxruntime import RapidOCR
        engine = RapidOCR(intra_op_num_threads=2, inter_op_num_threads=1)
        out = a.intake / 'ocr'
        out.mkdir(exist_ok=True)
        for item in items:
            target = out / (item['sha256'] + '.json')
            if target.exists():
                continue
            try:
                result, _ = engine(item['path'])
                obj = {'state': 'candidate_requires_visual_check', 'lines': [{'bbox': r[0], 'text': r[1], 'confidence': r[2]} for r in result or []]}
            except Exception as e:
                obj = {'state': 'failed', 'reason': type(e).__name__}
            target.write_text(json.dumps(obj, ensure_ascii=False, indent=2))
        print(json.dumps({'images': len(items), 'ocr': len(list(out.glob('*.json')))}))


if __name__ == '__main__':
    main()
