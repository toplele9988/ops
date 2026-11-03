#!/bin/sh
# 只由总指挥安装后调用；默认只读，运行模式必须显式指定。
set -eu
umask 077
KB_RECOVERY_HOME=${OPS_KB_COORDINATOR_HOME:-/opt/imgw/kb-recovery-coordinator}
KB_RECOVERY_PYTHON=${OPS_KB_COORDINATOR_PYTHON:-/opt/imgw/venv/bin/python}
export PYTHONDONTWRITEBYTECODE=1
exec "$KB_RECOVERY_PYTHON" "$KB_RECOVERY_HOME/coordinator.py" "$@"
