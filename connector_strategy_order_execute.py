"""Submit REQUESTED strategy execution orders through MarketConnector.

Default execution is a dry run: it reads target orders and prints the planned
processing order without calling KIS, buy_stock/sell_stock, or DB update paths.
Use --execute only in the guarded paper/aws-paper runtime.
"""

import argparse
import json
import os
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional, Tuple

from psycopg.rows import dict_row

from connector_db import get_conn


DEFAULT_LIMIT = 20
EXECUTION_MODE = "PAPER_STRATEGY"
REQUESTED_STATUS = "REQUESTED"
SUBMITTED_STATUS = "SUBMITTED"
FAILED_STATUS = "FAILED"
RETRYABLE_REJECTION_CODE = "40580000"
RETRYABLE_EXECUTION_STATUSES = ("READY", "FAILED")

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
) -> List[Dict[str, Any]]:
    table_ref, columns = _table_info("strategy_execution_order")
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
) -> List[Dict[str, Any]]:
    """Recover retryable rejected execution orders before Step 12 submission.

    Step 12 submits only REQUESTED orders whose connector_order_request_id is
    NULL. A retryable rejected request, especially KIS paper market-closed
    40580000, can otherwise leave an execution order stuck with an old rejected
    connector_order_request_id.

    Safety gates:
    - PAPER_STRATEGY only
    - BUY/SELL only
    - execution_status in READY/FAILED
    - connector_order_request is REJECTED with rejection_code 40580000
    - broker_order_no is NULL
    - no connector_fill exists for the old request

    In dry-run mode this only prints candidates. In execute mode it detaches the
    old rejected request, sets the execution order back to REQUESTED, and records
    the recovery in result_payload.retry_normalizer.
    """

    order_table_ref, _ = _table_info("strategy_execution_order")
    request_table_ref, _ = _table_info("connector_order_request")
    fill_table_ref, _ = _table_info("connector_fill")

    conditions = [
        "eo.execution_mode = %s",
        "eo.action_type IN ('BUY', 'SELL')",
        "eo.execution_status IN ('READY', 'FAILED')",
        "eo.connector_order_request_id IS NOT NULL",
        "COALESCE(eo.order_qty, 0) > 0",
        "cor.request_status = 'REJECTED'",
        "cor.rejection_code = %s",
        "cor.broker_order_no IS NULL",
        f"NOT EXISTS (SELECT 1 FROM {fill_table_ref} cf WHERE cf.order_request_id = cor.id)",
    ]
    params: List[Any] = [EXECUTION_MODE, RETRYABLE_REJECTION_CODE]

    if action:
        conditions.append("eo.action_type = %s")
        params.append(action)

    if plan_id is not None:
        conditions.append("eo.execution_plan_id = %s")
        params.append(plan_id)

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
                           'normalized_reason', 'RETRY_REJECTED_MARKET_CLOSED_40580000',
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
                   AND execution_status = 'REQUESTED'
                   AND connector_order_request_id IS NULL
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
        "signal_type": action,
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


def _print_order(prefix: str, order: Dict[str, Any]) -> None:
    print(
        f"{prefix} id={order.get('id')}, action={order.get('action_type')}, "
        f"ticker={order.get('ticker_code')}, qty={order.get('order_qty')}, "
        f"mode={order.get('execution_mode')}"
    )


def run(args: argparse.Namespace) -> int:
    action = args.action.upper() if args.action else None
    if args.execute:
        _require_execute_environment()

    normalize_retryable_rejected_orders(
        execute=args.execute,
        action=action,
        plan_id=args.plan_id,
        limit=args.limit,
    )

    orders = fetch_requested_strategy_orders(
        limit=args.limit,
        action=action,
        plan_id=args.plan_id,
    )

    if not orders:
        print("[NO_TARGET] REQUESTED strategy order 없음")
        return 0

    mode = "EXECUTE" if args.execute else "DRY_RUN"
    print(f"[{mode}] REQUESTED strategy order count={len(orders)}")

    if not args.execute:
        for order in orders:
            _print_order("[ORDER]", order)
        return 0

    for order in orders:
        _print_order("[ORDER]", order)
        execution_order_id = order["id"]

        try:
            _validate_order_for_execute(order)
            result = _submit_order(order)
            connector_order_request_id = (
                result.get("order_request_id") if result else None
            )

            result_payload = {
                **_order_payload(order),
                "result": result,
            }

            if _is_successful_result(result):
                mark_strategy_execution_order_submitted(
                    execution_order_id=execution_order_id,
                    connector_order_request_id=connector_order_request_id,
                    result_payload=result_payload,
                )

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
                    f"broker_order_no={result.get('broker_order_no')}"
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
            mark_strategy_execution_order_failed(
                execution_order_id=execution_order_id,
                result_payload={
                    **_order_payload(order),
                    "reason": str(exc),
                    "error_type": type(exc).__name__,
                },
            )
            print(f"[FAILED] execution_order_id={execution_order_id}, reason={exc}")

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
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.limit <= 0:
        parser.error("--limit must be positive")

    try:
        return run(args)
    except Exception as exc:
        print(f"[ERROR] {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
