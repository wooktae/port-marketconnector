#!/usr/bin/env bash
# Daily AWS Paper Step 1 - CONNECTOR_BALANCE runner
#
# Purpose:
#   Run connector_balance.py on the MarketConnector EC2 in a Step Functions / SSM friendly way.
#
# Safety:
#   - Paper environment only
#   - AWS paper DB target only
#   - Secret values are never printed
#   - /tmp/inject-env.sh is rebuilt when missing or when --rebuild-env is passed
#
# Usage:
#   /home/ec2-user/apps/port-marketconnector/scripts/run_connector_balance_daily.sh
#   /home/ec2-user/apps/port-marketconnector/scripts/run_connector_balance_daily.sh --rebuild-env
#   /home/ec2-user/apps/port-marketconnector/scripts/run_connector_balance_daily.sh --run-date 2026-06-22

set -euo pipefail

APP_ROOT="/home/ec2-user/apps/port-marketconnector"
SRC_DIR="${APP_ROOT}/src"
VENV_PYTHON="${APP_ROOT}/.venv/bin/python"

MARKETCONNECTOR_ENV_FILE="/tmp/inject-env.sh"
MARKETCONNECTOR_ENV_BUILDER="/tmp/build-marketconnector-env.py"

AWS_REGION_VALUE="${AWS_REGION:-${AWS_DEFAULT_REGION:-ap-northeast-2}}"
REBUILD_ENV="false"
RUN_DATE=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --rebuild-env)
            REBUILD_ENV="true"
            shift
            ;;
        --run-date)
            if [[ $# -lt 2 ]]; then
                echo "[BLOCKER] --run-date requires YYYY-MM-DD value" >&2
                exit 10
            fi
            RUN_DATE="$2"
            shift 2
            ;;
        *)
            echo "[BLOCKER] Unknown argument: $1" >&2
            exit 11
            ;;
    esac
done

echo "===== CONNECTOR_BALANCE START ====="
date -u
echo "[INFO] host=$(hostname)"
echo "[INFO] user=$(whoami)"
echo "[INFO] aws_region=${AWS_REGION_VALUE}"
if [[ -n "${RUN_DATE}" ]]; then
    echo "[INFO] run_date=${RUN_DATE}"
fi

export AWS_REGION="${AWS_REGION_VALUE}"
export AWS_DEFAULT_REGION="${AWS_REGION_VALUE}"
export PORT_ENVIRONMENT="paper"
export PORT_DB_TARGET="aws-paper"

if [[ ! -d "${SRC_DIR}" ]]; then
    echo "[BLOCKER] source directory not found: ${SRC_DIR}" >&2
    exit 20
fi

if [[ ! -x "${VENV_PYTHON}" ]]; then
    echo "[BLOCKER] MarketConnector python not executable: ${VENV_PYTHON}" >&2
    exit 21
fi

if ! command -v aws >/dev/null 2>&1; then
    echo "[BLOCKER] aws cli not found in PATH" >&2
    exit 22
fi

cd "${SRC_DIR}"

echo "[ENV] MarketConnector env bootstrap start"

if [[ "${REBUILD_ENV}" == "true" ]]; then
    echo "[ENV] rebuild requested. removing ${MARKETCONNECTOR_ENV_FILE}"
    rm -f "${MARKETCONNECTOR_ENV_FILE}"
fi

if [[ ! -f "${MARKETCONNECTOR_ENV_FILE}" ]]; then
    echo "[ENV] ${MARKETCONNECTOR_ENV_FILE} missing. rebuilding from Secrets Manager / SSM Parameter Store"

    umask 077

    cat > "${MARKETCONNECTOR_ENV_BUILDER}" <<'PY_EOF'
import json
import os
import pathlib
import shlex
import subprocess

REGION = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "ap-northeast-2"
OUT = pathlib.Path("/tmp/inject-env.sh")


def aws_text(args):
    cmd = ["aws", "--region", REGION] + args
    return subprocess.check_output(cmd, text=True).strip()


def secret_string(secret_id):
    return aws_text([
        "secretsmanager",
        "get-secret-value",
        "--secret-id",
        secret_id,
        "--query",
        "SecretString",
        "--output",
        "text",
    ])


def secret_key(secret_id, key):
    raw = secret_string(secret_id)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return raw

    if isinstance(data, dict):
        if key in data:
            return str(data[key])
        raise KeyError(f"Secret {secret_id} does not contain key {key}")

    return str(data)


def parameter(name):
    return aws_text([
        "ssm",
        "get-parameter",
        "--name",
        name,
        "--query",
        "Parameter.Value",
        "--output",
        "text",
    ])


values = {}

values["APP_KEY"] = secret_key("/portfolio/paper/marketconnector/kis-app-key", "APP_KEY")
values["APP_SECRET"] = secret_key("/portfolio/paper/marketconnector/kis-app-secret", "APP_SECRET")
values["PAPER_ACNT"] = secret_key("/portfolio/paper/marketconnector/paper-account", "PAPER_ACNT")
values["ACNT_PRDT_CD"] = secret_key("/portfolio/paper/marketconnector/paper-account", "ACNT_PRDT_CD")

rds = json.loads(secret_string("/portfolio/paper/rds/marketconnector-app"))
values["INTEREST_DB_HOST"] = str(rds["host"])
values["INTEREST_DB_PORT"] = str(rds["port"])
values["INTEREST_DB_NAME"] = str(rds["dbname"])
values["INTEREST_DB_USER"] = str(rds["username"])
values["INTEREST_DB_PASSWORD"] = str(rds["password"])

values["BASE_URL"] = parameter("/portfolio/paper/marketconnector/kis-base-url")
values["PORT_ENVIRONMENT"] = parameter("/portfolio/paper/marketconnector/environment")
values["PORT_BROKER_NAME"] = parameter("/portfolio/paper/marketconnector/broker-name")
values["CONNECTOR_HOST"] = parameter("/portfolio/paper/marketconnector/connector-host")
values["CONNECTOR_PORT"] = parameter("/portfolio/paper/marketconnector/connector-port")
values["CONNECTOR_DEBUG"] = parameter("/portfolio/paper/marketconnector/connector-debug")

values["KIS_APP_KEY"] = values["APP_KEY"]
values["KIS_APP_SECRET"] = values["APP_SECRET"]
values["KIS_PAPER_ACNT"] = values["PAPER_ACNT"]
values["KIS_ACNT_PRDT_CD"] = values["ACNT_PRDT_CD"]
values["KIS_BASE_URL"] = values["BASE_URL"]

# Daily AWS paper guard. Keep this explicit even if caller already exported it.
values["PORT_DB_TARGET"] = "aws-paper"

required = [
    "APP_KEY",
    "APP_SECRET",
    "PAPER_ACNT",
    "ACNT_PRDT_CD",
    "INTEREST_DB_HOST",
    "INTEREST_DB_PORT",
    "INTEREST_DB_NAME",
    "INTEREST_DB_USER",
    "INTEREST_DB_PASSWORD",
    "BASE_URL",
    "PORT_ENVIRONMENT",
    "PORT_BROKER_NAME",
    "CONNECTOR_HOST",
    "CONNECTOR_PORT",
    "CONNECTOR_DEBUG",
    "KIS_APP_KEY",
    "KIS_APP_SECRET",
    "KIS_PAPER_ACNT",
    "KIS_ACNT_PRDT_CD",
    "KIS_BASE_URL",
    "PORT_DB_TARGET",
]

missing = [key for key in required if key not in values or values[key] == ""]
if missing:
    raise RuntimeError("Missing required env keys: " + ",".join(missing))

lines = [
    "#!/usr/bin/env bash",
    "# Generated by run_connector_balance_daily.sh. Do not commit. Do not print values.",
    "set -euo pipefail",
]

for key in required:
    lines.append(f"export {key}={shlex.quote(values[key])}")

OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
OUT.chmod(0o700)

# Print only key names. Never print values.
print("[ENV_BUILDER] wrote /tmp/inject-env.sh")
print("[ENV_BUILDER] exported keys=" + ",".join(required))
PY_EOF

    "${VENV_PYTHON}" "${MARKETCONNECTOR_ENV_BUILDER}"
    rm -f "${MARKETCONNECTOR_ENV_BUILDER}"
else
    echo "[ENV] ${MARKETCONNECTOR_ENV_FILE} exists. reusing"
fi

chmod 700 "${MARKETCONNECTOR_ENV_FILE}"
# shellcheck source=/tmp/inject-env.sh
source "${MARKETCONNECTOR_ENV_FILE}"

echo "[ENV] loaded keys:"
env | grep -E '^(APP_KEY|APP_SECRET|PAPER_ACNT|ACNT_PRDT_CD|KIS_APP_KEY|KIS_APP_SECRET|KIS_PAPER_ACNT|KIS_ACNT_PRDT_CD|KIS_BASE_URL|INTEREST_DB_|BASE_URL|PORT_|CONNECTOR_)' | cut -d= -f1 | sort

test "${PORT_ENVIRONMENT:-}" = "paper"
test "${PORT_DB_TARGET:-}" = "aws-paper"

echo "[ENV] MarketConnector env bootstrap end"
echo "[GUARD] PORT_ENVIRONMENT=${PORT_ENVIRONMENT:-}"
echo "[GUARD] PORT_DB_TARGET=${PORT_DB_TARGET:-}"

if [[ -n "${RUN_DATE}" ]]; then
    export RUN_DATE
fi

"${VENV_PYTHON}" connector_balance.py

echo "===== CONNECTOR_BALANCE END ====="
