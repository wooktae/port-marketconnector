"""Offline intraday lifecycle tests. No application imports or service connections."""

import argparse
import ast
import importlib.util
import json
import random
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional
from unittest.mock import MagicMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("intraday_under_test", ROOT / "connector_intraday_auto_run.py")
auto = importlib.util.module_from_spec(spec)
spec.loader.exec_module(auto)


def records():
    now = datetime.now(timezone.utc)
    order = {"id": 10, "account_id": 2, "account_no": "TEST_ACCOUNT", "ticker_code": "TEST_STOCK",
                 "action_type": "SELL", "source_type": auto.SIGNAL, "signal_type": auto.SIGNAL,
                 "execution_mode": auto.MODE, "order_method": "MARKET", "source_position_state_id": 3,
                 "order_qty": 2, "execution_status": "READY", "connector_order_request_id": None,
                 "result_payload": {"intraday_auto": {"runId": "TEST_RUN", "checkId": 4, "stage": "READY"}}}
    position = {"id": 3, "account_id": 2, "account_no": "TEST_ACCOUNT", "ticker_code": "TEST_STOCK",
                    "position_status": "OPEN", "remaining_qty": 2, "entry_price": 100}
    snapshot = {"account_id": 2, "account_no": "TEST_ACCOUNT", "ticker_code": "TEST_STOCK",
                    "quantity": 2, "sellable_quantity": 2, "as_of_ts": now, "current_price": 90}
    check = {"account_id": 2, "account_no": "TEST_ACCOUNT", "ticker_code": "TEST_STOCK",
                 "position_state_id": 3, "created_execution_order_id": 10, "environment": "paper",
                 "should_stop": True, "check_ts": now, "pnl_rate": Decimal("-0.10"), "entry_price": 100}
    return order, position, snapshot, check


class MemoryStore:
    def __init__(self):
        self.order = records()[0]
        self.transitions = []
        self.locked = False
        self.conflict = False
        self.sync_error = False
        self.lost_claim = False
        self.resume_pending = False

    def pending(self, account):
        if (self.resume_pending and self.order["execution_status"] in ("READY", "REQUESTED")
                and self.order["connector_order_request_id"] is None):
            return [(10, 4)]
        return []

    def register_created(self, order_id, check_id, account, run_id):
        self.order["result_payload"] = {"intraday_auto": {
            "runId": run_id, "originRunId": run_id, "checkId": check_id, "stage": "READY"}}
        self.resume_pending = True

    @contextmanager
    def cycle(self, account):
        yield not self.locked

    def advance(self, order_id, check_id, account, run_id, old, new):
        if order_id != 10 or check_id != 4 or account != "TEST_ACCOUNT":
            raise auto.SafetyBlock("scope")
        if self.conflict:
            raise auto.SafetyBlock("active_sell")
        if self.order["execution_status"] != old or (new == "SUBMITTING" and self.lost_claim):
            return None
        self.order["execution_status"] = new
        self.order["result_payload"]["intraday_auto"].update(runId=run_id, stage=new)
        self.transitions.append(new)
        return dict(self.order)

    def submitted(self, order, request_id, run_id):
        if self.sync_error:
            raise RuntimeError("synthetic sync failure")
        self.order["execution_status"] = "SUBMITTED"
        self.order["connector_order_request_id"] = request_id
        self.transitions.append("SUBMITTED")


class OfflineCase(unittest.TestCase):
    def setUp(self):
        for target in ("socket.socket", "socket.create_connection", "urllib.request.urlopen",
                       "subprocess.Popen"):
            guard = patch(target, side_effect=AssertionError("External access forbidden"))
            blocked = guard.start()
            self.addCleanup(guard.stop)
            self.addCleanup(blocked.assert_not_called)
        self.store = MemoryStore()
        self.sell = Mock(return_value={"order_request_id": 7, "broker_order_no": "MOCK",
                                       "response": {"rt_cd": "0"}})
        self.enabled = Mock(return_value=True)

    def submit(self, created=None):
        return auto.submit_created(self.store, [(10, 4)] if created is None else created,
                                   "TEST_ACCOUNT", "TEST_RUN", self.sell, self.enabled)


