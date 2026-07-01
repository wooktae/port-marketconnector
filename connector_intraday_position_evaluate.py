"""Evaluate intraday strategy positions from connector snapshots.

Safety-first initial version:
- Reads execution.strategy_position_state where position_status = 'OPEN' only.
- Reads latest connector snapshots.
- Inserts execution.strategy_intraday_position_check for OPEN positions only.
- Does NOT create strategy_execution_order by default.
- Broker order submission is impossible in this file.
"""

import argparse
import json
import os
import subprocess
import tempfile
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from psycopg.rows import dict_row

from connector_db import get_conn


SOURCE_VERSION = "connector-intraday-position-evaluate-1.1.1-slack-notify"
DEFAULT_ENVIRONMENT = os.environ.get("PORTFOLIO_ENV", "paper")
DEFAULT_HARD_STOP_RATE = Decimal(os.environ.get("INTRADAY_HARD_STOP_RATE", "-0.10"))
DEFAULT_MAX_SNAPSHOT_AGE_MINUTES = int(os.environ.get("INTRADAY_MAX_SNAPSHOT_AGE_MINUTES", "30"))


def _json_default(value: Any) -> str:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def _json_payload(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=_json_default)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _decimal_or_none(value: Any) -> Optional[Decimal]:
    if value is None:
        return None
    return Decimal(str(value))


def _rate(numerator: Optional[Decimal], denominator: Optional[Decimal]) -> Optional[Decimal]:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def fetch_latest_balance(account_no: Optional[str]) -> Optional[Dict[str, Any]]:
    params: List[Any] = []
    where = ""
    if account_no:
        where = "where account_no = %s"
        params.append(account_no)

    sql = f"""
        select *
        from connector.connector_balance_snapshot
        {where}
        order by as_of_ts desc, id desc
        limit 1
    """

    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, tuple(params))
            row = cur.fetchone()
            return dict(row) if row else None


def fetch_latest_positions(account_no: Optional[str]) -> Dict[str, Dict[str, Any]]:
    params: List[Any] = []
    where = ""
    if account_no:
        where = "where account_no = %s"
        params.append(account_no)

    sql = f"""
        with latest_date as (
            select max(as_of_date) as as_of_date
            from connector.connector_position_snapshot
            {where}
        )
        select cps.*
        from connector.connector_position_snapshot cps
        join latest_date ld on ld.as_of_date = cps.as_of_date
        where (%s::varchar is null or cps.account_no = %s)
        order by cps.ticker_code
    """

    effective_account = account_no if account_no else None

    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, (effective_account, effective_account))
            rows = [dict(row) for row in cur.fetchall()]

    return {row["ticker_code"]: row for row in rows}


def fetch_open_positions(account_no: Optional[str]) -> List[Dict[str, Any]]:
    params: List[Any] = []
    conditions = ["position_status = 'OPEN'"]

    if account_no:
        conditions.append("account_no = %s")
        params.append(account_no)

    sql = f"""
        select
          id,
          account_id,
          account_no,
          ticker_code,
          stock_name,
          entry_date,
          entry_price,
          entry_qty,
          remaining_qty,
          position_status,
          created_at,
          updated_at
        from execution.strategy_position_state
        where {" and ".join(conditions)}
        order by id
    """

    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, tuple(params))
            return [dict(row) for row in cur.fetchall()]


def count_non_open_active(account_no: Optional[str]) -> List[Dict[str, Any]]:
    params: List[Any] = []
    conditions = ["position_status in ('SELL_READY', 'SELL_ORDERED')"]

    if account_no:
        conditions.append("account_no = %s")
        params.append(account_no)

    sql = f"""
        select position_status, count(*) as cnt
        from execution.strategy_position_state
        where {" and ".join(conditions)}
        group by position_status
        order by position_status
    """

    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, tuple(params))
            return [dict(row) for row in cur.fetchall()]


