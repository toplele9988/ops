#!/bin/sh
# Supply explicitly approved channel=unit pairs. No logs or service configuration are read.
set -eu
output=${PIPELINE_FAILED_FILE:-/var/lib/imgw/ops-app/systemd-failed.json}
[ "$#" -gt 0 ] || { echo '请指定渠道和单元，例如 wechat=example.service' >&2; exit 2; }
tmp=$(mktemp "${output}.tmp.XXXXXXXX")
trap 'rm -f "$tmp"' EXIT HUP INT TERM
chmod 600 "$tmp"
python3 - "$tmp" "$@" <<'PY'
import json, re, subprocess, sys, time
items = []
if len(sys.argv) > 102:
    raise SystemExit('单元数量过多')
for arg in sys.argv[2:]:
    channel, sep, unit = arg.partition('=')
    if not sep or channel not in ('wechat', 'whatsapp', 'alibaba') or not re.fullmatch(r'[A-Za-z0-9_.@:-]{1,160}\.(?:service|timer)', unit):
        raise SystemExit('渠道或单元名称无效')
    def show(prop):
        return subprocess.check_output(['systemctl', 'show', '--value', '--property=' + prop, '--', unit], text=True, timeout=10).strip()
    if show('LoadState') != 'loaded':
        raise SystemExit('无法读取指定单元')
    if show('ActiveState') != 'failed':
        continue
    result, code = show('Result'), show('ExecMainStatus')
    if not re.fullmatch(r'[a-z0-9_-]{1,40}', result) or not code.isascii() or not code.isdigit() or int(code) > 255:
        raise SystemExit('单元状态格式无效')
    # systemd does not expose a dedicated failure timestamp here.
    failed_at = None
    items.append(dict(channel=channel, unit=unit, failed_at=failed_at, result=result, exit_code=int(code)))
with open(sys.argv[1], 'w', encoding='utf8') as stream:
    json.dump(dict(schema='pipeline_failed_units_v1', generated_at=time.time(), items=items), stream)
PY
mv -f -- "$tmp" "$output"