class LifecycleTests(OfflineCase):
    def test_temporarily_blocked_ready_resumes_next_cycle_after_refresh(self):
        self.store.resume_pending = True
        self.store.conflict = True
        self.assertEqual(self.submit(), 1)
        self.assertEqual(self.store.order["execution_status"], "READY")
        def refresh():
            self.store.conflict = False
            return 0
        self.assertEqual(auto.run_cycle(self.store, "TEST_ACCOUNT", refresh, Mock(return_value=0),
                                       self.sell, object(), self.enabled), 0)
        self.sell.assert_called_once()
        self.assertEqual(self.store.order["execution_status"], "SUBMITTED")

    def test_requested_interrupted_before_claim_resumes_with_new_run(self):
        self.enabled.side_effect = [True, False]
        self.assertEqual(self.submit(), 0)
        self.assertEqual(self.store.order["execution_status"], "REQUESTED")
        self.store.resume_pending = True
        self.enabled.side_effect = None
        self.assertEqual(auto.run_cycle(self.store, "TEST_ACCOUNT", Mock(return_value=0),
                                       Mock(return_value=0), self.sell, object(), self.enabled), 0)
        self.sell.assert_called_once()
        self.assertNotEqual(self.store.order["result_payload"]["intraday_auto"]["runId"], "TEST_RUN")

    def test_submitting_and_unknown_attempts_never_resume_next_cycle(self):
        self.store.resume_pending = True
        self.sell.side_effect = TimeoutError("synthetic response loss")
        self.assertEqual(self.submit(), 1)
        self.assertEqual(auto.run_cycle(self.store, "TEST_ACCOUNT", Mock(return_value=0),
                                       Mock(return_value=0), self.sell, object(), self.enabled), 0)
        self.sell.assert_called_once()
        self.assertEqual(self.store.order["execution_status"], "SUBMITTING")

    def test_registered_ready_survives_evaluation_failure_for_later_revalidation(self):
        def evaluate(args, *, on_created):
            on_created(10, 4)
            return 1
        self.assertEqual(auto.run_cycle(self.store, "TEST_ACCOUNT", Mock(return_value=0),
                                       evaluate, self.sell, object(), self.enabled), 1)
        self.sell.assert_not_called()
        self.assertEqual(self.store.pending("TEST_ACCOUNT"), [(10, 4)])
        self.assertEqual(auto.run_cycle(self.store, "TEST_ACCOUNT", Mock(return_value=0),
                                       Mock(return_value=0), self.sell, object(), self.enabled), 0)
        self.sell.assert_called_once()

    def test_actual_evaluation_handoff_at_threshold(self):
        path = ROOT / "connector_intraday_position_evaluate.py"
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        names = {"_decimal_or_none", "snapshot_age_minutes", "evaluate_position", "run"}
        functions = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
        for price, posts in ((91, 0), (90, 1), (89, 1)):
            with self.subTest(price=price):
                self.store = MemoryStore()
                self.sell.reset_mock()
                _, position, snapshot, _ = records()
                snapshot["current_price"] = price
                ns = {"argparse": argparse, "Any": Any, "Dict": dict, "List": list, "Optional": Optional, "Decimal": Decimal,
                          "datetime": datetime, "timezone": timezone, "SOURCE_VERSION": "SYNTHETIC",
                          "DEFAULT_ENVIRONMENT": "paper", "_now_utc": lambda: datetime.now(timezone.utc),
                          "existing_intraday_stop_order": Mock(return_value=None),
                          "fetch_latest_balance": Mock(return_value=None),
                          "count_non_open_active": Mock(return_value=[]),
                          "fetch_open_positions": Mock(return_value=[position]),
                          "fetch_latest_positions": Mock(return_value={"TEST_STOCK": snapshot}),
                          "insert_intraday_check": Mock(return_value=4),
                          "create_intraday_stop_order": Mock(return_value=10),
                          "attach_created_execution_order": Mock(), "notify_intraday_stop_slack": Mock(),
                          "_json_payload": lambda value: json.dumps(value, default=str), "print": Mock()}
                exec(compile(ast.Module(body=functions, type_ignores=[]), str(path), "exec"), ns)  # noqa: S102 - Isolated test code with synthetic dependencies only.
                args = SimpleNamespace(account_no="TEST_ACCOUNT", create_order=True,
                                       notify_slack=False, hard_stop_rate="-0.10",
                                       slack_function_name="MOCK", slack_region="MOCK",
                                       max_snapshot_age_minutes=2, dry_run=False)
                self.assertEqual(auto.run_cycle(self.store, "TEST_ACCOUNT", Mock(return_value=0),
                                               ns["run"], self.sell, args, self.enabled), 0)
                self.assertEqual(self.sell.call_count, posts)
                self.assertEqual(ns["create_intraday_stop_order"].call_count, posts)
                ns["notify_intraday_stop_slack"].assert_not_called()

    def test_successful_transition_order_and_no_retry_policy(self):
        self.assertEqual(self.submit(), 0)
        self.assertEqual(self.store.transitions, ["REQUESTED", "SUBMITTING", "SUBMITTED"])
        self.sell.assert_called_once()
        self.assertEqual(self.sell.call_args.kwargs["qty"], 2)
        self.assertIs(self.sell.call_args.kwargs["retry_token_errors"], False)

    def test_same_order_reexecution_submits_once(self):
        self.submit()
        self.submit()
        self.sell.assert_called_once()

    def test_duplicate_ids_submit_once(self):
        self.assertEqual(self.submit([(10, 4), (10, 4)]), 0)
        self.sell.assert_called_once()

    def test_no_ids_never_fetches_bulk_ready_or_submits(self):
        self.assertEqual(self.submit([]), 0)
        self.assertEqual(self.store.transitions, [])
        self.sell.assert_not_called()

    def test_wrong_order_scope_is_blocked(self):
        self.assertEqual(self.submit([(99, 4)]), 1)
        self.sell.assert_not_called()

    def test_daily_active_sell_blocks(self):
        self.store.conflict = True
        self.assertEqual(self.submit(), 1)
        self.sell.assert_not_called()

    def test_losing_atomic_claim_does_not_submit(self):
        self.store.lost_claim = True
        self.assertEqual(self.submit(), 0)
        self.sell.assert_not_called()

    def test_kill_switch_off(self):
        self.enabled.return_value = False
        self.assertEqual(self.submit(), 0)
        self.sell.assert_not_called()

    def test_kill_switch_changed_before_post_leaves_claim_blocked(self):
        self.enabled.side_effect = [True, True, False]
        self.assertEqual(self.submit(), 1)
        self.assertEqual(self.store.order["execution_status"], "SUBMITTING")
        self.sell.assert_not_called()

    def test_response_loss_does_not_resubmit(self):
        self.sell.side_effect = TimeoutError("synthetic timeout")
        self.assertEqual(self.submit(), 1)
        self.assertEqual(self.store.order["execution_status"], "SUBMITTING")
        self.submit()
        self.sell.assert_called_once()

    def test_rejected_or_malformed_result_does_not_resubmit(self):
        for result in (None, {}, {"response": {"rt_cd": "1"}},
                       {"order_request_id": 7, "response": {"rt_cd": "0"}}):
            with self.subTest(result=result):
                self.store = MemoryStore()
                self.sell.reset_mock()
                self.sell.return_value = result
                self.assertEqual(self.submit(), 1)
                self.submit()
                self.sell.assert_called_once()

    def test_acceptance_then_state_sync_failure_is_not_retried(self):
        self.store.sync_error = True
        self.assertEqual(self.submit(), 1)
        self.submit()
        self.sell.assert_called_once()
        self.assertEqual(self.store.order["execution_status"], "SUBMITTING")

    def test_partial_fill_state_is_never_resubmitted(self):
        for status in ("SUBMITTING", "SUBMITTED", "PARTIAL_FILLED", "FILLED"):
            with self.subTest(status=status):
                self.store.order["execution_status"] = status
                self.assertEqual(self.submit(), 0)
        self.sell.assert_not_called()

    def test_evaluation_handoff_only_uses_created_ids(self):
        refresh = Mock(return_value=0)
        def evaluate(args, *, on_created):
            on_created(10, 4)
            return 0
        self.assertEqual(auto.run_cycle(self.store, "TEST_ACCOUNT", refresh, evaluate,
                                       self.sell, object(), self.enabled), 0)
        refresh.assert_called_once_with()
        self.sell.assert_called_once()

    def test_duplicate_cycle_does_not_evaluate(self):
        self.store.locked = True
        refresh, evaluate = Mock(), Mock()
        self.assertEqual(auto.run_cycle(self.store, "TEST_ACCOUNT", refresh, evaluate,
                                       self.sell, object(), self.enabled), 0)
        refresh.assert_not_called()
        evaluate.assert_not_called()
        self.sell.assert_not_called()

    def test_snapshot_failure_blocks_evaluation(self):
        evaluate = Mock()
        self.assertEqual(auto.run_cycle(self.store, "TEST_ACCOUNT", Mock(return_value=20),
                                       evaluate, self.sell, object(), self.enabled), 1)
        evaluate.assert_not_called()
        self.sell.assert_not_called()

    def test_evaluation_failure_after_callback_never_submits(self):
        def evaluate(args, *, on_created):
            on_created(10, 4)
            return 1
        self.assertEqual(auto.run_cycle(self.store, "TEST_ACCOUNT", Mock(return_value=0),
                                       evaluate, self.sell, object(), self.enabled), 1)
        self.sell.assert_not_called()