def existing_intraday_stop_order(position_state_id: int) -> Optional[Dict[str, Any]]:
    sql = """
        select id, execution_status
        from execution.strategy_execution_order
        where source_position_state_id = %s
          and source_type = 'INTRADAY_STOP_SELL'
          and action_type = 'SELL'
          and execution_status in ('READY', 'REQUESTED', 'SUBMITTED', 'ACCEPTED', 'PARTIAL_FILLED')
        order by id desc
        limit 1
    """
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, (position_state_id,))
            row = cur.fetchone()
            return dict(row) if row else None


def snapshot_age_minutes(snapshot_ts: Optional[datetime], now_ts: datetime) -> Optional[Decimal]:
    if snapshot_ts is None:
        return None
    if snapshot_ts.tzinfo is None:
        snapshot_ts = snapshot_ts.replace(tzinfo=timezone.utc)
    return Decimal(str((now_ts - snapshot_ts).total_seconds() / 60.0))


def evaluate_position(
    position: Dict[str, Any],
    snapshot: Optional[Dict[str, Any]],
    *,
    now_ts: datetime,
    hard_stop_rate: Decimal,
    max_snapshot_age_minutes: int,
) -> Dict[str, Any]:
    warnings: List[str] = []

    entry_price = _decimal_or_none(position.get("entry_price"))
    remaining_qty = int(position.get("remaining_qty") or 0)

    snapshot_qty = None
    sellable_qty = None
    current_price = None
    snapshot_current_price = None
    snapshot_ts = None

    if not snapshot:
        warnings.append("NO_CONNECTOR_POSITION_SNAPSHOT")
    else:
        snapshot_qty = int(snapshot.get("quantity") or 0)
        sellable_qty = int(snapshot.get("sellable_quantity") or 0)
        current_price = _decimal_or_none(snapshot.get("current_price"))
        snapshot_current_price = current_price
        snapshot_ts = snapshot.get("as_of_ts")

        age = snapshot_age_minutes(snapshot_ts, now_ts)
        if age is None:
            warnings.append("SNAPSHOT_TS_MISSING")
        elif age > Decimal(max_snapshot_age_minutes):
            warnings.append(f"STALE_SNAPSHOT:{age:.2f}m")

        if snapshot_qty <= 0:
            warnings.append("SNAPSHOT_QTY_ZERO_OR_MISSING")
        if sellable_qty is None or sellable_qty <= 0:
            warnings.append("SELLABLE_QTY_ZERO_OR_MISSING")
        if current_price is None or current_price <= 0:
            warnings.append("CURRENT_PRICE_ZERO_OR_MISSING")

    pnl_rate = None
    expected_pnl_amount = None
    should_stop = False
    stop_reason = None

    if current_price is not None and entry_price is not None and entry_price > 0:
        pnl_rate = (current_price - entry_price) / entry_price
        expected_pnl_amount = (current_price - entry_price) * Decimal(remaining_qty)

    if (
        pnl_rate is not None
        and pnl_rate <= hard_stop_rate
        and remaining_qty > 0
        and sellable_qty is not None
        and sellable_qty > 0
        and current_price is not None
        and current_price > 0
        and not any(w.startswith("STALE_SNAPSHOT") for w in warnings)
    ):
        should_stop = True
        stop_reason = "INTRADAY_HARD_STOP"

    if existing_intraday_stop_order(int(position["id"])):
        warnings.append("DUPLICATE_INTRADAY_STOP_ORDER_READY_OR_SUBMITTED")
        should_stop = False
        stop_reason = "DUPLICATE_INTRADAY_STOP_ORDER"

    raw_payload = {
        "source": SOURCE_VERSION,
        "position": position,
        "snapshot": snapshot,
        "rules": {
            "hard_stop_rate": hard_stop_rate,
            "max_snapshot_age_minutes": max_snapshot_age_minutes,
            "target_position_status": "OPEN",
        },
        "warnings": warnings,
    }

    return {
        "check_date": now_ts.date(),
        "check_ts": now_ts,
        "account_id": position.get("account_id"),
        "account_no": position.get("account_no"),
        "environment": DEFAULT_ENVIRONMENT,
        "position_state_id": position.get("id"),
        "ticker_code": position.get("ticker_code"),
        "stock_name": position.get("stock_name"),
        "entry_date": position.get("entry_date"),
        "entry_price": entry_price,
        "remaining_qty": remaining_qty,
        "snapshot_qty": snapshot_qty,
        "sellable_qty": sellable_qty,
        "current_price": current_price,
        "snapshot_current_price": snapshot_current_price,
        "previous_close": None,
        "pnl_rate": pnl_rate,
        "intraday_drop_rate": None,
        "hard_stop_rate": hard_stop_rate,
        "should_stop": should_stop,
        "stop_reason": stop_reason,
        "created_execution_order_id": None,
        "warning_count": len(warnings),
        "warnings": warnings,
        "raw_payload": raw_payload,
        "expected_pnl_amount": expected_pnl_amount,
    }



