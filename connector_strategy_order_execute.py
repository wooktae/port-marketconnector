"""Submit REQUESTED strategy execution orders through MarketConnector.

Default execution is a dry run: it reads target orders and prints the planned
processing order without calling KIS, buy_stock/sell_stock, or DB update paths.
Use --execute only in the guarded paper/aws-paper runtime.
"""

import argparse
import json
import os
import time
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional, Tuple

from psycopg.rows import dict_row

from connector_db import get_conn
from connector_order_common import (
    check_fatal_max_order_qty,
    normalize_order_qty,
    resolve_fatal_max_order_qty,
)


DEFAULT_LIMIT = 20
EXECUTION_MODE = "PAPER_STRATEGY"
REQUESTED_STATUS = "REQUESTED"
SUBMITTING_STATUS = "SUBMITTING"
SUBMITTED_STATUS = "SUBMITTED"
FAILED_STATUS = "FAILED"
RETRYABLE_REJECTION_CODES = ("40580000", "EGW00201")
RATE_LIMIT_REJECTION_CODE = "EGW00201"
RETRYABLE_EXECUTION_STATUSES = ("READY", "FAILED")
DEFAULT_ORDER_SLEEP_SECONDS = float(os.environ.get("STEP12_ORDER_SLEEP_SECONDS", "2.0"))
DEFAULT_RATE_LIMIT_RETRY_COUNT = int(os.environ.get("STEP12_RATE_LIMIT_RETRY_COUNT", "2"))
DEFAULT_RATE_LIMIT_BACKOFF_SECONDS = float(os.environ.get("STEP12_RATE_LIMIT_BACKOFF_SECONDS", "3.0"))

REQUIRED_COLUMNS = [
    "id",
    "execution_plan_id",
    "execution_mode",
    "source_type",
    "action_type",
    "execution_status",
    "ticker_code",
    "stock_name",
    "order_qty",
    "order_method",
    "order_price",
    "connector_order_request_id",
    "source_position_state_id",
]

OPTIONAL_COLUMNS = [
    "strategy_name",
    "strategy_version",
    "source_daily_run_id",
    "source_daily_signal_id",
    "source_daily_position_decision_id",
    "source_signal_id",
    "signal_type",
    "signal_date",
    "signal_score",
    "target_weight",
    "signal_position_size",
    "sell_reason",
    "sell_info",
]


def _json_default(value: Any) -> str:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def _json_payload(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=_json_default)


def _quote_identifier(identifier: str) -> str:
    if not identifier.replace("_", "").isalnum():
        raise RuntimeError(f"invalid SQL identifier: {identifier}")
    return f'"{identifier}"'


