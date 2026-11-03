import json
import os
import re
import shlex
import urllib.request
from datetime import datetime, date
from zoneinfo import ZoneInfo

import boto3
from connector_intraday_settings import intraday_window_open

ssm = boto3.client("ssm")

DEFAULT_SCRIPT = "/home/ec2-user/apps/port-marketconnector/scripts/run_intraday_snapshot_and_evaluate.sh"
HOLIDAY_API_BASE = "https://nagerholidays.com/api/v4/Holidays"
_holiday_cache = {}


def _event_bool(event: dict, name: str, default: bool) -> bool:
    raw = event.get(name, default)
    if isinstance(raw, bool):
        return raw
    if raw is None:
        return default
    if isinstance(raw, str):
        return raw.strip().lower() in ("1", "true", "yes", "y", "on")
    return bool(raw)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "y", "on")


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value or not value.strip():
        raise ValueError(f"Required environment variable is missing: {name}")
    return value.strip()


def _validated_dispatch(event):
    if not isinstance(event, dict) or set(event) - {"dryRun", "reason", "accountNo", "runDate"}:
        raise ValueError("UNSUPPORTED_INTRADAY_INPUT")
    if "dryRun" in event and type(event["dryRun"]) is not bool:
        raise ValueError("INVALID_DRY_RUN")
    if "reason" in event and (not isinstance(event["reason"], str)
            or not re.fullmatch(r"[A-Z0-9_:-]{1,80}", event["reason"])):
        raise ValueError("INVALID_REASON")
    # accountNo is retained only as a compatibility assertion for the existing
    # Scheduler input. It never selects or replaces the configured account.
    account_no = _require_env("ACCOUNT_NO")
    if "accountNo" in event and (not isinstance(event["accountNo"], str)
                                or event["accountNo"] != account_no):
        raise ValueError("ACCOUNT_OVERRIDE_FORBIDDEN")
    instance_id = _require_env("MARKETCONNECTOR_INSTANCE_ID")
    if not re.fullmatch(r"i-(?:[0-9a-f]{8}|[0-9a-f]{17})", instance_id):
        raise ValueError("INVALID_CONFIGURED_INSTANCE")
    script_path = os.environ.get("INTRADAY_WRAPPER_PATH") or DEFAULT_SCRIPT
    if script_path != DEFAULT_SCRIPT:
        raise ValueError("UNEXPECTED_CONFIGURED_WRAPPER")
    try:
        timeout = int(os.environ.get("SSM_EXECUTION_TIMEOUT_SECONDS", "900"))
    except ValueError:
        raise ValueError("INVALID_SSM_TIMEOUT") from None
    if not 1 <= timeout <= 900:
        raise ValueError("INVALID_SSM_TIMEOUT")
    return account_no, instance_id, script_path, str(timeout)


def _log_result(message, result):
    safe = {k: v for k, v in result.items()
            if k not in {"accountNo", "instanceId", "commandPreview", "commandId"}}
    print(json.dumps({"message": message, "result": safe}, ensure_ascii=False, default=str))


def _fetch_public_holidays(year: int, country_code: str) -> set[date]:
    key = (year, country_code)
    if key in _holiday_cache:
        return _holiday_cache[key]

    url = f"{HOLIDAY_API_BASE}/{country_code}/{year}"
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "PortfolioHolidayChecker/1.0",
            "Accept": "application/json",
        },
    )

    with urllib.request.urlopen(request, timeout=5) as response:
        payload = json.loads(response.read().decode("utf-8"))

    holidays = {
        datetime.strptime(row["date"], "%Y-%m-%d").date()
        for row in payload
        if row.get("date")
    }
    _holiday_cache[key] = holidays
    return holidays


def _is_weekend_or_public_holiday(target_date: date, country_code: str) -> tuple[bool, str]:
    if target_date.weekday() >= 5:
        return True, "WEEKEND"

    holidays = _fetch_public_holidays(target_date.year, country_code)
    if target_date in holidays:
        return True, "PUBLIC_HOLIDAY"

    return False, "MARKET_DAY_CANDIDATE"