def create_intraday_stop_order(record: Dict[str, Any], check_id: int) -> int:
    """Create READY execution order only. This function never submits broker orders."""
    signal_reason = {
        "source": SOURCE_VERSION,
        "reason": "INTRADAY_HARD_STOP",
        "check_id": check_id,
        "position_state_id": record.get("position_state_id"),
        "hard_stop_rate": record.get("hard_stop_rate"),
        "pnl_rate": record.get("pnl_rate"),
        "warnings": record.get("warnings") or [],
    }

    validation_result = {
        "source": SOURCE_VERSION,
        "decision_type": "SELL",
        "decision_status": "READY",
        "sell_reason": record.get("stop_reason"),
        "check_id": check_id,
        "position_state_id": record.get("position_state_id"),
        "expected_pnl_rate": record.get("pnl_rate"),
        "expected_pnl_amount": record.get("expected_pnl_amount"),
        "expected_sell_price": record.get("current_price"),
        "snapshot_qty": record.get("snapshot_qty"),
        "sellable_qty": record.get("sellable_qty"),
    }

    sell_info = {
        "source": SOURCE_VERSION,
        "check_id": check_id,
        "position_state_id": record.get("position_state_id"),
        "entry_date": record.get("entry_date"),
        "entry_price": record.get("entry_price"),
        "remaining_qty": record.get("remaining_qty"),
        "snapshot_qty": record.get("snapshot_qty"),
        "sellable_qty": record.get("sellable_qty"),
        "current_price": record.get("current_price"),
        "pnl_rate": record.get("pnl_rate"),
        "hard_stop_rate": record.get("hard_stop_rate"),
        "stop_reason": record.get("stop_reason"),
    }

    order_qty = min(
        int(record.get("remaining_qty") or 0),
        int(record.get("sellable_qty") or 0),
    )

    if order_qty <= 0:
        raise ValueError("INTRADAY_STOP_ORDER_QTY_ZERO")

    sql = """
        insert into execution.strategy_execution_order (
          execution_plan_id,
          strategy_signal_id,
          account_id,
          account_no,
          signal_date,
          ticker_code,
          stock_name,
          action_type,
          signal_type,
          signal_score,
          signal_position_size,
          market_signal,
          market_regime_score,
          flow_score,
          tape_score,
          final_score,
          short_pressure_score,
          target_weight,
          target_amount,
          current_qty,
          current_eval_amount,
          order_qty,
          order_price,
          order_method,
          execution_status,
          block_reason,
          approval_required,
          signal_reason,
          validation_result,
          request_payload,
          result_payload,
          source_type,
          execution_mode,
          source_position_state_id,
          sell_reason,
          sell_info,
          expected_sell_price,
          expected_pnl_amount,
          expected_pnl_rate
        )
        values (
          0,
          null,
          %(account_id)s,
          %(account_no)s,
          %(signal_date)s,
          %(ticker_code)s,
          %(stock_name)s,
          'SELL',
          'INTRADAY_STOP_SELL',
          null,
          null,
          null,
          null,
          null,
          null,
          null,
          null,
          null,
          null,
          %(current_qty)s,
          %(current_eval_amount)s,
          %(order_qty)s,
          %(order_price)s,
          'MARKET',
          'READY',
          null,
          true,
          %(signal_reason)s::jsonb,
          %(validation_result)s::jsonb,
          %(request_payload)s::jsonb,
          %(result_payload)s::jsonb,
          'INTRADAY_STOP_SELL',
          %(execution_mode)s,
          %(source_position_state_id)s,
          %(sell_reason)s,
          %(sell_info)s::jsonb,
          %(expected_sell_price)s,
          %(expected_pnl_amount)s,
          %(expected_pnl_rate)s
        )
        returning id
    """

    params = {
        "account_id": record.get("account_id"),
        "account_no": record.get("account_no"),
        "signal_date": record.get("check_date"),
        "ticker_code": record.get("ticker_code"),
        "stock_name": record.get("stock_name"),
        "current_qty": record.get("remaining_qty"),
        "current_eval_amount": (
            record.get("current_price") * Decimal(order_qty)
            if record.get("current_price") is not None
            else None
        ),
        "order_qty": order_qty,
        "order_price": record.get("current_price"),
        "signal_reason": _json_payload(signal_reason),
        "validation_result": _json_payload(validation_result),
        "request_payload": _json_payload({}),
        "result_payload": _json_payload({}),
        "execution_mode": DEFAULT_ENVIRONMENT.upper() + "_INTRADAY",
        "source_position_state_id": record.get("position_state_id"),
        "sell_reason": record.get("stop_reason"),
        "sell_info": _json_payload(sell_info),
        "expected_sell_price": record.get("current_price"),
        "expected_pnl_amount": record.get("expected_pnl_amount"),
        "expected_pnl_rate": record.get("pnl_rate"),
    }

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
        conn.commit()

    return int(row[0])