class ValidationTests(OfflineCase):
    def test_quantity_properties_with_synthetic_positions(self):
        rng = random.Random(407)
        for _ in range(100):
            remaining, held, sellable = (rng.randint(1, 100) for _ in range(3))
            allowed = min(remaining, held, sellable)
            for qty in (1, allowed, allowed + 1, 0, -1, Decimal(allowed) + Decimal("0.5")):
                data = list(records())
                data[0]["order_qty"] = qty
                data[1]["remaining_qty"] = remaining
                data[2]["quantity"] = held
                data[2]["sellable_quantity"] = sellable
                with self.subTest(remaining=remaining, held=held, sellable=sellable, qty=qty):
                    if qty in (1, allowed):
                        auto.validate(*data, "TEST_ACCOUNT", datetime.now(timezone.utc))
                    else:
                        with self.assertRaises(auto.SafetyBlock):
                            auto.validate(*data, "TEST_ACCOUNT", datetime.now(timezone.utc))

    def test_exact_minus_ten_percent_is_allowed(self):
        auto.validate(*records(), "TEST_ACCOUNT", datetime.now(timezone.utc))

    def test_invalid_scope_quantity_and_market_data(self):
        changes = [
            (0, "execution_mode", "PAPER_STRATEGY"), (0, "action_type", "BUY"),
            (0, "signal_type", "OTHER"), (0, "source_type", "OTHER"),
            (0, "account_no", "OTHER"), (0, "ticker_code", "OTHER"),
            (0, "source_position_state_id", 9), (0, "order_method", "LIMIT"),
            (0, "order_qty", 3), (0, "order_qty", 0), (0, "order_qty", True),
            (0, "order_qty", Decimal("1.5")), (1, "remaining_qty", 0),
            (1, "position_status", "SELL_ORDERED"), (2, "sellable_quantity", 0),
            (2, "account_no", "OTHER"), (2, "current_price", 91),
            (2, "as_of_ts", datetime.now(timezone.utc).replace(tzinfo=None)),
            (2, "as_of_ts", datetime.now(timezone.utc) - timedelta(minutes=3)),
            (2, "as_of_ts", datetime.now(timezone.utc) + timedelta(minutes=1)),
            (3, "should_stop", False), (3, "created_execution_order_id", 99),
            (3, "environment", "live"), (3, "pnl_rate", Decimal("-0.09")),
        ]
        for index, field, value in changes:
            with self.subTest(index=index, field=field, value=value):
                data = list(records())
                data[index][field] = value
                with self.assertRaises((auto.SafetyBlock, ValueError)):
                    auto.validate(*data, "TEST_ACCOUNT", datetime.now(timezone.utc))