def lambda_handler(event, context):
    account_no, instance_id, script_path, execution_timeout = _validated_dispatch(event)
    if not intraday_window_open():
        result = {"ok": True, "started": False, "skipped": True,
                  "skipReason": "OUTSIDE_EXECUTION_WINDOW"}
        _log_result("OUTSIDE_EXECUTION_WINDOW", result)
        return result
    print(json.dumps({"message": "EVENT_RECEIVED", "eventKeys": sorted(event)}, ensure_ascii=False))

    timezone_name = os.environ.get("TIMEZONE", "Asia/Seoul")
    country_code = os.environ.get("HOLIDAY_COUNTRY", "KR")
    fail_closed = _env_bool("FAIL_CLOSED_ON_HOLIDAY_ERROR", True)

    now_kst = datetime.now(ZoneInfo(timezone_name))
    if "runDate" in event and event["runDate"] != now_kst.date().isoformat():
        raise ValueError("RUN_DATE_OVERRIDE_FORBIDDEN")
    run_date = event.get("runDate") or now_kst.date().isoformat()

    target_date = datetime.strptime(run_date, "%Y-%m-%d").date()
    holiday_decision = {
        "checked": True,
        "country": country_code,
        "runDate": run_date,
        "closed": None,
        "reason": None,
    }

    try:
        is_closed, market_reason = _is_weekend_or_public_holiday(target_date, country_code)
    except Exception as exc:
        holiday_decision["reason"] = "HOLIDAY_CHECK_ERROR"
        holiday_decision["errorType"] = type(exc).__name__

        if fail_closed:
            result = {
                "ok": True,
                "dryRun": _event_bool(event, "dryRun", True),
                "started": False,
                "skipped": True,
                "skipReason": "HOLIDAY_CHECK_FAILED_FAIL_CLOSED",
                "runDate": run_date,
                "holidayDecision": holiday_decision,
            }
            print(json.dumps({"message": "HOLIDAY_CHECK_FAILED_FAIL_CLOSED", "result": result}, ensure_ascii=False, default=str))
            return result

        print(json.dumps({"message": "HOLIDAY_CHECK_FAILED_OPEN", "holidayDecision": holiday_decision}, ensure_ascii=False, default=str))
    else:
        holiday_decision["closed"] = is_closed
        holiday_decision["reason"] = market_reason

        if is_closed:
            result = {
                "ok": True,
                "dryRun": _event_bool(event, "dryRun", True),
                "started": False,
                "skipped": True,
                "skipReason": market_reason,
                "runDate": run_date,
                "holidayDecision": holiday_decision,
            }
            print(json.dumps({"message": "INTRADAY_ACTION_SKIPPED_BY_HOLIDAY_GUARD", "result": result}, ensure_ascii=False, default=str))
            return result

    dry_run = _event_bool(event, "dryRun", True)
    reason = event.get("reason") or "INTRADAY_SNAPSHOT_AND_EVALUATE"

    command = f"""
set -euo pipefail

echo "===== LAMBDA_INTRADAY_DISPATCH_START ====="
date -u

WRAPPER={shlex.quote(script_path)}
test -x "$WRAPPER"

ACCOUNT_NO={shlex.quote(account_no)} "$WRAPPER"

echo "LAMBDA_INTRADAY_DISPATCH=SUCCESS"
echo "===== LAMBDA_INTRADAY_DISPATCH_END ====="
date -u
""".strip()

    response_preview = {
        "ok": True,
        "dryRun": dry_run,
        "started": False,
        "skipped": False,
        "runDate": run_date,
        "reason": reason,
        "instanceId": instance_id,
        "accountNo": account_no,
        "scriptPath": script_path,
        "executionTimeoutSeconds": execution_timeout,
        "holidayDecision": holiday_decision,
        "commandPreview": command,
    }

    if dry_run:
        _log_result("DRY_RUN_NO_SSM_SEND_COMMAND", response_preview)
        return response_preview

    if not intraday_window_open():
        return {"ok": True, "started": False, "skipped": True,
                "skipReason": "OUTSIDE_EXECUTION_WINDOW"}
    response = ssm.send_command(
        InstanceIds=[instance_id],
        DocumentName="AWS-RunShellScript",
        Parameters={
            "commands": [command],
            "workingDirectory": ["/home/ec2-user"],
            "executionTimeout": [execution_timeout],
        },
        Comment=f"PORTFOLIO_INTRADAY_SNAPSHOT_AND_EVALUATE:{reason}",
    )

    command_id = response["Command"]["CommandId"]

    result = {
        "ok": True,
        "dryRun": False,
        "started": True,
        "skipped": False,
        "runDate": run_date,
        "reason": reason,
        "instanceId": instance_id,
        "accountNo": account_no,
        "scriptPath": script_path,
        "commandId": command_id,
        "holidayDecision": holiday_decision,
    }

    _log_result("SSM_SEND_COMMAND_SUBMITTED", result)
    return result
