#!/bin/sh
set -eu
PRIVATE_SOURCE_ROOT=${WECHAT_PRIVATE_SOURCE_ROOT:-/opt/imgw/wechat}
export PYTHONPATH="$PRIVATE_SOURCE_ROOT"
export PYTHONDONTWRITEBYTECODE=1
exec /opt/imgw/venv/bin/python "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/manage.py" replay "$@"