def _table_info(table_name: str) -> Tuple[str, set]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT table_schema, column_name
                FROM information_schema.columns
                WHERE table_name = %s
                  AND table_schema NOT IN ('pg_catalog', 'information_schema')
                ORDER BY
                    CASE
                        WHEN table_schema = current_schema() THEN 0
                        WHEN table_schema = 'public' THEN 1
                        ELSE 2
                    END,
                    table_schema,
                    ordinal_position
                """,
                (table_name,),
            )
            rows = cur.fetchall()

    if not rows:
        raise RuntimeError(f"{table_name} 테이블을 찾을 수 없음")

    selected_schema = rows[0][0]
    columns = {column for schema, column in rows if schema == selected_schema}
    table_ref = (
        f"{_quote_identifier(selected_schema)}.{_quote_identifier(table_name)}"
    )
    return table_ref, columns


def _select_exprs(existing_columns: Iterable[str]) -> Tuple[str, List[str]]:
    existing = set(existing_columns)
    missing_required = [col for col in REQUIRED_COLUMNS if col not in existing]
    if missing_required:
        raise RuntimeError(
            "strategy_execution_order 필수 컬럼 누락: "
            + ", ".join(missing_required)
        )

    exprs: List[str] = [f"{col}" for col in REQUIRED_COLUMNS]
    for col in OPTIONAL_COLUMNS:
        if col in existing:
            exprs.append(col)
        else:
            exprs.append(f"NULL AS {col}")

    return ",\n                    ".join(exprs), REQUIRED_COLUMNS + OPTIONAL_COLUMNS


def fetch_requested_strategy_orders(
    limit: int = DEFAULT_LIMIT,
    action: Optional[str] = None,
    plan_id: Optional[int] = None,
    signal_type: Optional[str] = None,
) -> List[Dict[str, Any]]:
    table_ref, columns = _table_info("strategy_execution_order")
    if signal_type and "signal_type" not in columns:
        raise RuntimeError("strategy_execution_order signal_type 컬럼 없음")
    select_exprs, _ = _select_exprs(columns)

    conditions = [
        "execution_status = %s",
        "connector_order_request_id IS NULL",
        "execution_mode IN (%s)",
        "action_type IN ('BUY', 'SELL')",
        "order_qty > 0",
    ]
    params: List[Any] = [REQUESTED_STATUS, EXECUTION_MODE]

    if action:
        conditions.append("action_type = %s")
        params.append(action)

    if plan_id is not None:
        conditions.append("execution_plan_id = %s")
        params.append(plan_id)

    if signal_type:
        conditions.append("signal_type = %s")
        params.append(signal_type)

    params.append(limit)
    sql = f"""
        SELECT
                    {select_exprs}
        FROM {table_ref}
        WHERE {" AND ".join(conditions)}
        ORDER BY
            CASE
                WHEN action_type = 'SELL' THEN 0
                WHEN action_type = 'BUY' THEN 1
                ELSE 2
            END,
            id ASC
        LIMIT %s
    """

    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, tuple(params))
            return [dict(row) for row in cur.fetchall()]



def normalize_retryable_rejected_orders(
    *,
    execute: bool,
    action: Optional[str],
    plan_id: Optional[int],
    limit: int,
    signal_type: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Recover retryable rejected execution orders before Step 12 submission.

    Step 12 submits only REQUESTED orders whose connector_order_request_id is
    NULL. Retryable rejected requests can otherwise leave an execution order
    stuck with an old rejected connector_order_request_id.

    Retryable rejection codes:
    - 40580000: KIS paper market closed. Safe to retry during a later market
      session.
    - EGW00201: KIS gateway rate limit. Safe to retry only with throttling /
      backoff, so Step 12 also sleeps between orders and retries this code.

    Safety gates:
    - PAPER_STRATEGY only
    - BUY/SELL only
    - execution_status in READY/FAILED
    - connector_order_request is REJECTED with retryable rejection_code
    - broker_order_no is NULL
    - no connector_fill exists for the old request

    In dry-run mode this only prints candidates. In execute mode it detaches the
    old rejected request, sets the execution order back to REQUESTED, and records
    the recovery in result_payload.retry_normalizer.
    """

    order_table_ref, order_columns = _table_info("strategy_execution_order")
    if signal_type and "signal_type" not in order_columns:
        raise RuntimeError("strategy_execution_order signal_type 컬럼 없음")
    request_table_ref, _ = _table_info("connector_order_request")
    fill_table_ref, _ = _table_info("connector_fill")

    conditions = [
        "eo.execution_mode = %s",
        "eo.action_type IN ('BUY', 'SELL')",
        "eo.execution_status IN ('READY', 'FAILED')",
        "eo.connector_order_request_id IS NOT NULL",
        "COALESCE(eo.order_qty, 0) > 0",
        "cor.request_status = 'REJECTED'",
        "cor.rejection_code IN (" + ", ".join(["%s"] * len(RETRYABLE_REJECTION_CODES)) + ")",
        "cor.broker_order_no IS NULL",
        f"NOT EXISTS (SELECT 1 FROM {fill_table_ref} cf WHERE cf.order_request_id = cor.id)",
    ]
    params: List[Any] = [EXECUTION_MODE, *RETRYABLE_REJECTION_CODES]

    if action:
        conditions.append("eo.action_type = %s")
        params.append(action)

    if plan_id is not None:
        conditions.append("eo.execution_plan_id = %s")
        params.append(plan_id)

    if signal_type:
        conditions.append("eo.signal_type = %s")
        params.append(signal_type)

    where_clause = " AND ".join(conditions)

    select_sql = f"""
        SELECT
            eo.id AS execution_order_id,
            eo.execution_plan_id,
            eo.execution_mode,
            eo.source_type,
            eo.action_type,
            eo.execution_status,
            eo.ticker_code,
            eo.stock_name,
            eo.order_qty,
            eo.order_method,
            eo.order_price,
            eo.connector_order_request_id,
            cor.id AS rejected_order_request_id,
            cor.request_status,
            cor.rejection_code,
            cor.rejection_message,
            cor.broker_order_no,
            cor.broker_branch_code,
            cor.requested_at
        FROM {order_table_ref} eo
        JOIN {request_table_ref} cor
          ON cor.id = eo.connector_order_request_id
        WHERE {where_clause}
        ORDER BY
            CASE
                WHEN eo.action_type = 'SELL' THEN 0
                WHEN eo.action_type = 'BUY' THEN 1
                ELSE 2
            END,
            eo.id ASC
        LIMIT %s
    """

    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(select_sql, tuple(params + [limit]))
            candidates = [dict(row) for row in cur.fetchall()]

    if not candidates:
        print("[RETRY_NORMALIZER] retryable rejected order 없음")
        return []

    mode = "EXECUTE" if execute else "DRY_RUN"
    print(f"[RETRY_NORMALIZER:{mode}] candidate_count={len(candidates)}")
    for row in candidates:
        print(
            "[RETRY_CANDIDATE] "
            f"execution_order_id={row['execution_order_id']}, "
            f"old_request_id={row['rejected_order_request_id']}, "
            f"action={row['action_type']}, "
            f"ticker={row['ticker_code']}, "
            f"qty={row['order_qty']}, "
            f"status={row['execution_status']}, "
            f"rejection_code={row['rejection_code']}"
        )

    if not execute:
        return candidates

    update_sql = f"""
        WITH retryable AS (
            SELECT
                eo.id AS execution_order_id,
                eo.execution_status AS old_execution_status,
                eo.connector_order_request_id AS old_connector_order_request_id,
                cor.rejection_code,
                cor.rejection_message
            FROM {order_table_ref} eo
            JOIN {request_table_ref} cor
              ON cor.id = eo.connector_order_request_id
            WHERE {where_clause}
            ORDER BY
                CASE
                    WHEN eo.action_type = 'SELL' THEN 0
                    WHEN eo.action_type = 'BUY' THEN 1
                    ELSE 2
                END,
                eo.id ASC
            LIMIT %s
        )
        UPDATE {order_table_ref} eo
           SET execution_status = 'REQUESTED',
               connector_order_request_id = NULL,
               result_payload =
                   COALESCE(eo.result_payload, '{{}}'::jsonb)
                   || jsonb_build_object(
                       'retry_normalizer',
                       jsonb_build_object(
                           'normalized_at', now(),
                           'normalized_reason', 'RETRY_REJECTED_ORDER_REQUEST',
                           'old_execution_status', r.old_execution_status,
                           'old_connector_order_request_id', r.old_connector_order_request_id,
                           'old_rejection_code', r.rejection_code,
                           'old_rejection_message', r.rejection_message
                       )
                   ),
               updated_at = now()
          FROM retryable r
         WHERE eo.id = r.execution_order_id
        RETURNING
            eo.id AS execution_order_id,
            eo.execution_status,
            eo.connector_order_request_id,
            r.old_connector_order_request_id,
            r.old_execution_status,
            r.rejection_code
    """

    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(update_sql, tuple(params + [limit]))
            recovered = [dict(row) for row in cur.fetchall()]
        conn.commit()

    print(f"[RETRY_NORMALIZER:UPDATED] recovered_count={len(recovered)}")
    for row in recovered:
        print(
            "[RETRY_RECOVERED] "
            f"execution_order_id={row['execution_order_id']}, "
            f"old_status={row['old_execution_status']}, "
            f"new_status={row['execution_status']}, "
            f"old_request_id={row['old_connector_order_request_id']}, "
            f"new_request_id={row['connector_order_request_id']}, "
            f"rejection_code={row['rejection_code']}"
        )

    return recovered

