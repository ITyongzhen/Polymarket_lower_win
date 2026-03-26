#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

python_is_supported() {
  local bin="$1"
  if [ -z "$bin" ]; then
    return 1
  fi
  if ! command -v "$bin" >/dev/null 2>&1; then
    return 1
  fi
  "$bin" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1
}

resolve_python_bin() {
  if [ -x "$PROJECT_ROOT/.venv/bin/python" ]; then
    printf '%s\n' "$PROJECT_ROOT/.venv/bin/python"
    return 0
  fi
  if [ -n "${PYTHON_BIN:-}" ] && python_is_supported "$PYTHON_BIN"; then
    printf '%s\n' "$PYTHON_BIN"
    return 0
  fi
  local candidate
  for candidate in python3.11 /usr/bin/python3.11 python3.12 /usr/bin/python3.12 python3 /usr/bin/python3; do
    if python_is_supported "$candidate"; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

ENV_FILE="${PM_ENV_FILE:-.env}"
if [ $# -gt 0 ] && [[ "${1:-}" != --* ]]; then
  ENV_FILE="$1"
  shift
fi

if [ ! -f "$ENV_FILE" ]; then
  if [ -f ".env.example" ]; then
    cp .env.example "$ENV_FILE"
    echo "[paper] created $ENV_FILE from .env.example"
  else
    echo "[paper] env file not found: $ENV_FILE" >&2
    exit 1
  fi
fi

mkdir -p logs

# 让 .env 里的参数自动导出给 Python 进程。
set -a
source "$ENV_FILE"
set +a

if [ -z "${PM_RUN_ID:-}" ] || [ "${PM_RUN_ID}" = "auto" ]; then
  export PM_RUN_ID="${PM_LOG_STAMP:-$(date +%Y%m%d%H%M%S)}"
fi

case "${PM_LOGS_ROOT:-}" in
  "" )
    export PM_LOGS_ROOT="logs/paper_low_win"
    ;;
  Logs/*)
    export PM_LOGS_ROOT="logs/${PM_LOGS_ROOT#Logs/}"
    ;;
  Logs)
    export PM_LOGS_ROOT="logs/paper_low_win"
    ;;
esac

export PYTHONPATH=src

PYTHON_BIN="$(resolve_python_bin || true)"
if [ -z "$PYTHON_BIN" ]; then
  echo "[paper] no supported Python found. Require Python 3.11+." >&2
  exit 2
fi

exec "$PYTHON_BIN" scripts/run_paper_low_win.py --env-file "$ENV_FILE" "$@"