def _percent_for_slack(value: Any) -> Optional[float]:
    """Convert stored rate ratio such as -0.042 into Slack display percent -4.2."""
    if value is None:
        return None
    try:
        dec = Decimal(str(value))
        return float((dec * Decimal("100")).quantize(Decimal("0.01")))
    except Exception:
        return None


def notify_intraday_stop_slack(
    record: Dict[str, Any],
    *,
    check_id: int,
    created_order_id: int,
    slack_function_name: str,
    slack_region: str,
) -> bool:
    """Notify Slack after INTRADAY_STOP_SELL READY execution order is created.

    Safety:
    - This function only invokes the Slack notifier Lambda.
    - It never submits broker orders.
    - It is called only after READY order creation and check attachment succeed.
    """
    order_qty = min(
        int(record.get("remaining_qty") or 0),
        int(record.get("sellable_qty") or 0),
    )

    payload = {
        "eventType": "INTRADAY_STOP_LOSS",
        "stockName": record.get("stock_name") or record.get("ticker_code") or "-",
        "quantity": order_qty,
        "condition": record.get("stop_reason") or "INTRADAY_STOP_SELL READY",
        "evalProfitRate": _percent_for_slack(record.get("pnl_rate")),
        "runDate": str(record.get("check_date") or date.today()),
        "source": "connector_intraday_position_evaluate",
        "tickerCode": record.get("ticker_code"),
        "executionOrderId": created_order_id,
        "checkId": check_id,
    }

    with tempfile.TemporaryDirectory(prefix="intraday_stop_slack_") as tmp_dir:
        payload_path = os.path.join(tmp_dir, "payload.json")
        response_path = os.path.join(tmp_dir, "response.json")

        with open(payload_path, "w", encoding="utf-8") as fp:
            json.dump(payload, fp, ensure_ascii=False, default=_json_default)

        cmd = [
            "aws",
            "lambda",
            "invoke",
            "--region",
            slack_region,
            "--function-name",
            slack_function_name,
            "--payload",
            f"fileb://{payload_path}",
            response_path,
        ]

        result = subprocess.run(
            cmd,
            check=False,
            text=True,
            capture_output=True,
        )

        if result.returncode != 0:
            print(
                "[WARN] INTRADAY_STOP_SLACK_NOTIFY_FAILED "
                f"returncode={result.returncode}, "
                f"stderr={result.stderr[:500]}"
            )
            return False

        print(
            "[SLACK_NOTIFIED] "
            f"eventType=INTRADAY_STOP_LOSS, "
            f"ticker_code={record.get('ticker_code')}, "
            f"stock_name={record.get('stock_name')}, "
            f"check_id={check_id}, "
            f"execution_order_id={created_order_id}"
        )
        return True