def claim_strategy_execution_order(
    execution_order_id: int,
) -> Optional[Dict[str, Any]]:
    """Atomically claim a REQUESTED execution order for broker submission.

    Runs a single conditional UPDATE that transitions execution_status from
    REQUESTED to SUBMITTING only when the row id matches, the current
    execution_status is REQUESTED, and connector_order_request_id IS NULL. This
    matches the fetch_requested_strategy_orders selection so a claimed row is
    interchangeable with a fetched order downstream.

    Returns the claimed row (same column shape as
    fetch_requested_strategy_orders) when exactly one row is updated. Returns
    None when zero rows are updated, leaving execution_status unchanged (for
    example when the order was already claimed or already has a connector
    request). Concurrent claims of the same order resolve to a single winner
    because the conditional UPDATE is atomic; losing claims observe zero rows.
    """
    table_ref, columns = _table_info("strategy_execution_order")
    select_exprs, _ = _select_exprs(columns)
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"""
                UPDATE {table_ref}
                   SET execution_status = %s,
                       updated_at = now()
                 WHERE id = %s
                   AND execution_status = %s
                   AND connector_order_request_id IS NULL
                 RETURNING
                    {select_exprs}
                """,
                (SUBMITTING_STATUS, execution_order_id, REQUESTED_STATUS),
            )
            row = cur.fetchone()
        conn.commit()
    return dict(row) if row else None