class StoreTests(OfflineCase):
    def test_old_requested_is_reowned_only_before_submission_and_revalidated(self):
        order, position, snapshot, check = records()
        order["execution_status"] = "REQUESTED"
        order["result_payload"]["intraday_auto"].update(runId="OLD_RUN", stage="REQUESTED",
                                                       originRunId="ORIGINAL_RUN")
        check["check_ts"] -= timedelta(minutes=30)
        store, _conn, cur = self.setup_db([order, position, snapshot, check,
                                        {"conflict": False}, {**order, "execution_status": "REQUESTED"}])
        self.assertIsNotNone(store.advance(10, 4, "TEST_ACCOUNT", "NEW_RUN", "REQUESTED", "REQUESTED"))
        metadata = json.loads(cur.execute.call_args.args[1][1])["intraday_auto"]
        self.assertEqual(metadata["originRunId"], "ORIGINAL_RUN")
        self.assertEqual(metadata["runId"], "NEW_RUN")
        self.assertIn("not exists", cur.execute.call_args.args[0])
        self.assertIn("strategy_run_id", cur.execute.call_args.args[0])

    def test_resumed_old_check_still_requires_current_threshold_quantity_and_freshness(self):
        for field, value in (("current_price", 91), ("sellable_quantity", 1),
                             ("as_of_ts", datetime.now(timezone.utc) - timedelta(minutes=5))):
            with self.subTest(field=field):
                order, position, snapshot, check = records()
                check["check_ts"] -= timedelta(minutes=30)
                snapshot[field] = value
                store, conn, _cur = self.setup_db([order, position, snapshot, check])
                with self.assertRaises(auto.SafetyBlock):
                    store.advance(10, 4, "TEST_ACCOUNT", "NEW_RUN", "READY", "REQUESTED")
                conn.commit.assert_not_called()

    def test_resumed_order_blocks_changed_position_entry_price(self):
        order, position, snapshot, check = records()
        position["entry_price"] = 110
        check["check_ts"] -= timedelta(minutes=30)
        store, _conn, _cur = self.setup_db([order, position, snapshot, check])
        with self.assertRaises(auto.SafetyBlock):
            store.advance(10, 4, "TEST_ACCOUNT", "NEW_RUN", "READY", "REQUESTED")

    def test_untagged_or_uncertain_rows_cannot_be_reowned(self):
        for metadata in ({}, {"runId": "OLD", "checkId": 4, "stage": "SUBMITTING"},
                         {"runId": "OLD", "checkId": 99, "stage": "READY"}):
            with self.subTest(metadata=metadata):
                order, position, snapshot, check = records()
                order["result_payload"] = {"intraday_auto": metadata}
                store, conn, _cur = self.setup_db([order, position, snapshot, check])
                with self.assertRaises(auto.SafetyBlock):
                    store.advance(10, 4, "TEST_ACCOUNT", "NEW_RUN", "READY", "REQUESTED")
                conn.commit.assert_not_called()

    def test_pending_query_requires_provenance_link_and_no_attempt(self):
        store, _conn, cur = self.setup_db([])
        cur.fetchall.side_effect = None
        cur.fetchall.return_value = [{"id": 10, "check_id": 4}]
        self.assertEqual(store.pending("TEST_ACCOUNT"), [(10, 4)])
        sql, params = cur.execute.call_args.args
        self.assertEqual(params, ("TEST_ACCOUNT",))
        for clause in ("in ('READY', 'REQUESTED')", "connector_order_request_id is null",
                       "c.created_execution_order_id = o.id", "'checkId'", "'stage'",
                       "not exists", "r.strategy_run_id", "PAPER_INTRADAY"):
            self.assertIn(clause, sql)

    def test_created_registration_requires_linked_evaluation_and_never_overwrites_owner(self):
        store, conn, cur = self.setup_db([{"id": 10}])
        store.register_created(10, 4, "TEST_ACCOUNT", "NEW_RUN")
        sql, params = cur.execute.call_args.args
        self.assertIn("c.created_execution_order_id = o.id", sql)
        self.assertIn("not (coalesce(o.result_payload", sql)
        self.assertEqual(json.loads(params[0])["intraday_auto"]["stage"], "READY")
        conn.commit.assert_called_once_with()

    def setup_db(self, rows):
        cursor = MagicMock()
        cursor.__enter__.return_value = cursor
        conflicts = [row for row in rows if isinstance(row, dict) and set(row) == {"conflict"}]
        cursor.fetchone.side_effect = [row for row in rows if row not in conflicts]
        conflict = conflicts[0]["conflict"] if conflicts else False
        cursor.fetchall.side_effect = [[{"execution_status": "REQUESTED"}] if conflict else [], []]
        conn = MagicMock()
        conn.__enter__.return_value = conn
        conn.cursor.return_value = cursor
        connect = Mock(return_value=conn)
        return auto.Store(connect, object()), conn, cursor

    def test_real_store_scopes_update_and_commits(self):
        order, position, snapshot, check = records()
        store, conn, cur = self.setup_db(
            [order, position, snapshot, check, {"conflict": False}, {**order, "execution_status": "REQUESTED"}])
        result = store.advance(10, 4, "TEST_ACCOUNT", "TEST_RUN", "READY", "REQUESTED")
        self.assertEqual(result["execution_status"], "REQUESTED")
        queries = [call.args[0] for call in cur.execute.call_args_list]
        self.assertIn("for update", queries[0].lower())
        self.assertIn("parent_order_request_id", queries[-2])
        self.assertIn("execution_mode = 'PAPER_INTRADAY'", queries[-1])
        self.assertIn("execution_status = %s", queries[-1])
        self.assertEqual(cur.execute.call_args.args[1][-3:], (10, "READY", "TEST_ACCOUNT"))
        conn.commit.assert_called_once_with()

    def test_real_store_active_daily_blocks_update(self):
        data = list(records())
        store, conn, cur = self.setup_db(data + [{"conflict": True}])
        with self.assertRaises(auto.SafetyBlock):
            store.advance(10, 4, "TEST_ACCOUNT", "TEST_RUN", "READY", "REQUESTED")
        conn.commit.assert_not_called()
        self.assertFalse(any("update execution" in call.args[0] for call in cur.execute.call_args_list))

    def test_real_store_stale_status_and_wrong_owner_do_not_update(self):
        order, position, snapshot, check = records()
        order["execution_status"] = "SUBMITTING"
        store, _conn, _cur = self.setup_db([order])
        self.assertIsNone(store.advance(10, 4, "TEST_ACCOUNT", "TEST_RUN", "READY", "REQUESTED"))
        order["execution_status"] = "REQUESTED"
        store, _conn, _cur = self.setup_db([order, position, snapshot, check])
        with self.assertRaises(auto.SafetyBlock):
            store.advance(10, 4, "TEST_ACCOUNT", "TEST_RUN", "REQUESTED", "SUBMITTING")

    def test_zero_submitted_update_never_marks_position(self):
        store, conn, cur = self.setup_db([{"id": 7}, None])
        with self.assertRaises(auto.SafetyBlock):
            store.submitted(records()[0], 7, "TEST_RUN")
        self.assertEqual(cur.execute.call_count, 2)
        conn.commit.assert_not_called()

    def test_submitted_validates_connector_scope_and_updates_position_atomically(self):
        store, conn, cur = self.setup_db([{"id": 7}, {"id": 10}, {"id": 3}])
        store.submitted(records()[0], 7, "TEST_RUN")
        self.assertIn("strategy_run_id = %s", cur.execute.call_args_list[0].args[0])
        self.assertIn("position_status = 'SELL_ORDERED'", cur.execute.call_args.args[0])
        conn.commit.assert_called_once_with()

    def test_position_sync_failure_rolls_back_submitted_transition(self):
        store, conn, _cur = self.setup_db([{"id": 7}, {"id": 10}, None])
        with self.assertRaises(auto.SafetyBlock):
            store.submitted(records()[0], 7, "TEST_RUN")
        conn.commit.assert_not_called()
        self.assertIs(conn.__exit__.call_args.args[0], auto.SafetyBlock)

    def test_db_claim_exception_does_not_reach_broker(self):
        store, _conn, cur = self.setup_db([])
        cur.execute.side_effect = RuntimeError("synthetic DB failure")
        self.assertEqual(auto.submit_created(store, [(10, 4)], "TEST_ACCOUNT", "TEST_RUN",
                                            self.sell, self.enabled), 1)
        self.sell.assert_not_called()

    def test_requested_to_submitting_requires_same_run_and_scope(self):
        order, position, snapshot, check = records()
        order["execution_status"] = "REQUESTED"
        order["result_payload"] = {"intraday_auto": {
            "runId": "TEST_RUN", "checkId": 4, "stage": "REQUESTED"}}
        store, conn, _cur = self.setup_db([order, position, snapshot, check,
                                        {"conflict": False}, {**order, "execution_status": "SUBMITTING"}])
        result = store.advance(10, 4, "TEST_ACCOUNT", "TEST_RUN", "REQUESTED", "SUBMITTING")
        self.assertEqual(result["execution_status"], "SUBMITTING")
        conn.commit.assert_called_once_with()

    def test_lock_released_even_after_evaluation_error(self):
        store, _conn, cur = self.setup_db([(True,)])
        with self.assertRaises(RuntimeError), store.cycle("TEST_ACCOUNT"):
            raise RuntimeError("synthetic evaluation error")
        self.assertIn("pg_advisory_unlock", cur.execute.call_args.args[0])

    def test_intraday_lock_failure_is_normal_skip(self):
        store, _conn, cur = self.setup_db([(False,)])
        with store.cycle("TEST_ACCOUNT") as acquired:
            self.assertFalse(acquired)
        self.assertEqual(cur.execute.call_count, 1)


