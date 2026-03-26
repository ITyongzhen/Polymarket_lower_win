#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

APP_NAME="${1:-polymarket-lower-win}"
ENV_FILE="${2:-.env}"
RUN_STAMP="$(date +%Y%m%d%H%M%S)"
PM2_LOG_DIR="logs/pm2/${RUN_STAMP}"
PAPER_APP_NAME="${APP_NAME}-paper"
CHAINLINK_APP_NAME="${APP_NAME}-chainlink"

if [ ! -f "$ENV_FILE" ]; then
  if [ -f ".env.example" ]; then
    cp .env.example "$ENV_FILE"
    echo "[pm2] created $ENV_FILE from .env.example"
  else
    echo "[pm2] env file not found: $ENV_FILE" >&2
    exit 1
  fi
fi

mkdir -p "$PM2_LOG_DIR"
mkdir -p logs

set -a
source "$ENV_FILE"
set +a

export PM_LOG_STAMP="$RUN_STAMP"

pm2 delete "$PAPER_APP_NAME" >/dev/null 2>&1 || true
pm2 delete "$CHAINLINK_APP_NAME" >/dev/null 2>&1 || true

pm2 start bash \
  --name "$PAPER_APP_NAME" \
  --cwd "$ROOT_DIR" \
  --time \
  --update-env \
  --output "$PM2_LOG_DIR/${PAPER_APP_NAME}.out.log" \
  --error "$PM2_LOG_DIR/${PAPER_APP_NAME}.err.log" \
  -- scripts/start_paper_low_win.sh "$ENV_FILE"

if [ -n "${PM_CHAINLINK_API_KEY:-}" ] && [ -n "${PM_CHAINLINK_API_SECRET:-}" ]; then
  pm2 start bash \
    --name "$CHAINLINK_APP_NAME" \
    --cwd "$ROOT_DIR" \
    --time \
    --update-env \
    --output "$PM2_LOG_DIR/${CHAINLINK_APP_NAME}.out.log" \
    --error "$PM2_LOG_DIR/${CHAINLINK_APP_NAME}.err.log" \
    -- scripts/start_chainlink_collector.sh "$ENV_FILE"
  echo "[pm2] chainlink collector enabled"
else
  echo "[pm2] chainlink collector skipped: PM_CHAINLINK_API_KEY / PM_CHAINLINK_API_SECRET not set"
fi

pm2 save >/dev/null 2>&1 || true

echo "[pm2] started $PAPER_APP_NAME with env $ENV_FILE"
echo "[pm2] logs:"
echo "  $PM2_LOG_DIR/${PAPER_APP_NAME}.out.log"
echo "  $PM2_LOG_DIR/${PAPER_APP_NAME}.err.log"

if pm2 describe "$CHAINLINK_APP_NAME" >/dev/null 2>&1; then
  echo "  $PM2_LOG_DIR/${CHAINLINK_APP_NAME}.out.log"
  echo "  $PM2_LOG_DIR/${CHAINLINK_APP_NAME}.err.log"
fi