def mark_strategy_execution_order_submitted(
    execution_order_id: int,
    connector_order_request_id: int,
    result_payload: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    table_ref, _ = _table_info("strategy_execution_order")
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"""
                UPDATE {table_ref}
                   SET execution_status = 'SUBMITTED',
                       connector_order_request_id = %s,
                       result_payload = %s::jsonb,
                       updated_at = now()
                 WHERE id = %s
                   AND execution_status = 'SUBMITTING'
                 RETURNING id, execution_status, connector_order_request_id
                """,
                (
                    connector_order_request_id,
                    _json_payload(result_payload),
                    execution_order_id,
                ),
            )
            row = cur.fetchone()
        conn.commit()
    return dict(row) if row else None


def mark_strategy_execution_order_failed(
    execution_order_id: int,
    result_payload: Dict[str, Any],
    connector_order_request_id: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    table_ref, _ = _table_info("strategy_execution_order")
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"""
                UPDATE {table_ref}
                   SET execution_status = 'FAILED',
                       connector_order_request_id = COALESCE(%s, connector_order_request_id),
                       result_payload = %s::jsonb,
                       updated_at = now()
                 WHERE id = %s
                   AND execution_status NOT IN ('SUBMITTED','FILLED','CANCELED','REJECTED','FAILED')
                 RETURNING id, execution_status, connector_order_request_id
                """,
                (
                    connector_order_request_id,
                    _json_payload(result_payload),
                    execution_order_id,
                ),
            )
            row = cur.fetchone()
        conn.commit()
    return dict(row) if row else None


def mark_strategy_position_sell_ordered(
    position_state_id: int,
) -> Optional[Dict[str, Any]]:
    table_ref, _ = _table_info("strategy_position_state")
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"""
                UPDATE {table_ref}
                   SET position_status = 'SELL_ORDERED',
                       updated_at = now()
                 WHERE id = %s
                   AND position_status IN ('OPEN', 'SELL_READY')
                 RETURNING id, position_status
                """,
                (position_state_id,),
            )
            row = cur.fetchone()
        conn.commit()
    return dict(row) if row else None


def _require_execute_environment() -> None:
    environment = os.environ.get("PORT_ENVIRONMENT")
    db_target = os.environ.get("PORT_DB_TARGET")
    if environment != "paper" or db_target != "aws-paper":
        raise RuntimeError(
            "--execute requires PORT_ENVIRONMENT=paper and PORT_DB_TARGET=aws-paper"
        )


def _strategy_run_id(order: Dict[str, Any]) -> Optional[str]:
    """Return only UUID-backed strategy run id.

    connector.connector_order_request.strategy_run_id is a uuid column.
    Daily run ids and execution plan ids are numeric identifiers, so they must
    not be passed as strategy_run_id. They are preserved in payload fields.
    """
    source_run_id = order.get("source_run_id")
    if source_run_id is not None:
        return str(source_run_id)

    return None


def _signal_position_size(order: Dict[str, Any]) -> Optional[Any]:
    if order.get("signal_position_size") is not None:
        return order.get("signal_position_size")
    return order.get("target_weight")


