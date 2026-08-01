#!/usr/bin/env bash
set -euo pipefail

APP_ROOT="/home/ec2-user/apps/port-marketconnector"
PROCESS_PATTERN="${APP_ROOT}/src/connector_app.py"

PROCESS_COUNT="$(
  pgrep -f "$PROCESS_PATTERN" |
  wc -l ||
  true
)"

echo "CONNECTOR_PROCESS_COUNT=$PROCESS_COUNT"

if [ "$PROCESS_COUNT" -gt 1 ]; then
  echo "DUPLICATE_CONNECTOR_PROCESS=DETECTED"
  exit 30
fi

echo "PERSISTENT_SERVICE_BASELINE=ABSENT"
echo "CONNECTOR_PROCESS_STARTED=NO"
echo "APPLICATION_START=SUCCESS"
echo "ORDER_API_CALLED=NO"
