#!/usr/bin/env bash
set -euo pipefail

APP_ROOT="/home/ec2-user/apps/port-marketconnector"
STAGING="${APP_ROOT}/.codedeploy/staging"
BACKUP_ROOT="${APP_ROOT}/.codedeploy/backups"
BACKUP_ID="${DEPLOYMENT_ID:-manual}-$(date +%Y%m%d%H%M%S)"
BACKUP="${BACKUP_ROOT}/${BACKUP_ID}"

mkdir -p   "$STAGING"   "$BACKUP/src"   "$BACKUP/scripts"

find "$STAGING"   -mindepth 1   -maxdepth 1   -exec rm -rf {} +

find "${APP_ROOT}/src"   -maxdepth 1   -type f   -name '*.py'   -exec cp -a {} "$BACKUP/src/" \;

find "${APP_ROOT}/scripts"   -maxdepth 1   -type f   -name '*.sh'   -exec cp -a {} "$BACKUP/scripts/" \;

if [ -f "${APP_ROOT}/requirements.txt" ]; then
  cp -a     "${APP_ROOT}/requirements.txt"     "$BACKUP/"
fi

printf '%s
' "$BACKUP"   > "${APP_ROOT}/.codedeploy/latest-backup-path"

chown -R ec2-user:ec2-user   "${APP_ROOT}/.codedeploy"

echo "BACKUP_PATH=$BACKUP"
echo "BEFORE_INSTALL=SUCCESS"
echo "TOKEN_FILE_MODIFIED=NO"