def _order_payload(order: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "source": "connector_strategy_order_execute",
        "execution_order_id": order.get("id"),
        "execution_plan_id": order.get("execution_plan_id"),
        "execution_mode": order.get("execution_mode"),
        "source_type": order.get("source_type"),
        "action_type": order.get("action_type"),
        "signal_type": order.get("signal_type"),
        "ticker_code": order.get("ticker_code"),
        "stock_name": order.get("stock_name"),
        "order_qty": order.get("order_qty"),
        "order_method": order.get("order_method"),
        "order_price": order.get("order_price"),
        "source_run_id": order.get("source_run_id"),
        "source_daily_run_id": order.get("source_daily_run_id"),
        "source_daily_signal_id": order.get("source_daily_signal_id"),
        "source_signal_id": order.get("source_signal_id"),
        "source_position_state_id": order.get("source_position_state_id"),
    }


def _validate_order_for_execute(order: Dict[str, Any]) -> None:
    if order.get("execution_mode") != EXECUTION_MODE:
        raise ValueError("execution_mode must be PAPER_STRATEGY")
    if order.get("action_type") not in ("BUY", "SELL"):
        raise ValueError("action_type must be BUY or SELL")
    if int(order.get("order_qty") or 0) <= 0:
        raise ValueError("order_qty must be positive")


def _is_successful_result(result: Optional[Dict[str, Any]]) -> bool:
    if not result:
        return False
    if not result.get("order_request_id"):
        return False

    response = result.get("response") or {}
    return response.get("rt_cd") == "0" or bool(result.get("broker_order_no"))


