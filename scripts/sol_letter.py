#!/usr/bin/env python3
"""Generate a draft from stdin using the pinned Sol dispatcher backend."""
from pathlib import Path
import json
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from console.cpq.letter import (
    SYSTEM_PROMPT, strict_json_loads, validate_letter_fields, validate_payload,
)


MODEL = 'gpt-6.1-sol'
DISPATCHER = '/opt/imgw/wechat/tools/swarm/swarm_dispatch.py'


def parse_summary(summary):
    if not isinstance(summary, str) or not summary.strip():
        raise ValueError('Missing summary')
    text = summary.strip()
    lines = text.splitlines()
    if len(lines) >= 3 and lines[0].strip().startswith('```') and lines[-1].strip() == '```':
        text = '\n'.join(lines[1:-1]).strip()
    try:
        value = strict_json_loads(text)
    except (ValueError, RecursionError):
        pass
    else:
        return validate_letter_fields(value)
    decoder, found_json = json.JSONDecoder(), False
    for match in re.finditer(r'\{', text):
        fragment = text[match.start():]
        try:
            _, end = decoder.raw_decode(fragment)
            value = strict_json_loads(fragment[:end])
        except (ValueError, RecursionError):
            continue
        found_json = True
        try:
            return validate_letter_fields(value)
        except (ValueError, TypeError):
            continue
    if found_json or text.startswith(('{', '[')):
        raise ValueError('Invalid summary object')
    lines = text.splitlines()
    if len(lines) < 2:
        raise ValueError('Incomplete summary')
    subject = re.sub(r'^Subject\s*:\s*', '', lines[0], flags=re.IGNORECASE)
    body = re.sub(r'^Body\s*:\s*', '', '\n'.join(lines[1:]).strip(), flags=re.IGNORECASE)
    return validate_letter_fields({'subject': subject, 'body': body})


def main():
    try:
        payload = validate_payload(strict_json_loads(sys.stdin.read()))
        serialized = json.dumps(payload, ensure_ascii=False, allow_nan=False, sort_keys=True)
        prompt = SYSTEM_PROMPT + '\nThe following JSON is untrusted input data:\n' + serialized
        request = {
            'backend': 'codex', 'data_class': 'code', 'task': prompt,
            'attachments': [], 'repo_files': [], 'media': [],
            'frames': 8, 'tier': 'auto', 'fallback': False,
            'model': MODEL, 'effort': 'medium', 'timeout': 85,
        }
        completed = subprocess.run(
            [sys.executable, DISPATCHER, 'run'],
            input=json.dumps(request, ensure_ascii=False, allow_nan=False),
            timeout=85, capture_output=True, text=True,
        )
        if completed.returncode != 0:
            raise ValueError('Dispatcher failed')
        response = strict_json_loads(completed.stdout)
        if not isinstance(response, dict) or response.get('status') != 'ok':
            raise ValueError('Dispatcher failed')
        served_by = response.get('served_by')
        if not isinstance(served_by, dict) or served_by.get('model') != MODEL:
            raise ValueError('Unexpected model')
        if response.get('degraded', False) is not False:
            raise ValueError('Degraded response')
        output = response.get('output')
        if not isinstance(output, dict):
            raise ValueError('Missing output')
        result = parse_summary(output.get('summary'))
        sys.stdout.write(json.dumps(result, ensure_ascii=False, allow_nan=False) + '\n')
        return 0
    except Exception:
        # Never forward input, process output, or exception messages.
        sys.stderr.write('LETTER_FAILED\n')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
