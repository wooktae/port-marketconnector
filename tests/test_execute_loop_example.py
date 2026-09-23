"""Example test: Claim-based run() loop batch behavior (Task 8.2).

This example directly runs the `--execute` loop and the Dry_Run path of
`connector_strategy_order_execute.run()` under mock isolation to verify the following.

- Full traversal of a mixed batch (Requirement 2.6):
  Even when some orders fail quantity validation and some Claims return 0 rows (skip),
  the loop traverses all target orders, records the unit failure/skip, and then continues.
- Dry_Run makes no changes (Requirement 2.12):
  Without `--execute`, it performs no Claim and no `execution_status` DB changes.
  It verifies that claim/mark/broker submission `call_count == 0`.
- Claim DB error (Requirement 2.13):
  If the Claim conditional UPDATE fails with a DB error, that order does not call the
  broker and is recorded as a unit failure (mark_failed), and the batch continues with
  the remaining orders.

All checks are performed with the broker/token/DB functions isolated by mocks, and do
not call the real KIS API, token, or operating DB (Requirement 6). The module-level
functions the run() loop calls (fetch/claim/mark/broker submission) are replaced with
mocks, and the real `get_conn` and the real broker submission entry point are guarded so
that they fail immediately if called.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path
from typing import Any


def _install_import_only_environment() -> None:
    """Fill only with dummy values the environment variables that config.py requires on import.

    Does not read or record real KIS key/account values, and does not trigger
    broker/DB/token side effects. Keys that are already set are not overwritten.
    """
    config_path = Path(__file__).resolve().parents[1] / "config.py"
    source = config_path.read_text(encoding="utf-8-sig")
    tree = ast.parse(source)

    keys: set[str] = set()

    for node in ast.walk(tree):
        # os.environ["KEY"]
        if isinstance(node, ast.Subscript):
            value = node.value
            if (
                isinstance(value, ast.Attribute)
                and isinstance(value.value, ast.Name)
                and value.value.id == "os"
                and value.attr == "environ"
                and isinstance(node.slice, ast.Constant)
                and isinstance(node.slice.value, str)
            ):
                keys.add(node.slice.value)

        if isinstance(node, ast.Call):
            func = node.func

            # os.getenv("KEY")
            if (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "os"
                and func.attr == "getenv"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                keys.add(node.args[0].value)

            # os.environ.get("KEY")
            if (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Attribute)
                and isinstance(func.value.value, ast.Name)
                and func.value.value.id == "os"
                and func.value.attr == "environ"
                and func.attr == "get"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                keys.add(node.args[0].value)

    for key in keys:
        if key in os.environ:
            continue

        if "URL" in key:
            value = "https://example.invalid"
        elif any(token in key for token in ("ACNT", "ACCOUNT", "CANO")):
            value = "00000000"
        elif any(token in key for token in ("PRDT", "PRODUCT")):
            value = "01"
        else:
            value = "TEST_ONLY_DUMMY"

        os.environ[key] = value


_install_import_only_environment()

import connector_strategy_order_execute as execute


# ---------------------------------------------------------------------------
# Test doubles / spies
# ---------------------------------------------------------------------------
class _GetConnGuard:
    """A guard that immediately fails on real DB access.

    Since all DB functions on the run() path are replaced with mocks, if the real
    get_conn is called it means the mock was not applied, so the check fails immediately
    (Requirement 6).
    """

    def __call__(self, *args: Any, **kwargs: Any):
        raise AssertionError("real get_conn must not be called in run() loop tests")


def _guard_real_broker_submit(*args: Any, **kwargs: Any):
    """A guard for the real broker submission entry point.

    Since the run() loop tests mock broker submission at the rate-limit-retry wrapper
    level, if the real _submit_order is called it means a risk of broker/token side
    effects, so the check fails immediately (Requirement 6).
    """
    raise AssertionError("real _submit_order must not be called in run() loop tests")


def _make_order(
    execution_order_id: int,
    order_qty: Any,
    *,
    action: str = "BUY",
) -> dict[str, Any]:
    return {
        "id": execution_order_id,
        "execution_plan_id": 1,
        "execution_mode": execute.EXECUTION_MODE,
        "source_type": "DAILY",
        "action_type": action,
        "signal_type": action,
        "execution_status": execute.REQUESTED_STATUS,
        "ticker_code": "000660",
        "stock_name": "TEST",
        "order_qty": order_qty,
        "order_method": "MARKET",
        "order_price": None,
        "connector_order_request_id": None,
        "source_position_state_id": None,
    }


def _successful_broker_result(order_request_id: int) -> dict[str, Any]:
    return {
        "order_request_id": order_request_id,
        "response": {"rt_cd": "0"},
        "broker_order_no": "0000000010",
    }


class _RunHarness:
    """Isolate with mocks the module-level functions the run() loop calls."""

    def __init__(self, monkeypatch, orders: list[dict[str, Any]]) -> None:
        self.orders = orders
        self.claim_calls: list[int] = []
        self.submit_calls: list[int] = []
        self.submitted_calls: list[int] = []
        self.failed_calls: list[int] = []
        self.sell_ordered_calls: list[int] = []
        # execution_order_id -> "row" | "skip" | "db_error"
        self.claim_behavior: dict[int, str] = {}
        # execution_order_id -> "row" | "none" | "db_error"
        self.mark_submitted_behavior: dict[int, str] = {}
        # execution_order_id -> "row" | "db_error"
        self.mark_failed_behavior: dict[int, str] = {}
        self._request_id_seq = 9000

        # Guard the real side-effect entry points.
        monkeypatch.setattr(execute, "get_conn", _GetConnGuard())
        monkeypatch.setattr(execute, "_submit_order", _guard_real_broker_submit)

        # The --execute environment requirement and retry recovery access the DB, so isolate them.
        monkeypatch.setattr(execute, "_require_execute_environment", lambda: None)
        monkeypatch.setattr(
            execute,
            "normalize_retryable_rejected_orders",
            lambda **kwargs: [],
        )
        # Fatal_Max is outside the scope of this test, so fix it as disabled (None).
        monkeypatch.setattr(execute, "resolve_fatal_max_order_qty", lambda: None)

        monkeypatch.setattr(
            execute,
            "fetch_requested_strategy_orders",
            lambda **kwargs: list(self.orders),
        )

        monkeypatch.setattr(
            execute,
            "claim_strategy_execution_order",
            self._fake_claim,
        )
        monkeypatch.setattr(
            execute,
            "_submit_order_with_rate_limit_retry",
            self._fake_submit,
        )
        monkeypatch.setattr(
            execute,
            "mark_strategy_execution_order_submitted",
            self._fake_mark_submitted,
        )
        monkeypatch.setattr(
            execute,
            "mark_strategy_execution_order_failed",
            self._fake_mark_failed,
        )
        monkeypatch.setattr(
            execute,
            "mark_strategy_position_sell_ordered",
            self._fake_mark_sell_ordered,
        )

    def _fake_claim(self, execution_order_id: int):
        self.claim_calls.append(execution_order_id)
        behavior = self.claim_behavior.get(execution_order_id, "row")
        if behavior == "db_error":
            raise RuntimeError("simulated claim DB error")
        if behavior == "skip":
            return None
        row = dict(self._order_by_id(execution_order_id))
        row["execution_status"] = execute.SUBMITTING_STATUS
        return row

    def _fake_submit(self, order: dict[str, Any], **kwargs: Any):
        self.submit_calls.append(order["id"])
        self._request_id_seq += 1
        result = _successful_broker_result(self._request_id_seq)
        attempts = [{"attempt_no": 1, "successful": True}]
        return result, attempts

    def _fake_mark_submitted(self, *, execution_order_id, **kwargs):
        self.submitted_calls.append(execution_order_id)
        behavior = self.mark_submitted_behavior.get(execution_order_id, "row")
        if behavior == "db_error":
            raise RuntimeError("simulated SUBMITTED state sync DB error")
        if behavior == "none":
            return None
        return {"id": execution_order_id, "execution_status": "SUBMITTED"}

    def _fake_mark_failed(self, *, execution_order_id, **kwargs):
        self.failed_calls.append(execution_order_id)
        behavior = self.mark_failed_behavior.get(execution_order_id, "row")
        if behavior == "db_error":
            raise RuntimeError("simulated FAILED status write DB error")
        return {"id": execution_order_id, "execution_status": "FAILED"}

    def _fake_mark_sell_ordered(self, position_state_id: int):
        self.sell_ordered_calls.append(position_state_id)
        return {"id": position_state_id, "position_status": "SELL_ORDERED"}

    def _order_by_id(self, execution_order_id: int) -> dict[str, Any]:
        for order in self.orders:
            if order["id"] == execution_order_id:
                return order
        raise KeyError(execution_order_id)


def _execute_args():
    return execute.build_parser().parse_args(
        ["--execute", "--order-sleep-seconds", "0"]
    )


def _dry_run_args():
    return execute.build_parser().parse_args([])


# ---------------------------------------------------------------------------
# Req 2.6: full traversal of a mixed batch
# ---------------------------------------------------------------------------
def test_mixed_batch_continues_through_failure_and_skip(monkeypatch) -> None:
    # id=1 normal submission, id=2 quantity validation failure, id=3 Claim 0-row skip, id=4 normal submission.
    orders = [
        _make_order(1, 3),
        _make_order(2, 2.5),  # fractional part -> QuantityValidationError (broker not called)
        _make_order(3, 5),
        _make_order(4, 7),
    ]
    harness = _RunHarness(monkeypatch, orders)
    harness.claim_behavior[3] = "skip"

    rc = execute.run(_execute_args())

    assert rc == 0

    # The batch traverses all 4 orders. Since id=2 stops before Claim due to a validation
    # failure, Claim is attempted only for 1, 3, and 4, which passed validation.
    assert harness.claim_calls == [1, 3, 4]

    # Broker submission occurs exactly for 1 and 4, for which Claim returned a row.
    # The validation failure (2) and the skip (3) do not call the broker.
    assert harness.submit_calls == [1, 4]

    # A unit failure is recorded only for the quantity validation failure order (2).
    assert harness.failed_calls == [2]

    # Only the normally submitted orders (1, 4) are recorded as SUBMITTED. The skip (3) has no state change.
    assert harness.submitted_calls == [1, 4]


# ---------------------------------------------------------------------------
# Req 2.12: Dry_Run performs no Claim, no execution_status DB change, and no
#           broker call.
# ---------------------------------------------------------------------------
def test_dry_run_performs_no_claim_mark_or_broker_call(monkeypatch) -> None:
    orders = [
        _make_order(11, 3),
        _make_order(12, 9),
    ]
    harness = _RunHarness(monkeypatch, orders)

    rc = execute.run(_dry_run_args())

    assert rc == 0

    # Dry_Run performs only pure validation and does not do Claim/mark/broker submission.
    assert len(harness.claim_calls) == 0
    assert len(harness.submit_calls) == 0
    assert len(harness.submitted_calls) == 0
    assert len(harness.failed_calls) == 0
    assert len(harness.sell_ordered_calls) == 0


# ---------------------------------------------------------------------------
# Req 2.13: on Claim DB error, broker not called, unit failure, batch continues
# ---------------------------------------------------------------------------
def test_claim_db_error_skips_broker_and_continues_batch(monkeypatch) -> None:
    # id=10 Claim DB error, id=11 normal submission.
    orders = [
        _make_order(10, 3),
        _make_order(11, 4),
    ]
    harness = _RunHarness(monkeypatch, orders)
    harness.claim_behavior[10] = "db_error"

    rc = execute.run(_execute_args())

    assert rc == 0

    # Claim is attempted for both orders (the batch is not aborted by the error).
    assert harness.claim_calls == [10, 11]

    # The Claim DB error order (10) does not call the broker.
    # Only the normal order (11) calls the broker exactly once.
    assert harness.submit_calls == [11]

    # The Claim DB error order (10) is recorded as a unit failure.
    assert harness.failed_calls == [10]

    # The batch continues and the normal order (11) is recorded as SUBMITTED.
    assert harness.submitted_calls == [11]


# ---------------------------------------------------------------------------
# The case where SUBMITTED state synchronization fails with no updated row (None) after broker success.
#
# Since the broker has already returned a success response, do not overwrite it with a
# resubmittable FAILED. Record it distinctly as SUBMITTED_STATE_SYNC_FAILED and continue
# to the next order without performing SELL follow-up processing or the [SUBMITTED]
# success log.
# ---------------------------------------------------------------------------
def test_broker_success_then_mark_submitted_returns_none(monkeypatch, capsys) -> None:
    # id=21 SELL: broker success, mark_submitted None. id=22 normal submission to confirm batch continuation.
    sell_order = _make_order(21, 3, action="SELL")
    sell_order["source_position_state_id"] = 555  # verify not called via the None-return guard
    orders = [
        sell_order,
        _make_order(22, 4, action="SELL"),
    ]
    harness = _RunHarness(monkeypatch, orders)
    harness.mark_submitted_behavior[21] = "none"

    rc = execute.run(_execute_args())
    out = capsys.readouterr().out

    assert rc == 0

    # The broker mock is called exactly once (id=21). id=22 is also submitted normally.
    assert harness.submit_calls == [21, 22]

    # If the SUBMITTED return is None, do not perform SELL_ORDERED follow-up processing.
    assert harness.sell_ordered_calls == []

    # Do not overwrite with a resubmittable FAILED.
    assert harness.failed_calls == []

    # A distinct SUBMITTED_STATE_SYNC_FAILED record remains.
    assert "SUBMITTED_STATE_SYNC_FAILED" in out
    assert "execution_order_id=21" in out

    # The [SUBMITTED] success log for id=21 is not printed, and only id=22 is processed as a success.
    assert "[SUBMITTED] execution_order_id=21" not in out
    assert harness.submitted_calls == [21, 22]
    assert 22 in harness.sell_ordered_calls or "[SUBMITTED] execution_order_id=22" in out


# ---------------------------------------------------------------------------
# The case where a DB exception occurs during SUBMITTED state reflection after broker success.
#
# Since the broker response may already be a success, do not call mark_failed; record it
# distinctly as SUBMITTED_STATE_SYNC_FAILED and continue to the next order.
# ---------------------------------------------------------------------------
def test_broker_success_then_mark_submitted_raises(monkeypatch, capsys) -> None:
    # id=31 SELL: broker success, mark_submitted exception. id=32 normal submission to confirm batch continuation.
    sell_order = _make_order(31, 3, action="SELL")
    sell_order["source_position_state_id"] = 777
    orders = [
        sell_order,
        _make_order(32, 5, action="SELL"),
    ]
    harness = _RunHarness(monkeypatch, orders)
    harness.mark_submitted_behavior[31] = "db_error"

    rc = execute.run(_execute_args())
    out = capsys.readouterr().out

    assert rc == 0

    # The broker mock is called exactly once for id=31, and id=32 is also submitted normally.
    assert harness.submit_calls == [31, 32]

    # A state reflection exception after broker success does not overwrite with FAILED.
    assert harness.failed_calls == []

    # SELL_ORDERED follow-up processing is also not performed.
    assert 31 not in harness.sell_ordered_calls

    # A distinct SUBMITTED_STATE_SYNC_FAILED record remains.
    assert "SUBMITTED_STATE_SYNC_FAILED" in out
    assert "execution_order_id=31" in out

    # The batch continues and id=32 is submitted normally.
    assert harness.submitted_calls == [31, 32]


# ---------------------------------------------------------------------------
# A double error where a Claim error and a failure-status write (mark_failed) error occur together.
#
# Even if the failure-status write also fails, the exception does not propagate outside
# run(); it is recorded distinctly as CLAIM_FAILED_STATUS_WRITE_FAILED and the batch continues.
# ---------------------------------------------------------------------------
def test_claim_error_and_mark_failed_error_do_not_abort_batch(
    monkeypatch, capsys
) -> None:
    # id=41 Claim exception + mark_failed exception, id=42 normal submission.
    orders = [
        _make_order(41, 3),
        _make_order(42, 4),
    ]
    harness = _RunHarness(monkeypatch, orders)
    harness.claim_behavior[41] = "db_error"
    harness.mark_failed_behavior[41] = "db_error"

    # run() must not terminate via an exception.
    rc = execute.run(_execute_args())
    out = capsys.readouterr().out

    assert rc == 0

    # The first order (41) does not call the broker due to the Claim failure.
    # Only the second order (42) calls the broker exactly once.
    assert harness.submit_calls == [42]

    # A distinct double-error record remains.
    assert "CLAIM_FAILED_STATUS_WRITE_FAILED" in out
    assert "execution_order_id=41" in out

    # The batch continues and the second order (42) is recorded as SUBMITTED.
    assert harness.submitted_calls == [42]