class FailedEvidenceTests(OfflineCase):
    def rejected(self, status="FAILED"):
        return {"id": 7, "account_id": 2, "ticker_code": "TEST_STOCK", "request_type": "SELL",
                    "request_status": status, "broker_order_no": None, "accepted_at": None,
                    "rejection_code": "SYNTHETIC_REJECTION",
                    "response_payload": {"rt_cd": "1", "msg_cd": "SYNTHETIC_REJECTION", "output": {}}}

    def test_explicit_rejection_without_fills_or_children_is_required(self):
        cur = MagicMock()
        cur.fetchone.return_value = {"evidence_conflict": False}
        self.assertTrue(auto.Store._confirmed_unaccepted(cur, self.rejected()))
        sql, params = cur.execute.call_args.args
        self.assertIn("connector.connector_fill", sql)
        self.assertIn("parent_order_request_id", sql)
        self.assertEqual(params, (7, 7))

    def test_failed_or_missing_broker_number_alone_is_not_evidence(self):
        changes = [("response_payload", None), ("response_payload", {}),
                   ("response_payload", {"rt_cd": "-1", "msg_cd": "JSON_PARSE_ERROR"}),
                   ("response_payload", {"error": "HTTP request failed"}),
                   ("rejection_code", None), ("broker_order_no", "MOCK_ACCEPTED"),
                   ("accepted_at", datetime.now(timezone.utc)), ("request_status", "PENDING"),
                   ("request_status", "PARTIAL_FILLED"), ("request_type", "CANCEL"),
                   ("response_payload", {"rt_cd": "1", "msg_cd": "WRONG"}),
                   ("response_payload", {"rt_cd": "1", "msg_cd": "SYNTHETIC_REJECTION",
                                         "output": {"ODNO": "MOCK_ACCEPTED"}})]
        for key, value in changes:
            with self.subTest(key=key, value=value):
                request = self.rejected()
                request[key] = value
                cur = MagicMock()
                self.assertFalse(auto.Store._confirmed_unaccepted(cur, request))
                cur.execute.assert_not_called()

    def test_any_fill_or_child_blocks_even_an_explicit_rejection(self):
        cur = MagicMock()
        cur.fetchone.return_value = {"evidence_conflict": True}
        self.assertFalse(auto.Store._confirmed_unaccepted(cur, self.rejected()))

    def conflict_cursor(self, strategies, requests, responses=()):
        cur = MagicMock()
        cur.fetchall.side_effect = [strategies, requests]
        cur.fetchone.side_effect = responses
        return cur

    def test_proven_failed_strategy_and_connector_are_excluded(self):
        other = {"execution_status": "FAILED", "connector_order_request_id": 7, "result_payload": {}}
        rejected = self.rejected()
        cur = self.conflict_cursor([other], [rejected],
                                  [rejected, {"evidence_conflict": False}, {"evidence_conflict": False}])
        self.assertFalse(auto.Store(Mock(), object())._has_conflicts(cur, records()[0]))

    def test_active_uncertain_and_unlinked_strategy_orders_remain_conflicts(self):
        for status in ("READY", "REQUESTED", "SUBMITTING", "SUBMITTED", "PARTIAL_FILLED", "FAILED", None):
            with self.subTest(status=status):
                other = {"execution_status": status, "connector_order_request_id": None, "result_payload": {}}
                cur = self.conflict_cursor([other], [])
                self.assertTrue(auto.Store(Mock(), object())._has_conflicts(cur, records()[0]))

    def test_unknown_marker_blocks_failed_strategy_despite_linked_rejection(self):
        other = {"execution_status": "FAILED", "connector_order_request_id": 7,
                     "result_payload": {"intraday_auto": {"stage": "SUBMITTING"}}}
        cur = self.conflict_cursor([other], [self.rejected()])
        self.assertTrue(auto.Store(Mock(), object())._has_conflicts(cur, records()[0]))
        cur.fetchone.assert_not_called()

    def test_unknown_failed_connector_and_partial_fills_remain_conflicts(self):
        for status in ("FAILED", "PENDING", "PARTIAL_FILLED", "CANCEL_ACCEPTED", None):
            with self.subTest(status=status):
                request = self.rejected(status)
                request["response_payload"] = {"error": "timeout"}
                cur = self.conflict_cursor([], [request])
                self.assertTrue(auto.Store(Mock(), object())._has_conflicts(cur, records()[0]))


