#!/bin/bash
# Execute the existing smoke runner with production static install/pkill intercepted.
# No sudo or production mutation is allowed through this wrapper.
set -euo pipefail
root=$(cd -- "$(dirname -- "$0")/../.." && pwd)
cd "$root"
export IMGW_REHEARSAL=1 REHEARSAL_PORT=${REHEARSAL_PORT:-18913}
[ "$REHEARSAL_PORT" != 8791 ] || exit 2
export OUT="$root/private_out/r11/real-smoke"
export R11_REHEARSAL_PID_FILE="$root/private_out/r11/rehearsal.pid"
mkdir -p "$OUT"
sudo() {
  [ "$1" = '-n' ] || return 99
  shift
  case "$1" in
    rm|cp|chown|find)
      # The original script's static publication is intentionally skipped;
      # rehearsal_server serves this workspace's web/dist directly.
      case "$*" in
        'rm -rf /var/lib/imgw/ops-app/dist/assets'|'cp -a web/dist/. /var/lib/imgw/ops-app/dist/'|'chown -R imgw:imgw /var/lib/imgw/ops-app/dist'|'find /var/lib/imgw/ops-app/dist -type f -exec chmod 0640 {} ;') return 0 ;;
        *) echo 'refusing unexpected sudo static action' >&2; return 99 ;;
      esac ;;
    pkill)
      if [ -f "$R11_REHEARSAL_PID_FILE" ]; then
        local pid
        pid=$(cat "$R11_REHEARSAL_PID_FILE")
        if [ -r "/proc/$pid/cmdline" ] && tr '\0' ' ' < "/proc/$pid/cmdline" | rg -q 'scripts/rehearsal_server.py'; then kill "$pid"; fi
        rm -f "$R11_REHEARSAL_PID_FILE"
      fi
      return 0 ;;
    -u)
      [ "$2" = imgw ] && [ "$3" = env ] || return 99
      shift 3
      bash -c 'echo "$$" > "$R11_REHEARSAL_PID_FILE"; exec env PYTHONDONTWRITEBYTECODE=1 "$@"' r11 "$@" ;;
    *) echo 'refusing unexpected sudo command' >&2; return 99 ;;
  esac
}
mktemp() { command mktemp "${OUT%/real-smoke}/logs/rehearsal-server.XXXXXX"; }
export -f sudo mktemp
trap 'sudo -n pkill -u imgw -f rehearsal-cleanup >/dev/null 2>&1 || true' EXIT
bash scripts/e2e_smoke.sh
