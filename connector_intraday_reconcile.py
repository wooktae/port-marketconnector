"""ID-scoped history query followed by the existing Execution fill-sync ECS task.

This module never submits/cancels broker orders. Runtime AWS calls are lazy and
must be mocked during tests. Task definition must pin the prepared Execution image.
"""
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import subprocess
import time


def validate_fill_config(config):
    import re
    if (not isinstance(config, dict) or not config.get("cluster") or not config.get("container")
            or not re.fullmatch(r"[A-Za-z0-9_:/.-]+:\d+", config.get("taskDefinition", ""))
            or not config.get("subnets") or not config.get("securityGroups")
            or any(not re.fullmatch(r"subnet-[0-9a-f]+", s) for s in config["subnets"])
            or any(not re.fullmatch(r"sg-[0-9a-f]+", s) for s in config["securityGroups"])
            or config.get("assignPublicIp") not in ("ENABLED", "DISABLED")):
        raise ValueError("INTRADAY_FILL_CONFIG_REQUIRED")


def aws_json(arguments):
    result = subprocess.run(["aws", *arguments, "--output", "json", "--no-cli-pager"],
                            capture_output=True, text=True, timeout=30, check=False)
    if result.returncode != 0:
        raise RuntimeError("INTRADAY_FILL_AWS_FAILED")
    return json.loads(result.stdout)


def sync_ecs(config, account_id, order_ids, run_id, *, call=aws_json, sleep=time.sleep, preflight=False):
    validate_fill_config(config)
    if not preflight and (type(account_id) is not int or account_id <= 0 or not order_ids or len(order_ids) > 20
            or any(type(i) is not int or i <= 0 for i in order_ids)):
        raise ValueError("INVALID_INTRADAY_FILL_SCOPE")
    # Old Execution images ignore unknown CLI arguments: fail before their Daily main.
    bootstrap = ("import execution_sync_sell_fill as m\n"
                 "if getattr(m, 'INTRADAY_SYNC_API', None) != 'intraday-id-scope-v1':\n"
                 "    raise RuntimeError('INTRADAY_SYNC_API_REQUIRED')\n"
                 "m.main()")
    if preflight:
        if account_id is not None or order_ids:
            raise ValueError("INVALID_INTRADAY_PREFLIGHT_SCOPE")
        command = ["python", "-c", bootstrap, "--intraday-preflight"]
    else:
        command = ["python", "-c", bootstrap, "--intraday-account-id", str(account_id)]
        for order_id in sorted(set(order_ids)):
            command += ["--intraday-order-id", str(order_id)]
    region = config.get("region", "ap-northeast-2")
    token = hashlib.sha256(json.dumps([run_id, preflight, account_id, sorted(set(order_ids))]).encode()).hexdigest()
    response = call(["ecs", "run-task", "--region", region, "--cluster", config["cluster"],
                     "--task-definition", config["taskDefinition"], "--launch-type", "FARGATE",
                     "--client-token", token, "--count", "1", "--network-configuration",
                     json.dumps({"awsvpcConfiguration": {"subnets": config["subnets"],
                         "securityGroups": config["securityGroups"], "assignPublicIp": config["assignPublicIp"]}}),
                     "--overrides", json.dumps({"containerOverrides": [{"name": config["container"],
                         "command": command, "environment": [{"name": "PORT_ENVIRONMENT", "value": "paper"},
                             {"name": "PORT_DB_TARGET", "value": "aws-paper"}]}]})])
    tasks = response.get("tasks") or []
    if response.get("failures") or len(tasks) != 1 or not tasks[0].get("taskArn"):
        raise RuntimeError("INTRADAY_FILL_TASK_NOT_STARTED")
    for attempt in range(12):
        state = call(["ecs", "describe-tasks", "--region", region, "--cluster", config["cluster"],
                      "--tasks", tasks[0]["taskArn"]])
        current = state.get("tasks") or []
        if state.get("failures") or len(current) != 1:
            raise RuntimeError("INTRADAY_FILL_TASK_UNKNOWN")
        if current[0].get("lastStatus") == "STOPPED":
            containers = [c for c in current[0].get("containers", []) if c.get("name") == config["container"]]
            if len(containers) != 1 or containers[0].get("exitCode") != 0:
                raise RuntimeError("INTRADAY_FILL_SYNC_FAILED")
            return
        if attempt < 11:
            sleep(5)
    raise RuntimeError("INTRADAY_FILL_SYNC_TIMEOUT")


def reconcile(store, account, run_id, config, query, *, sync=sync_ecs):
    rows = store.tracked(account)
    groups = {}
    for row in rows:
        if not all((row.get("broker_order_no"), row.get("broker_branch_code"), row.get("requested_at"))):
            raise ValueError("INTRADAY_HISTORY_CONTEXT_MISSING")
        if row.get("connector_status") != "FILLED":
            # Existing history code prints raw broker responses; keep them out of logs.
            with redirect_stdout(io.StringIO()):
                result = query(start_date=row["requested_at"].strftime("%Y%m%d"),
                               end_date=datetime.now(timezone(timedelta(hours=9))).strftime("%Y%m%d"),
                               stock_code=row["ticker_code"], order_no=row["broker_order_no"],
                               branch_code=row["broker_branch_code"], try_broad_search_if_empty=False,
                               expected_order_request_id=row["connector_order_request_id"])
            if not isinstance(result, dict) or result.get("rt_cd") != "0":
                raise RuntimeError("INTRADAY_HISTORY_QUERY_FAILED")
        groups.setdefault(row["account_id"], []).append(row["id"])
    for account_id, ids in groups.items():
        for offset in range(0, len(ids), 20):
            sync(config, account_id, ids[offset:offset + 20], run_id)
    return [row["id"] for row in rows]
