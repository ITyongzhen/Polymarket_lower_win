#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

ENV_FILE="${PM_ENV_FILE:-.env}"
if [ $# -gt 0 ] && [[ "${1:-}" != --* ]]; then
  ENV_FILE="$1"
  shift
fi

if [ ! -f "$ENV_FILE" ]; then
  if [ -f ".env.example" ]; then
    cp .env.example "$ENV_FILE"
    echo "[chainlink] created $ENV_FILE from .env.example"
  else
    echo "[chainlink] env file not found: $ENV_FILE" >&2
    exit 1
  fi
fi

# 让 .env 里的参数自动导出给 Python 进程。
set -a
source "$ENV_FILE"
set +a

export PYTHONPATH=src

PYTHON_BIN="python3"
if [ -x "$PROJECT_ROOT/.venv/bin/python" ]; then
  PYTHON_BIN="$PROJECT_ROOT/.venv/bin/python"
fi

exec "$PYTHON_BIN" scripts/collect_chainlink_reports.py --env-file "$ENV_FILE" "$@"
