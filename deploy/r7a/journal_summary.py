"""筛选 journalctl 的 JSON 标准输入，只输出固定错误码、源码位置和次数。"""
from collections import Counter
import json
import re
import sys

CODES = frozenset(('qwen_matrix_unavailable', 'digest_numeric_evidence_rejected',
    'digest_category_representative_rejected', 'model_exception', 'index_unavailable',
    'runtime_changed', 'bge_unavailable', 'authorization_revoked', 'index_root_invalid',
    'file_invalid', 'file_identity_changed', 'parent_changed', 'binding_changed',
    'binding_mismatch', 'cache_missing', 'cache_exists', 'cache_changed', 'root_changed',
    'ancestor_invalid', 'source_config_invalid', 'source_binding_invalid',
    'source_binding_changed', 'source_empty_unverified', 'name_conflict', 'repeated_page',
    'unsafe_cache', 'unsafe_root', 'refresh_unavailable'))
STATES = frozenset(('error', 'blocked', 'complete', 'model_failed', 'busy', 'dry_run', 'archived_only'))
TYPES = frozenset(('ValueError', 'RuntimeError', 'PermissionError', 'FileNotFoundError',
    'ModuleNotFoundError', 'ImportError', 'OperationalError', 'TimeoutError'))
SOURCES = frozenset((
    '/opt/imgw/scripts/knowledge_digest_generate.py',
    '/opt/imgw/video-broker/20261012/knowledge_digest/daily_worker_candidate.py',
    '/opt/imgw/video-broker/20261012/knowledge_digest/daily_pool_child.py',
    '/opt/imgw/video-broker/20261012/knowledge_digest/daily_pool_proxy.py',
    '/opt/imgw/console-waha-history-raw-v1/history_index_worker.py',
    '/opt/imgw/console-waha-history-raw-v1/history_runtime.py',
    '/opt/imgw/console-waha-history-raw-v1/history_index.py',
    '/opt/imgw/console-waha-history-raw-v1/window_entry.py',
    '/opt/imgw/waha-names-refresh-v1/capture_names_worker.py',
    '/opt/imgw/waha-names-refresh-v1/wa_names.py',
    '/opt/imgw/waha-names-refresh-v1/private_history.py',
    '/opt/imgw/waha-names-refresh-v1/private_history_entry.py',
))


def summarize(lines):
    codes, states, types, frames = Counter(), Counter(), Counter(), Counter()
    records = invalid = 0
    for line in lines:
        if len(line) > 262144:
            invalid += 1
            continue
        try:
            entry = json.loads(line)
            if not isinstance(entry, dict) or not isinstance(entry.get('MESSAGE'), str):
                raise ValueError
        except (ValueError, TypeError):
            invalid += 1
            continue
        records += 1
        message = entry['MESSAGE']
        try:
            value = json.loads(message)
        except ValueError:
            value = None
        if isinstance(value, dict):
            event_codes = {value[key] for key in ('error', 'error_code')
                           if isinstance(value.get(key), str) and value[key] in CODES}
            codes.update(event_codes)
            if isinstance(value.get('status'), str) and value['status'] in STATES:
                states[value['status']] += 1
            if isinstance(value.get('type'), str) and value['type'] in TYPES:
                types[value['type']] += 1
        for path, number in re.findall(r'File "([^"\n]+)", line (\d{1,6})', message):
            if path in SOURCES:
                frames[(path, int(number))] += 1
    return {'records': records, 'invalid': invalid, 'error_codes': dict(codes),
            'states': dict(states), 'types': dict(types),
            'frames': [dict(file=k[0], line=k[1], count=n) for k, n in sorted(frames.items())]}


def main():
    result = summarize(sys.stdin)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result['records'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
