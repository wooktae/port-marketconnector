#!/usr/bin/env bash
set -euo pipefail

APP_ROOT="/home/ec2-user/apps/port-marketconnector"
LOCK_DIR="${APP_ROOT}/.codedeploy"
LOCK_FILE="${LOCK_DIR}/deployment.lock"
PROCESS_PATTERN="${APP_ROOT}/src/connector_app.py"

mkdir -p "$LOCK_DIR"
touch "$LOCK_FILE"
chown -R ec2-user:ec2-user "$LOCK_DIR"

mapfile -t PIDS < <(
  pgrep -f "$PROCESS_PATTERN" || true
)

echo "CONNECTOR_PROCESS_COUNT_BEFORE=${#PIDS[@]}"

if [ "${#PIDS[@]}" -gt 0 ]; then
  kill -TERM "${PIDS[@]}"

  for _ in $(seq 1 20); do
    if ! pgrep -f "$PROCESS_PATTERN" >/dev/null 2>&1; then
      break
    fi
    sleep 1
  done
fi

if pgrep -f "$PROCESS_PATTERN" >/dev/null 2>&1; then
  echo "CONNECTOR_PROCESS_STOP=FAILED"
  exit 20
fi

echo "DEPLOYMENT_LOCK=ENABLED"
echo "CONNECTOR_PROCESS_STOP=SUCCESS"
echo "ORDER_API_CALLED=NO"