def attach_created_execution_order(check_id: int, execution_order_id: int) -> None:
    sql = """
        update execution.strategy_intraday_position_check
           set created_execution_order_id = %s
         where id = %s
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (execution_order_id, check_id))
        conn.commit()



def insert_intraday_check(record: Dict[str, Any]) -> int:
    sql = """
        insert into execution.strategy_intraday_position_check (
          check_date,
          check_ts,
          account_id,
          account_no,
          environment,
          position_state_id,
          ticker_code,
          stock_name,
          entry_date,
          entry_price,
          remaining_qty,
          snapshot_qty,
          sellable_qty,
          current_price,
          snapshot_current_price,
          previous_close,
          pnl_rate,
          intraday_drop_rate,
          hard_stop_rate,
          should_stop,
          stop_reason,
          created_execution_order_id,
          warning_count,
          warnings,
          raw_payload
        )
        values (
          %(check_date)s,
          %(check_ts)s,
          %(account_id)s,
          %(account_no)s,
          %(environment)s,
          %(position_state_id)s,
          %(ticker_code)s,
          %(stock_name)s,
          %(entry_date)s,
          %(entry_price)s,
          %(remaining_qty)s,
          %(snapshot_qty)s,
          %(sellable_qty)s,
          %(current_price)s,
          %(snapshot_current_price)s,
          %(previous_close)s,
          %(pnl_rate)s,
          %(intraday_drop_rate)s,
          %(hard_stop_rate)s,
          %(should_stop)s,
          %(stop_reason)s,
          %(created_execution_order_id)s,
          %(warning_count)s,
          %(warnings)s::jsonb,
          %(raw_payload)s::jsonb
        )
        returning id
    """

    params = dict(record)
    params["warnings"] = _json_payload(record["warnings"])
    params["raw_payload"] = _json_payload(record["raw_payload"])

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
        conn.commit()
    return int(row[0])


def run(args: argparse.Namespace) -> int:
    now_ts = _now_utc()
    account_no = args.account_no

    print("===== INTRADAY_POSITION_EVALUATE START =====")
    print(f"[INFO] source_version={SOURCE_VERSION}")
    print(f"[INFO] account_no={account_no or 'ALL'}")
    print(f"[INFO] create_order={args.create_order}")
    print(f"[INFO] notify_slack={args.notify_slack}")
    print(f"[INFO] slack_function_name={args.slack_function_name if args.notify_slack else 'DISABLED'}")
    print(f"[INFO] slack_region={args.slack_region if args.notify_slack else 'DISABLED'}")
    print(f"[INFO] hard_stop_rate={args.hard_stop_rate}")
    print(f"[INFO] max_snapshot_age_minutes={args.max_snapshot_age_minutes}")

    if args.create_order:
        print("[INFO] create_order mode enabled: READY execution orders may be created, but broker submission is still impossible in this file.")

    balance = fetch_latest_balance(account_no)
    if balance:
        print(
            "[INFO] latest_balance: "
            f"account_no={balance.get('account_no')}, "
            f"as_of_date={balance.get('as_of_date')}, "
            f"as_of_ts={balance.get('as_of_ts')}, "
            f"source_version={balance.get('source_version')}"
        )
    else:
        print("[WARN] latest_balance not found")

    non_open_active = count_non_open_active(account_no)
    if non_open_active:
        print(f"[INFO] non-open active positions ignored: {_json_payload(non_open_active)}")

    open_positions = fetch_open_positions(account_no)
    print(f"[INFO] open_position_count={len(open_positions)}")

    if not open_positions:
        print("[OK] no OPEN strategy positions. evaluate finished with no inserts and no orders.")
        print("===== INTRADAY_POSITION_EVALUATE END: EMPTY_NORMAL =====")
        return 0

    snapshots = fetch_latest_positions(account_no)
    print(f"[INFO] latest_position_snapshot_count={len(snapshots)}")

    inserted = 0
    should_stop_count = 0
    warning_count = 0

    hard_stop_rate = Decimal(str(args.hard_stop_rate))

    for position in open_positions:
        ticker_code = position["ticker_code"]
        snapshot = snapshots.get(ticker_code)
        evaluation = evaluate_position(
            position,
            snapshot,
            now_ts=now_ts,
            hard_stop_rate=hard_stop_rate,
            max_snapshot_age_minutes=args.max_snapshot_age_minutes,
        )

        if args.dry_run:
            print(
                "[DRY_RUN] "
                f"position_state_id={position['id']}, "
                f"ticker_code={ticker_code}, "
                f"should_stop={evaluation['should_stop']}, "
                f"warning_count={evaluation['warning_count']}, "
                f"warnings={_json_payload(evaluation['warnings'])}"
            )
        else:
            check_id = insert_intraday_check(evaluation)
            inserted += 1
            created_order_id = None

            if args.create_order and evaluation["should_stop"]:
                created_order_id = create_intraday_stop_order(evaluation, check_id)
                attach_created_execution_order(check_id, created_order_id)

                if args.notify_slack:
                    notify_intraday_stop_slack(
                        evaluation,
                        check_id=check_id,
                        created_order_id=created_order_id,
                        slack_function_name=args.slack_function_name,
                        slack_region=args.slack_region,
                    )

                print(
                    "[CREATED_ORDER] "
                    f"execution_order_id={created_order_id}, "
                    f"check_id={check_id}, "
                    f"position_state_id={position['id']}, "
                    f"ticker_code={ticker_code}, "
                    f"signal_type=INTRADAY_STOP_SELL, "
                    f"execution_status=READY"
                )

            print(
                "[INSERTED] "
                f"check_id={check_id}, "
                f"position_state_id={position['id']}, "
                f"ticker_code={ticker_code}, "
                f"should_stop={evaluation['should_stop']}, "
                f"created_order_id={created_order_id}, "
                f"warning_count={evaluation['warning_count']}"
            )

        if evaluation["should_stop"]:
            should_stop_count += 1
        if evaluation["warning_count"]:
            warning_count += 1

    print(
        "[SUMMARY] "
        f"open_positions={len(open_positions)}, "
        f"inserted_checks={inserted}, "
        f"should_stop_count={should_stop_count}, "
        f"warning_position_count={warning_count}, "
        f"create_order={args.create_order}"
    )
    print("===== INTRADAY_POSITION_EVALUATE END =====")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate intraday position risk from connector snapshots."
    )
    parser.add_argument("--account-no", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--create-order", action="store_true")
    parser.add_argument("--notify-slack", action="store_true")
    parser.add_argument("--slack-function-name", default="portfolio-event-notifier")
    parser.add_argument("--slack-region", default="ap-northeast-2")
    parser.add_argument("--hard-stop-rate", default=str(DEFAULT_HARD_STOP_RATE))
    parser.add_argument("--max-snapshot-age-minutes", type=int, default=DEFAULT_MAX_SNAPSHOT_AGE_MINUTES)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
