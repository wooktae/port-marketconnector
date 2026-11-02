"""Scoped PAPER_INTRADAY submission for the scheduled snapshot/evaluate wrapper.

No business execution on import. No bulk READY selection or order POST retry.
Only newly registered IDs or persisted, pre-submission intraday IDs can be submitted.
An ambiguous attempt remains SUBMITTING, blocking subsequent evaluation retries.
"""

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from uuid import uuid4


MODE = "PAPER_INTRADAY"
SIGNAL = "INTRADAY_STOP_SELL"
MAX_AGE_SECONDS = 120


class SafetyBlock(RuntimeError):
    pass


def enabled():
    return os.environ.get("INTRADAY_AUTO_SELL_ENABLED") == "true"


def event(stage, order_id=None):
    # Never log account numbers, broker responses, credentials or exception text.
    print(json.dumps({"component": "intraday_auto", "stage": stage,
                      "executionOrderId": order_id}))


def fresh(timestamp, now):
    return (isinstance(timestamp, datetime) and timestamp.tzinfo is not None
            and 0 <= (now - timestamp).total_seconds() <= MAX_AGE_SECONDS)


def validate(order, position, snapshot, check, account, now, *, allow_historical_check=False):
    if (order["account_no"] != account or position["account_no"] != account
            or snapshot["account_id"] != order["account_id"]
            or snapshot["account_no"] != account or check["account_no"] != account
            or check["account_id"] != order["account_id"]
            or position["account_id"] != order["account_id"]
            or order["action_type"] != "SELL" or order["source_type"] != SIGNAL
            or order["signal_type"] != SIGNAL or order["execution_mode"] != MODE
            or order["order_method"] != "MARKET"
            or order["source_position_state_id"] != position["id"]
            or check["position_state_id"] != position["id"]
            or check["created_execution_order_id"] != order["id"]
            or check["environment"] != "paper" or check["should_stop"] is not True
            or position["position_status"] != "OPEN"):
        raise SafetyBlock("scope")
    if not (order["ticker_code"] == position["ticker_code"]
            == snapshot["ticker_code"] == check["ticker_code"]):
        raise SafetyBlock("ticker")
    qty = order["order_qty"]
    if isinstance(qty, bool) or Decimal(str(qty)) != int(qty) or int(qty) <= 0:
        raise SafetyBlock("quantity")
    if int(qty) > min(int(position["remaining_qty"]), int(snapshot["quantity"]),
                      int(snapshot["sellable_quantity"])):
        raise SafetyBlock("sellable")
    check_ts = check["check_ts"]
    historical_proof = (allow_historical_check and isinstance(check_ts, datetime)
                        and check_ts.tzinfo is not None and check_ts <= now)
    if not fresh(snapshot["as_of_ts"], now) or not (fresh(check_ts, now) or historical_proof):
        raise SafetyBlock("stale")
    entry = Decimal(str(position["entry_price"]))
    price = Decimal(str(snapshot["current_price"]))
    if (entry != Decimal(str(check["entry_price"]))
            or entry <= 0 or price <= 0 or (price - entry) / entry > Decimal("-0.10")
            or Decimal(str(check["pnl_rate"])) > Decimal("-0.10")):
        raise SafetyBlock("threshold")


