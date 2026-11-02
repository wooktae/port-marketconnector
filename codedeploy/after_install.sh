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

# Non-secret intraday settings are separate from Daily secret injection.
# Preserve operator settings on subsequent deployments; first installation is OFF.
install -d -m 700 -o ec2-user -g ec2-user "${APP_ROOT}/runtime"
if [ ! -e "${APP_ROOT}/runtime/intraday-auto.json" ]; then
  install -m 600 -o ec2-user -g ec2-user \
    "${STAGING}/deployment/intraday-auto.default.json" "${APP_ROOT}/runtime/intraday-auto.json"
fi

echo "APPLICATION_SOURCE_INSTALL=SUCCESS"
echo "RUNTIME_TOKEN_FILES_PRESERVED=YES"
echo "AFTER_INSTALL=SUCCESS"