def _submit_order(order: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    action = order.get("action_type")
    common_kwargs = {
        "stock_code": order.get("ticker_code"),
        "qty": int(order.get("order_qty")),
        "order_method": order.get("order_method"),
        "order_price": order.get("order_price"),
        "stock_name": order.get("stock_name"),
        "strategy_name": order.get("strategy_name") or "strategy_ai",
        "strategy_version": order.get("strategy_version") or "strategy_execution",
        "strategy_run_id": _strategy_run_id(order),
        "strategy_signal_id": (
            order.get("source_daily_signal_id") or order.get("source_signal_id")
        ),
        "signal_date": order.get("signal_date"),
        "signal_type": order.get("signal_type") or action,
        "signal_score": order.get("signal_score"),
        "signal_position_size": _signal_position_size(order),
    }

    if action == "BUY":
        from connector_buy import buy_stock

        return buy_stock(**common_kwargs)

    if action == "SELL":
        from connector_sell import sell_stock

        return sell_stock(parent_order_request_id=None, **common_kwargs)

    raise ValueError(f"unsupported action_type: {action}")


def _response_code(result: Optional[Dict[str, Any]]) -> Optional[str]:
    if not result:
        return None

    response = result.get("response") or {}
    for key in ("msg_cd", "rt_cd", "error_code", "code"):
        value = response.get(key)
        if value:
            return str(value)

    for key in ("rejection_code", "msg_cd", "error_code", "code"):
        value = result.get(key)
        if value:
            return str(value)

    return None


def _is_rate_limit_result(result: Optional[Dict[str, Any]]) -> bool:
    if not result:
        return False

    if _response_code(result) == RATE_LIMIT_REJECTION_CODE:
        return True

    response = result.get("response") or {}
    message_values = [
        response.get("msg1"),
        response.get("message"),
        result.get("rejection_message"),
        result.get("message"),
    ]
    return any("초당" in str(value) and "초과" in str(value) for value in message_values if value)


def _sleep_if_needed(seconds: float, reason: str) -> None:
    if seconds <= 0:
        return
    print(f"[THROTTLE] reason={reason}, sleep_seconds={seconds:.2f}")
    time.sleep(seconds)


def _submit_order_with_rate_limit_retry(
    order: Dict[str, Any],
    *,
    retry_count: int,
    backoff_seconds: float,
) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    attempts: List[Dict[str, Any]] = []
    max_attempts = max(1, retry_count + 1)

    for attempt_no in range(1, max_attempts + 1):
        result = _submit_order(order)
        response_code = _response_code(result)
        rate_limited = _is_rate_limit_result(result)
        connector_order_request_id = result.get("order_request_id") if result else None

        attempts.append(
            {
                "attempt_no": attempt_no,
                "connector_order_request_id": connector_order_request_id,
                "response_code": response_code,
                "rate_limited": rate_limited,
                "successful": _is_successful_result(result),
            }
        )

        if _is_successful_result(result):
            return result, attempts

        if not rate_limited:
            return result, attempts

        if attempt_no >= max_attempts:
            print(
                "[RATE_LIMIT_RETRY_EXHAUSTED] "
                f"execution_order_id={order.get('id')}, attempts={attempt_no}, "
                f"response_code={response_code}"
            )
            return result, attempts

        sleep_seconds = backoff_seconds * attempt_no
        print(
            "[RATE_LIMIT_RETRY] "
            f"execution_order_id={order.get('id')}, attempt={attempt_no}, "
            f"response_code={response_code}, next_sleep_seconds={sleep_seconds:.2f}"
        )
        _sleep_if_needed(sleep_seconds, "KIS_RATE_LIMIT_BACKOFF")

    return None, attempts


def _print_order(prefix: str, order: Dict[str, Any]) -> None:
    print(
        f"{prefix} id={order.get('id')}, action={order.get('action_type')}, "
        f"signal_type={order.get('signal_type')}, "
        f"ticker={order.get('ticker_code')}, qty={order.get('order_qty')}, "
        f"mode={order.get('execution_mode')}"
    )


def run(args: argparse.Namespace) -> int:
    action = args.action.upper() if args.action else None
    signal_type = args.signal_type.upper() if args.signal_type else None

    if args.intraday_stop_only:
        if action and action != "SELL":
            raise RuntimeError("--intraday-stop-only requires --action SELL or no --action")
        if signal_type and signal_type != "INTRADAY_STOP_SELL":
            raise RuntimeError("--intraday-stop-only requires --signal-type INTRADAY_STOP_SELL or no --signal-type")
        action = "SELL"
        signal_type = "INTRADAY_STOP_SELL"
        print("[INTRADAY_STOP_ONLY] action=SELL, signal_type=INTRADAY_STOP_SELL")

    if args.execute:
        _require_execute_environment()

    normalize_retryable_rejected_orders(
        execute=args.execute,
        action=action,
        plan_id=args.plan_id,
        limit=args.limit,
        signal_type=signal_type,
    )

    orders = fetch_requested_strategy_orders(
        limit=args.limit,
        action=action,
        plan_id=args.plan_id,
        signal_type=signal_type,
    )

    if not orders:
        print("[NO_TARGET] REQUESTED strategy order 없음")
        return 0

    mode = "EXECUTE" if args.execute else "DRY_RUN"
    print(f"[{mode}] REQUESTED strategy order count={len(orders)}")

    # Resolve the Fatal_Max emergency cap once for this strategy-order path.
    # A misconfigured STEP12_FATAL_MAX_ORDER_QTY is an explicit configuration
    # error that must not be silently ignored (Requirements 3.10, 3.12); it
    # surfaces here for both dry-run and execute modes before any order is
    # processed. This enforcement is wired only into this strategy-order path
    # and is intentionally not applied to submit_cash_order().
    fatal_max_limit = resolve_fatal_max_order_qty()

    if not args.execute:
        # Dry_Run performs only the pure quantity normalization + Fatal Max
        # validation (steps 1-2). It never claims an order and never changes
        # execution_status in the DB (Requirement 2.12).
        for order in orders:
            _print_order("[ORDER]", order)
            try:
                _validate_order_for_execute(order)
                normalized_qty = normalize_order_qty(order.get("order_qty"))
                check_fatal_max_order_qty(normalized_qty, fatal_max_limit)
            except ValueError as exc:
                print(
                    "[DRY_RUN_INVALID] "
                    f"execution_order_id={order.get('id')}, "
                    f"reason={exc}, error_type={type(exc).__name__}"
                )
        return 0

    for index, order in enumerate(orders):
        if index > 0:
            _sleep_if_needed(args.order_sleep_seconds, "BETWEEN_ORDERS")

        _print_order("[ORDER]", order)
        execution_order_id = order["id"]

        # Step 1-2: quantity normalization + Fatal Max check before any broker
        # call. On validation failure the broker is not called; the order is
        # recorded as a unit failure and the batch continues with the remaining
        # orders (Requirements 2.6, 3.13, 3.14). QuantityValidationError and
        # FatalMaxExceededError are ValueError subclasses, as is the
        # _validate_order_for_execute contract check, so quantity validation
        # errors are always returned before the Fatal Max check.
        try:
            _validate_order_for_execute(order)
            normalized_qty = normalize_order_qty(order.get("order_qty"))
            check_fatal_max_order_qty(normalized_qty, fatal_max_limit)
        except ValueError as exc:
            mark_strategy_execution_order_failed(
                execution_order_id=execution_order_id,
                result_payload={
                    **_order_payload(order),
                    "reason": str(exc),
                    "error_type": type(exc).__name__,
                },
            )
            print(f"[FAILED] execution_order_id={execution_order_id}, reason={exc}")
            continue

        try:
            # Step 3: Claim before broker submission. A Claim DB error is
            # handled by the except block below: the broker is not called and
            # the order is recorded as a unit failure (Requirement 2.13).
            claimed = claim_strategy_execution_order(execution_order_id)

            # Step 4: Claim matched zero rows -> duplicate-submission block
            # (skip). Leave execution_status unchanged and continue the batch
            # (Requirements 2.5, 2.6).
            if not claimed:
                print(
                    "[SKIP_DUPLICATE] "
                    f"execution_order_id={execution_order_id}, "
                    "reason=claim matched zero rows "
                    "(already claimed or not REQUESTED)"
                )
                continue

            # Step 6: Claim matched exactly one row -> submit to broker once,
            # then transition SUBMITTING -> SUBMITTED on success or -> FAILED
            # on failure.
            result, submit_attempts = _submit_order_with_rate_limit_retry(
                order,
                retry_count=args.rate_limit_retry_count,
                backoff_seconds=args.rate_limit_backoff_seconds,
            )
            connector_order_request_id = (
                result.get("order_request_id") if result else None
            )

            result_payload = {
                **_order_payload(order),
                "result": result,
                "submit_attempts": submit_attempts,
            }

            if _is_successful_result(result):
                broker_order_no = result.get("broker_order_no")

                # Step 7a: broker already succeeded. Transition SUBMITTING ->
                # SUBMITTED and confirm the actual updated row before treating
                # submission as complete. The broker response is preserved so an
                # operator can reconcile the order.
                #
                # A local-state sync failure here (either a DB exception or a
                # missing updated row) must NOT be re-processed as a normal,
                # retryable broker FAILED: the broker may have already accepted
                # the order. So mark_strategy_execution_order_failed() is not
                # called, SELL_ORDERED follow-up is not performed, and the
                # [SUBMITTED] success log is not printed. A distinct
                # SUBMITTED_STATE_SYNC_FAILED log is emitted with the ids needed
                # for reconciliation, and the batch continues with the next
                # order. The existing SUBMITTING state and the created
                # connector_order_request_id are left intact so no automatic
                # re-submission is triggered.
                try:
                    submitted_row = mark_strategy_execution_order_submitted(
                        execution_order_id=execution_order_id,
                        connector_order_request_id=connector_order_request_id,
                        result_payload=result_payload,
                    )
                except Exception as sync_exc:
                    print(
                        "[SUBMITTED_STATE_SYNC_FAILED] "
                        f"error=SUBMITTED_STATE_SYNC_FAILED, "
                        f"execution_order_id={execution_order_id}, "
                        f"connector_order_request_id={connector_order_request_id}, "
                        f"broker_order_no={broker_order_no}, "
                        f"reason={sync_exc}, error_type={type(sync_exc).__name__}"
                    )
                    continue

                if not submitted_row:
                    print(
                        "[SUBMITTED_STATE_SYNC_FAILED] "
                        f"error=SUBMITTED_STATE_SYNC_FAILED, "
                        f"execution_order_id={execution_order_id}, "
                        f"connector_order_request_id={connector_order_request_id}, "
                        f"broker_order_no={broker_order_no}, "
                        "reason=mark_strategy_execution_order_submitted returned "
                        "no updated row"
                    )
                    continue

                # Step 7b: SUBMITTED row confirmed. Only now run the SELL
                # position follow-up and emit the [SUBMITTED] success log.
                if (
                    order.get("action_type") == "SELL"
                    and order.get("source_position_state_id") is not None
                ):
                    mark_strategy_position_sell_ordered(
                        order["source_position_state_id"]
                    )

                print(
                    "[SUBMITTED] "
                    f"execution_order_id={execution_order_id}, "
                    f"connector_order_request_id={connector_order_request_id}, "
                    f"broker_order_no={broker_order_no}"
                )
            else:
                mark_strategy_execution_order_failed(
                    execution_order_id=execution_order_id,
                    connector_order_request_id=connector_order_request_id,
                    result_payload={
                        **result_payload,
                        "reason": "order result rejected or missing broker response",
                    },
                )
                print(
                    "[FAILED] "
                    f"execution_order_id={execution_order_id}, "
                    "reason=order result rejected or missing broker response"
                )
        except Exception as exc:
            # A Claim (or other pre-submission) error is recorded as a unit
            # failure. The failed-status write itself can also fail if the DB
            # outage persists; that second error must NOT propagate out of
            # run() and abort the whole batch (Requirement: double-fault
            # protection). It is wrapped in its own try/except so the batch
            # continues with the remaining orders. The order was not submitted
            # to the broker on this path.
            try:
                mark_strategy_execution_order_failed(
                    execution_order_id=execution_order_id,
                    result_payload={
                        **_order_payload(order),
                        "reason": str(exc),
                        "error_type": type(exc).__name__,
                    },
                )
                print(
                    f"[FAILED] execution_order_id={execution_order_id}, reason={exc}"
                )
            except Exception as status_exc:
                print(
                    "[CLAIM_FAILED_STATUS_WRITE_FAILED] "
                    f"error=CLAIM_FAILED_STATUS_WRITE_FAILED, "
                    f"execution_order_id={execution_order_id}, "
                    f"claim_error={exc}, "
                    f"claim_error_type={type(exc).__name__}, "
                    f"status_write_error={status_exc}, "
                    f"status_write_error_type={type(status_exc).__name__}"
                )

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Submit REQUESTED strategy execution orders via MarketConnector"
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="실제 주문 제출 및 DB 상태 갱신을 수행",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help=f"조회 개수 제한. 기본값 {DEFAULT_LIMIT}",
    )
    parser.add_argument(
        "--order-sleep-seconds",
        type=float,
        default=DEFAULT_ORDER_SLEEP_SECONDS,
        help=(
            "주문 1건 처리 후 다음 주문 전 대기 초. "
            f"기본값 {DEFAULT_ORDER_SLEEP_SECONDS}"
        ),
    )
    parser.add_argument(
        "--rate-limit-retry-count",
        type=int,
        default=DEFAULT_RATE_LIMIT_RETRY_COUNT,
        help=(
            "EGW00201 rate limit 발생 시 동일 주문 재시도 횟수. "
            f"기본값 {DEFAULT_RATE_LIMIT_RETRY_COUNT}"
        ),
    )
    parser.add_argument(
        "--rate-limit-backoff-seconds",
        type=float,
        default=DEFAULT_RATE_LIMIT_BACKOFF_SECONDS,
        help=(
            "EGW00201 재시도 backoff 기준 초. n번째 재시도 전 n배 대기. "
            f"기본값 {DEFAULT_RATE_LIMIT_BACKOFF_SECONDS}"
        ),
    )
    parser.add_argument(
        "--action",
        choices=["BUY", "SELL"],
        default=None,
        help="특정 주문 방향만 처리",
    )
    parser.add_argument(
        "--plan-id",
        type=int,
        default=None,
        help="특정 execution_plan_id만 처리",
    )
    parser.add_argument(
        "--signal-type",
        default=None,
        help="특정 signal_type만 처리. 예: INTRADAY_STOP_SELL",
    )
    parser.add_argument(
        "--intraday-stop-only",
        action="store_true",
        help="장중 손절 주문만 처리. action=SELL, signal_type=INTRADAY_STOP_SELL을 강제",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.limit <= 0:
        parser.error("--limit must be positive")
    if args.order_sleep_seconds < 0:
        parser.error("--order-sleep-seconds must be non-negative")
    if args.rate_limit_retry_count < 0:
        parser.error("--rate-limit-retry-count must be non-negative")
    if args.rate_limit_backoff_seconds < 0:
        parser.error("--rate-limit-backoff-seconds must be non-negative")
    if args.signal_type:
        args.signal_type = args.signal_type.upper()
    if args.intraday_stop_only and args.action and args.action != "SELL":
        parser.error("--intraday-stop-only requires --action SELL or no --action")
    if args.intraday_stop_only and args.signal_type and args.signal_type != "INTRADAY_STOP_SELL":
        parser.error("--intraday-stop-only requires --signal-type INTRADAY_STOP_SELL or no --signal-type")

    try:
        return run(args)
    except Exception as exc:
        print(f"[ERROR] {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
