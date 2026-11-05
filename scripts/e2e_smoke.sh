#!/bin/bash
# Full-page smoke test on real read-only data. Run before every deploy.
# Starts the rehearsal server (as imgw, so it can read the real stores), opens every page in a real browser, reports.
set -u
cd "$(dirname "$0")/.."
PORT=${REHEARSAL_PORT:-18902}
WEB=web
LOG=$(mktemp)
(cd "$WEB" && npm run build >/dev/null 2>&1) || { echo "前端构建失败"; exit 2; }
# The rehearsal server serves web/dist directly; publishing is part of installation.
if [ "${SMOKE_PUBLISH:-0}" = 1 ]; then
  sudo -n rm -rf /var/lib/imgw/ops-app/dist/assets
  sudo -n cp -a "$WEB/dist/." /var/lib/imgw/ops-app/dist/ && sudo -n chown -R imgw:imgw /var/lib/imgw/ops-app/dist
  sudo -n find /var/lib/imgw/ops-app/dist -type f -exec chmod 0640 {} \;
fi
sudo -n pkill -u imgw -f "^/opt/imgw/venv/bin/python scripts/rehearsal_server.py" 2>/dev/null; sleep 1
(sudo -n -u imgw env IMGW_REHEARSAL=1 REHEARSAL_PORT=$PORT PYTHONPATH="${SMOKE_PYTHONPATH:-$PWD/src:/opt/imgw/wechat}" /opt/imgw/venv/bin/python scripts/rehearsal_server.py >"$LOG" 2>&1 &)
for i in $(seq 1 60); do grep -q REHEARSAL_READY "$LOG" 2>/dev/null && break; sleep 1; done
grep -q REHEARSAL_READY "$LOG" || { echo "演练服务没有启动："; tail -5 "$LOG"; exit 2; }
sleep 12   # let the knowledge index warm up like it does in production
(cd "$WEB" && BASE=http://127.0.0.1:$PORT OUT="${OUT:-}" node e2e/smoke.mjs)
CODE=$?
sudo -n pkill -u imgw -f "^/opt/imgw/venv/bin/python scripts/rehearsal_server.py" 2>/dev/null
rm -f "$LOG"
exit $CODE
