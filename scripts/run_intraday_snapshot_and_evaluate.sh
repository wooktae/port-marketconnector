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
test -f "$SRC/connector_intraday_auto_run.py"

source /tmp/inject-env.sh

ACCOUNT_NO="${ACCOUNT_NO:-${KIS_PAPER_ACNT:-}}"
: "${ACCOUNT_NO:?ACCOUNT_NO or KIS_PAPER_ACNT is required}"

cd "$SRC"

echo "[STEP 1-3] snapshot, evaluate, scoped intraday submission"
"$PY" connector_intraday_auto_run.py --account-no "$ACCOUNT_NO"

echo "INTRADAY_SNAPSHOT_AND_EVALUATE=SUCCESS"
echo "===== INTRADAY_SNAPSHOT_AND_EVALUATE_END ====="
date -u