class RetryPolicyTests(OfflineCase):
    def test_common_transport_no_token_retry_for_intraday_and_default_preserved(self):
        source = ROOT / "connector_order_common.py"
        tree = ast.parse(source.read_text(encoding="utf-8-sig"))
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_request_api")
        # Execute the actual transport function with only synthetic dependencies.
        for retry, expected_posts in ((False, 1), (True, 2)):
            with self.subTest(retry=retry):
                response = Mock(text="synthetic token error", status_code=200)
                response.json.return_value = {"rt_cd": "1"}
                requests = Mock()
                requests.post.return_value = response
                refresh = Mock(return_value="SYNTHETIC_REFRESH")
                ns = {"Any": object, "Dict": dict, "get_access_token": Mock(return_value="SYNTHETIC"),
                          "BASE_URL": "https://example.invalid", "APP_KEY": "SYNTHETIC",
                          "APP_SECRET": "SYNTHETIC", "requests": requests,
                          "time": Mock(time=Mock(return_value=0)), "insert_api_call_log": Mock(),
                          "check_and_refresh_token": refresh, "t": lambda english, korean: "MOCK"}
                exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), "exec"), ns)  # noqa: S102 - Isolated test code with synthetic dependencies only.
                kwargs = {"account_id": 2, "api_category": "ORDER", "api_name": "MOCK",
                              "http_method": "POST", "endpoint": "/mock", "tr_id": "MOCK", "request_payload": {}}
                if not retry:
                    kwargs["retry_token_errors"] = False
                ns["_request_api"](**kwargs)
                self.assertEqual(requests.post.call_count, expected_posts)
                if not retry:
                    refresh.assert_not_called()

    def test_sell_wrapper_forwards_only_explicit_no_retry(self):
        path = ROOT / "connector_sell.py"
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "sell_stock")
        for retry in (False, True):
            submit = Mock()
            ns = {"Optional": Optional, "submit_cash_order": submit, "API_NAME": "MOCK", "TR_ID": "MOCK"}
            exec(compile(ast.Module(body=[fn], type_ignores=[]), str(path), "exec"), ns)  # noqa: S102 - Isolated test code with synthetic dependencies only.
            kwargs = {} if retry else {"retry_token_errors": False}
            ns["sell_stock"]("TEST_STOCK", 2, **kwargs)
            if retry:
                self.assertNotIn("retry_token_errors", submit.call_args.kwargs)
            else:
                self.assertIs(submit.call_args.kwargs["retry_token_errors"], False)


if __name__ == "__main__":
    unittest.main()