class Store:
    def __init__(self, connect, row_factory):
        self.connect = connect
        self.row_factory = row_factory

    @contextmanager
    def cycle(self, account):
        # Intraday-only session lock: no change to the Daily locking contract.
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("select pg_try_advisory_lock(hashtextextended(%s, 0))",
                            ("intraday_auto:" + account,))
                acquired = cur.fetchone()[0]
            try:
                yield acquired
            finally:
                if acquired:
                    with conn.cursor() as cur:
                        cur.execute("select pg_advisory_unlock(hashtextextended(%s, 0))",
                                    ("intraday_auto:" + account,))

    def register_created(self, order_id, check_id, account, run_id):
        """Persist provenance before any transient safety check can block READY."""
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    update execution.strategy_execution_order o
                       set result_payload = coalesce(o.result_payload, '{}'::jsonb) || %s::jsonb
                      from execution.strategy_intraday_position_check c
                     where o.id = %s and c.id = %s and o.account_no = %s
                       and o.execution_status = 'READY' and o.execution_mode = 'PAPER_INTRADAY'
                       and o.source_type = 'INTRADAY_STOP_SELL' and o.signal_type = 'INTRADAY_STOP_SELL'
                       and o.action_type = 'SELL' and o.connector_order_request_id is null
                       and c.created_execution_order_id = o.id and c.account_id = o.account_id
                       and c.account_no = o.account_no and c.ticker_code = o.ticker_code
                       and c.position_state_id = o.source_position_state_id
                       and c.environment = 'paper' and c.should_stop = true
                       and not (coalesce(o.result_payload, '{}'::jsonb) ? 'intraday_auto')
                     returning o.id
                """, (json.dumps({"intraday_auto": {"runId": run_id, "originRunId": run_id,
                          "checkId": check_id, "stage": "READY"}}), order_id, check_id, account))
                if not cur.fetchone():
                    raise SafetyBlock("registration")
            conn.commit()

    def pending(self, account):
        """Never adopt arbitrary/legacy untagged READY rows or any attempted order."""
        with self.connect() as conn:
            with conn.cursor(row_factory=self.row_factory) as cur:
                cur.execute("""
                    select o.id, c.id as check_id
                      from execution.strategy_execution_order o
                      join execution.strategy_intraday_position_check c
                        on c.created_execution_order_id = o.id
                       and c.id::text = o.result_payload->'intraday_auto'->>'checkId'
                     where o.account_no = %s and o.execution_mode = 'PAPER_INTRADAY'
                       and o.action_type = 'SELL' and o.source_type = 'INTRADAY_STOP_SELL'
                       and o.signal_type = 'INTRADAY_STOP_SELL'
                       and o.execution_status in ('READY', 'REQUESTED')
                       and o.connector_order_request_id is null
                       and coalesce(o.result_payload->'intraday_auto'->>'runId', '') <> ''
                       and o.result_payload->'intraday_auto'->>'stage' = o.execution_status
                       and c.account_id = o.account_id and c.account_no = o.account_no
                       and c.ticker_code = o.ticker_code
                       and c.position_state_id = o.source_position_state_id
                       and c.environment = 'paper' and c.should_stop = true
                       and not exists (select 1 from connector.connector_order_request r
                                       where r.strategy_run_id = 'intraday:' || o.id::text)
                     order by o.id
                """, (account,))
                return [(row["id"], row["check_id"]) for row in cur.fetchall()]

    def tracked(self, account):
        with self.connect() as conn:
            with conn.cursor(row_factory=self.row_factory) as cur:
                cur.execute("""
                    select o.id, o.account_id, o.ticker_code, o.connector_order_request_id,
                           r.broker_order_no, r.broker_branch_code, r.requested_at,
                           r.request_status as connector_status
                      from execution.strategy_execution_order o
                      join connector.connector_order_request r on r.id = o.connector_order_request_id
                       and r.account_id = o.account_id and r.ticker_code = o.ticker_code
                       and r.request_type = 'SELL' and r.signal_type = 'INTRADAY_STOP_SELL'
                       and r.strategy_run_id = 'intraday:' || o.id::text
                     where o.account_no = %s and o.execution_mode = 'PAPER_INTRADAY'
                       and o.source_type = 'INTRADAY_STOP_SELL' and o.signal_type = 'INTRADAY_STOP_SELL'
                       and o.action_type = 'SELL' and o.execution_status = 'SUBMITTED'
                       and coalesce(o.result_payload->'intraday_auto'->>'runId', '') <> ''
                     order by o.id
                """, (account,))
                return list(cur.fetchall())

    def report_fills(self, account, ids):
        if not ids:
            return
        with self.connect() as conn:
            with conn.cursor(row_factory=self.row_factory) as cur:
                cur.execute("""select id, execution_status from execution.strategy_execution_order
                               where id = any(%s) and account_no = %s
                                 and execution_mode = 'PAPER_INTRADAY' and action_type = 'SELL'
                                 and source_type = 'INTRADAY_STOP_SELL'""", (ids, account))
                rows = cur.fetchall()
                if {row["id"] for row in rows} != set(ids):
                    raise SafetyBlock("fill_scope")
                for row in rows:
                    if row["execution_status"] not in ("FILLED", "SUBMITTED"):
                        raise SafetyBlock("fill_state")
                    event("FILLED" if row["execution_status"] == "FILLED" else "FILL_PENDING", row["id"])

    @staticmethod
    def _confirmed_unaccepted(cur, request):
        """Only explicit broker rejection plus no order/fills/children proves safety.

        FAILED, an empty history, a missing broker number or a timeout alone is
        never evidence of non-acceptance. No broker calls or reconciliation writes.
        """
        if not request or request.get("request_type") != "SELL":
            return False
        response = request.get("response_payload")
        if not isinstance(response, dict):
            return False
        output = response.get("output") or {}
        if (request.get("request_status") not in ("FAILED", "REJECTED")
                or request.get("broker_order_no") or request.get("accepted_at")
                or response.get("rt_cd") != "1"
                or not request.get("rejection_code")
                or response.get("msg_cd") != request["rejection_code"]
                or not isinstance(output, dict) or output.get("ODNO")):
            return False
        cur.execute("""
            select exists (select 1 from connector.connector_fill where order_request_id = %s)
                or exists (select 1 from connector.connector_order_request
                            where parent_order_request_id = %s) as evidence_conflict
        """, (request["id"], request["id"]))
        return not cur.fetchone()["evidence_conflict"]

    def _has_conflicts(self, cur, order):
        scope = (order["account_id"], order["ticker_code"])
        cur.execute("""
            select * from execution.strategy_execution_order
             where account_id = %s and ticker_code = %s and id <> %s and action_type = 'SELL'
               and (execution_status is null or execution_status not in ('FILLED', 'CANCELED', 'REJECTED'))
        """, (*scope, order["id"]))
        strategies = cur.fetchall()
        cur.execute("""
            select * from connector.connector_order_request
             where account_id = %s and ticker_code = %s
               and (request_type = 'SELL' or parent_order_request_id in
                    (select id from connector.connector_order_request
                      where account_id = %s and ticker_code = %s and request_type = 'SELL'))
               and (request_status is null or request_status not in ('FILLED', 'CANCELED', 'REJECTED'))
        """, (*scope, *scope))
        requests = cur.fetchall()
        for other in strategies:
            if (other["execution_status"] != "FAILED"
                    or (other.get("result_payload") or {}).get("intraday_auto", {}).get("stage")
                    in ("SUBMITTING", "UNKNOWN") or not other.get("connector_order_request_id")):
                return True
            cur.execute("""select * from connector.connector_order_request
                           where id = %s and account_id = %s and ticker_code = %s""",
                        (other["connector_order_request_id"], *scope))
            if not self._confirmed_unaccepted(cur, cur.fetchone()):
                return True
        for request in requests:
            if request["request_status"] != "FAILED" or not self._confirmed_unaccepted(cur, request):
                return True
        return False

    def advance(self, order_id, check_id, account, run_id, old, new):
        with self.connect() as conn:
            with conn.cursor(row_factory=self.row_factory) as cur:
                cur.execute("""
                    select * from execution.strategy_execution_order
                    where id = %s for update
                """, (order_id,))
                order = cur.fetchone()
                if not order or order["execution_status"] != old:
                    return None
                if order["connector_order_request_id"] is not None:
                    return None
                cur.execute("""select * from execution.strategy_position_state
                               where id = %s for update""",
                            (order["source_position_state_id"],))
                position = cur.fetchone()
                cur.execute("""select * from connector.connector_position_snapshot
                               where account_id = %s and ticker_code = %s
                               order by as_of_ts desc, id desc limit 1""",
                            (order["account_id"], order["ticker_code"]))
                snapshot = cur.fetchone()
                cur.execute("""select * from execution.strategy_intraday_position_check
                               where id = %s""", (check_id,))
                check = cur.fetchone()
                if not position or not snapshot or not check:
                    raise SafetyBlock("missing")
                payload = order.get("result_payload") or {}
                owner = payload.get("intraday_auto") or {}
                if (not owner.get("runId") or owner.get("checkId") != check_id
                        or owner.get("stage") != old):
                    raise SafetyBlock("provenance")
                if new == "SUBMITTING" and owner["runId"] != run_id:
                    raise SafetyBlock("owner")
                validate(order, position, snapshot, check, account, datetime.now(timezone.utc),
                         allow_historical_check=True)
                if self._has_conflicts(cur, order):
                    raise SafetyBlock("active_sell")
                owner = {**owner, "runId": run_id, "checkId": check_id, "stage": new}
                cur.execute("""
                    update execution.strategy_execution_order
                       set execution_status = %s, updated_at = now(),
                           result_payload = coalesce(result_payload, '{}'::jsonb) || %s::jsonb
                     where id = %s and execution_status = %s
                       and execution_mode = 'PAPER_INTRADAY' and action_type = 'SELL'
                       and source_type = 'INTRADAY_STOP_SELL'
                       and signal_type = 'INTRADAY_STOP_SELL' and account_no = %s
                       and connector_order_request_id is null
                       and not exists (select 1 from connector.connector_order_request r
                                       where r.strategy_run_id = 'intraday:' || strategy_execution_order.id::text)
                     returning *
                """, (new, json.dumps({"intraday_auto": owner}), order_id, old, account))
                updated = cur.fetchone()
            conn.commit()
        return updated

    def submitted(self, order, request_id, run_id):
        with self.connect() as conn:
            with conn.cursor(row_factory=self.row_factory) as cur:
                cur.execute("""select id from connector.connector_order_request
                               where id = %s and account_id = %s and ticker_code = %s
                                 and request_type = 'SELL' and signal_type = 'INTRADAY_STOP_SELL'
                                 and strategy_run_id = %s and order_qty = %s
                                 and request_status in ('ACCEPTED', 'SUBMITTED', 'FILLED', 'PARTIAL_FILLED')
                                 and broker_order_no is not null""",
                            (request_id, order["account_id"], order["ticker_code"],
                             "intraday:" + str(order["id"]), order["order_qty"]))
                if not cur.fetchone():
                    raise SafetyBlock("request_scope")
                cur.execute("""
                    update execution.strategy_execution_order
                       set execution_status = 'SUBMITTED', connector_order_request_id = %s,
                           updated_at = now()
                     where id = %s and execution_status = 'SUBMITTING'
                       and execution_mode = 'PAPER_INTRADAY'
                       and result_payload->'intraday_auto'->>'runId' = %s
                       and connector_order_request_id is null
                     returning id
                """, (request_id, order["id"], run_id))
                if not cur.fetchone():
                    raise SafetyBlock("state_sync")
                cur.execute("""update execution.strategy_position_state
                               set position_status = 'SELL_ORDERED', updated_at = now()
                               where id = %s and position_status = 'OPEN' returning id""",
                            (order["source_position_state_id"],))
                if not cur.fetchone():
                    raise SafetyBlock("position_sync")
            conn.commit()


def submit_created(store, created, account, run_id, sell, is_enabled=enabled):
    errors = 0
    for order_id, check_id in created:
        if not is_enabled():
            break
        try:
            order = store.advance(order_id, check_id, account, run_id, "READY", "REQUESTED")
            if order is None:
                # Re-own only a proven, never-attempted REQUESTED row. This is
                # intentionally NOT a retry of SUBMITTING or a broker request.
                order = store.advance(order_id, check_id, account, run_id, "REQUESTED", "REQUESTED")
                if order is None:
                    continue
            if not is_enabled():
                break
            order = store.advance(order_id, check_id, account, run_id, "REQUESTED", "SUBMITTING")
            if order is None:
                continue
            if not is_enabled():
                event("STOPPED_AFTER_CLAIM", order_id)
                return 1
            result = sell(stock_code=order["ticker_code"], qty=int(order["order_qty"]),
                          order_method="MARKET", order_price=0,
                          signal_type=SIGNAL, strategy_run_id="intraday:" + str(order_id),
                          retry_token_errors=False)
            if not (isinstance(result, dict) and result.get("order_request_id")
                    and result.get("broker_order_no")
                    and (result.get("response") or {}).get("rt_cd") == "0"):
                # Includes explicit rejection: no automatic retry or new candidate.
                raise SafetyBlock("unconfirmed")
            store.submitted(order, result["order_request_id"], run_id)
            event("SUBMITTED", order_id)
        except Exception:
            # Never reset SUBMITTING, including HTTP/JSON/DB failures after acceptance.
            errors += 1
            event("ERROR_RECONCILIATION_REQUIRED", order_id)
    return 1 if errors else 0


def run_cycle(store, account, refresh, evaluate, sell, args, is_enabled=enabled, *, observe=None):
    with store.cycle(account) as acquired:
        if not acquired:
            event("DUPLICATE_CYCLE")
            return 0
        run_id = str(uuid4())
        if observe is not None:
            try:
                observe(store, account, run_id)
            except Exception:
                event("FILL_SYNC_ERROR")
                return 1
        if refresh() != 0:
            event("SNAPSHOT_FAILED")
            return 1
        created = store.pending(account)
        def on_created(order_id, check_id):
            store.register_created(order_id, check_id, account, run_id)
            created.append((order_id, check_id))
        status = evaluate(args, on_created=on_created)
        if status != 0:
            return status
        status = submit_created(store, list(dict.fromkeys(created)), account, run_id, sell, is_enabled)
        if observe is not None:
            try:
                observe(store, account, run_id)
            except Exception:
                event("FILL_SYNC_ERROR")
                return 1
        return status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account-no", required=True)
    args = parser.parse_args()
    from connector_intraday_settings import apply_settings
    from connector_intraday_reconcile import reconcile, validate_fill_config, sync_ecs
    settings = apply_settings()
    if enabled():
        validate_fill_config(settings.get("fillSync"))
    # Lazy imports: tests/imports never load credentials or contact services.
    from config import PAPER_ACNT
    from connector_db import get_conn
    from psycopg.rows import dict_row
    from connector_intraday_snapshot_refresh import refresh_intraday_snapshot
    from connector_intraday_position_evaluate import build_parser, run
    from connector_sell import sell_stock
    from connector_order_check import fetch_and_save_orders
    if (args.account_no != PAPER_ACNT or os.environ.get("PORT_ENVIRONMENT") != "paper"
            or os.environ.get("PORT_DB_TARGET") != "aws-paper"
            or os.environ.get("PORTFOLIO_ENV", "paper") != "paper"):
        raise SafetyBlock("environment")
    evaluation_args = build_parser().parse_args([
        "--account-no", args.account_no, "--create-order", "--notify-slack",
        "--hard-stop-rate=-0.10", "--max-snapshot-age-minutes=2"])
    if not enabled():
        evaluation_args.create_order = False
        evaluation_args.notify_slack = False
    preflight_done = False
    def observe(store, account, run_id):
        nonlocal preflight_done
        if enabled() and not preflight_done:
            # Verify task permission/image API before any new broker POST.
            sync_ecs(settings["fillSync"], None, [], run_id, preflight=True)
            preflight_done = True
        ids = reconcile(store, account, run_id, settings.get("fillSync", {}), fetch_and_save_orders)
        store.report_fills(account, ids)
    return run_cycle(Store(get_conn, dict_row), args.account_no,
                     refresh_intraday_snapshot, run, sell_stock, evaluation_args, observe=observe)


if __name__ == "__main__":
    raise SystemExit(main())
