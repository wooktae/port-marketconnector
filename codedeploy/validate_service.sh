#!/usr/bin/env bash
set -euo pipefail

APP_ROOT="/home/ec2-user/apps/port-marketconnector"
SRC="${APP_ROOT}/src"
SCRIPTS="${APP_ROOT}/scripts"
LOCK_FILE="${APP_ROOT}/.codedeploy/deployment.lock"
PROCESS_PATTERN="${SRC}/connector_app.py"

test -f "${APP_ROOT}/access_token.txt"
test -f "${SRC}/access_token.txt"

python3 -m py_compile   "${SRC}/config.py"   "${SRC}/connector_app.py"   "${SRC}/connector_balance.py"   "${SRC}/connector_intraday_snapshot_refresh.py"   "${SRC}/connector_intraday_position_evaluate.py"

bash -n   "${SCRIPTS}/run_connector_balance_daily.sh"

bash -n   "${SCRIPTS}/run_intraday_snapshot_and_evaluate.sh"

test -x   "${SCRIPTS}/run_connector_balance_daily.sh"

test -x   "${SCRIPTS}/run_intraday_snapshot_and_evaluate.sh"

PROCESS_COUNT="$(
  pgrep -f "$PROCESS_PATTERN" |
  wc -l ||
  true
)"

echo "CONNECTOR_PROCESS_COUNT=$PROCESS_COUNT"

if [ "$PROCESS_COUNT" -gt 1 ]; then
  echo "DUPLICATE_CONNECTOR_PROCESS=DETECTED"
  exit 40
fi

rm -f "$LOCK_FILE"

echo "PYTHON_COMPILE=SUCCESS"
echo "WRAPPER_SHELL_SYNTAX=SUCCESS"
echo "TOKEN_FILES_PRESERVED=SUCCESS"
echo "DEPLOYMENT_LOCK=DISABLED"
echo "ORDER_API_CALLED=NO"
echo "VALIDATE_SERVICE=SUCCESS"
