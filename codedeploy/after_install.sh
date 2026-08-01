#!/usr/bin/env bash
set -euo pipefail

APP_ROOT="/home/ec2-user/apps/port-marketconnector"
STAGING="${APP_ROOT}/.codedeploy/staging"
SRC="${APP_ROOT}/src"
SCRIPTS="${APP_ROOT}/scripts"

test -d "$STAGING"
test -f "${STAGING}/config.py"
test -f "${STAGING}/connector_app.py"
test -f "${STAGING}/scripts/run_connector_balance_daily.sh"
test -f "${STAGING}/scripts/run_intraday_snapshot_and_evaluate.sh"

mkdir -p "$SRC" "$SCRIPTS"

find "$STAGING"   -maxdepth 1   -type f   -name '*.py'   -exec cp -f {} "$SRC/" \;

if [ -f "${STAGING}/requirements.txt" ]; then
  cp -f     "${STAGING}/requirements.txt"     "${APP_ROOT}/requirements.txt"
fi

find "${STAGING}/scripts"   -maxdepth 1   -type f   -name '*.sh'   -exec cp -f {} "$SCRIPTS/" \;

chown -R ec2-user:ec2-user   "$SRC"   "$SCRIPTS"

find "$SRC"   -maxdepth 1   -type f   -name '*.py'   -exec chmod 644 {} \;

chmod 600 "${SRC}/config.py"
chmod 700 "${SCRIPTS}/run_connector_balance_daily.sh"
chmod 755 "${SCRIPTS}/run_intraday_snapshot_and_evaluate.sh"

echo "APPLICATION_SOURCE_INSTALL=SUCCESS"
echo "RUNTIME_TOKEN_FILES_PRESERVED=YES"
echo "AFTER_INSTALL=SUCCESS"
