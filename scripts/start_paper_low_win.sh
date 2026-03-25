#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

# 让 .env 里的参数自动导出给 Python 进程。
set -a
source .env
set +a

export PYTHONPATH=src

PYTHON_BIN="python3"
if [ -x "$PROJECT_ROOT/.venv/bin/python" ]; then
  PYTHON_BIN="$PROJECT_ROOT/.venv/bin/python"
fi

exec "$PYTHON_BIN" scripts/run_paper_low_win.py "$@"
