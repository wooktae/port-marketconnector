#!/usr/bin/env bash
set -euo pipefail

APP=/home/ec2-user/apps/port-marketconnector
SRC="$APP/src"
PY="$APP/.venv/bin/python"


echo "===== INTRADAY_SNAPSHOT_AND_EVALUATE_START ====="
date -u

echo "[CHECK] runtime"
test -x "$PY"
test -f /tmp/inject-env.sh
test -f "$SRC/connector_intraday_snapshot_refresh.py"
test -f "$SRC/connector_intraday_position_evaluate.py"

source /tmp/inject-env.sh

ACCOUNT_NO="${ACCOUNT_NO:-${KIS_PAPER_ACNT:-}}"
: "${ACCOUNT_NO:?ACCOUNT_NO or KIS_PAPER_ACNT is required}"

cd "$SRC"

echo "[STEP 1] snapshot refresh"
"$PY" connector_intraday_snapshot_refresh.py

echo "[STEP 2] position evaluate"
"$PY" connector_intraday_position_evaluate.py \
  --account-no "$ACCOUNT_NO" \
  --create-order \
  --notify-slack

echo "INTRADAY_SNAPSHOT_AND_EVALUATE=SUCCESS"
echo "===== INTRADAY_SNAPSHOT_AND_EVALUATE_END ====="
date -u
