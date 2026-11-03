#!/usr/bin/env python3
"""Hash-check local supplier attachments; parse Office/PDF originals privately."""
import argparse
import json
import os
from pathlib import Path
import shutil
import sys
from prepare import parse_file, file_sha, dump
from visuals import contact_sheets

sys.dont_write_bytecode = True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--intake', type=Path, required=True)
    ap.add_argument('--blob-root', type=Path, required=True)
    a = ap.parse_args()
    if 'private_out' not in a.intake.resolve().parts:
        raise ValueError('private_output_required')
    os.umask(0o077)
    originals = a.intake / 'originals'
    originals.mkdir(exist_ok=True)
    manifest, images = [], []
    for name in ['company_media', 'company_note_assets']:
        for item in json.loads((a.intake / (name + '.private.json')).read_text()):
            entry = {'id': item['review_ref'], 'archive_sha256': item['archive_sha256'], 'cached_status': item['status'], 'format': item['format']}
            h = item['archive_sha256']
            src = a.blob_root / (h + '.blob') if h else None
            if not src or not src.is_file():
                entry['state'] = 'original_missing'
            elif file_sha(src) != h:
                entry['state'] = 'hash_mismatch'
            else:
                entry['state'] = 'hash_verified'
                entry['path'] = str(src)
                if item['format'] in ['jpeg', 'png']:
                    images.append({'id': item['review_ref'], 'path': str(src)})
                elif item['format'] in ['doc', 'docx', 'pdf', 'xls', 'xlsx']:
                    target = originals / (h + '.' + item['format'])
                    if not target.exists():
                        shutil.copyfile(src, target)
                    if file_sha(target) != h:
                        raise ValueError('copied_original_hash_mismatch')
                    parsed = a.intake / 'parsed' / (h + '.json')
                    try:
                        if not parsed.exists():
                            dump(parsed, parse_file(target))
                        entry['state'] = 'original_text_parsed'
                    except Exception as e:
                        entry['state'] = 'parse_failed'
                        entry['reason'] = type(e).__name__ + ':' + str(e)[:200]
            manifest.append(entry)
    dump(a.intake / 'attachments-availability.private.json', manifest)
    dump(a.intake / 'attachment-images.private.json', images)
    contact_sheets(images, a.intake / 'visual', 'wechat')
    print(json.dumps({'attachments': len(manifest), 'readable_images': len(images), 'office_pdf_parsed': sum(x['state'] == 'original_text_parsed' for x in manifest)}))


if __name__ == '__main__':
    main()
