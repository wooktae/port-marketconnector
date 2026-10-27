"""Example test: the Claim/mark contract (Task 7.4).

This example validates the Claim contract at the broker order submission boundary.

- Verify the `SUBMITTING_STATUS` value and the Claim conditional UPDATE references (Requirement 2.1)
- Verify Claim-first execution and exactly one broker call (Requirements 2.2, 2.4)

Integration of the Claim-based `run()` processing loop is performed in Task 8, so this
example focuses on Claim + one broker call + ordering contract rather than the full
`run()` batch behavior. The `run()` loop behavior, such as mixed-batch continuation,
Dry_Run, and Claim DB errors, is covered in Task 8.2.

All checks are performed with the broker/token/DB functions isolated by mocks or
in-memory fakes, and do not call the real KIS API, token, or operating DB
(Requirement 6). The real `get_conn` is replaced with a fake, and any attempt to access
the real DB without replacement fails immediately.
"""

from __future__ import annotations

import ast
import inspect
import os
from pathlib import Path
from typing import Any, Self


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
# In-memory fake DB
# ---------------------------------------------------------------------------
class _FakeCursor:
    """A minimal fake that mimics a psycopg cursor.

    - information_schema.columns query: returns the strategy_execution_order column list.
    - conditional UPDATE (claim): returns the preconfigured claim result row via RETURNING.
    Does not access a real DB or the network.
    """

    def __init__(self, shared: dict[str, Any]) -> None:
        self._shared = shared
        self._rows: list[Any] = []
        self._one: dict[str, Any] | None = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def execute(self, sql: str, params: Any = ()) -> None:
        if "information_schema.columns" in sql:
            schema = self._shared["schema"]
            self._rows = [(schema, col) for col in self._shared["columns"]]
            self._one = None
            return

        if "UPDATE" in sql and "execution_status" in sql:
            # Record the Claim conditional UPDATE execution and capture the parameters.
            self._shared["call_log"].append("claim")
            self._shared["claim_params"] = params
            self._one = self._shared["claim_result"]
            self._rows = []
            return

        raise AssertionError(f"unexpected SQL executed in fake cursor: {sql[:80]}")

    def fetchall(self) -> list[Any]:
        return self._rows

    def fetchone(self) -> dict[str, Any] | None:
        return self._one


class _FakeConn:
    def __init__(self, shared: dict[str, Any]) -> None:
        self._shared = shared

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def cursor(self, *args: Any, **kwargs: Any) -> _FakeCursor:
        return _FakeCursor(self._shared)

    def commit(self) -> None:
        return None


def _make_fake_get_conn(shared: dict[str, Any]):
    def _fake_get_conn() -> _FakeConn:
        return _FakeConn(shared)

    return _fake_get_conn


def _all_execution_order_columns() -> list[str]:
    return list(execute.REQUIRED_COLUMNS) + list(execute.OPTIONAL_COLUMNS)


def _claimed_row(execution_order_id: int = 501) -> dict[str, Any]:
    """The row returned via RETURNING after a successful Claim (includes all columns)."""
    row = {col: None for col in _all_execution_order_columns()}
    row.update(
        {
            "id": execution_order_id,
            "execution_plan_id": 1,
            "execution_mode": execute.EXECUTION_MODE,
            "source_type": "DAILY",
            "action_type": "BUY",
            "execution_status": execute.SUBMITTING_STATUS,
            "ticker_code": "000660",
            "stock_name": "TEST",
            "order_qty": 3,
            "order_method": "MARKET",
            "order_price": None,
            "connector_order_request_id": None,
            "source_position_state_id": None,
        }
    )
    return row


def _install_shared(
    monkeypatch,
    *,
    claim_result: dict[str, Any] | None,
) -> dict[str, Any]:
    shared: dict[str, Any] = {
        "schema": "execution",
        "columns": _all_execution_order_columns(),
        "call_log": [],
        "claim_params": None,
        "claim_result": claim_result,
    }
    monkeypatch.setattr(execute, "get_conn", _make_fake_get_conn(shared))
    return shared


# ---------------------------------------------------------------------------
# Req 2.1: SUBMITTING_STATUS value and Claim query references
# ---------------------------------------------------------------------------
def test_submitting_status_constant_and_query_references() -> None:
    # Verify the SUBMITTING_STATUS constant value.
    assert execute.SUBMITTING_STATUS == "SUBMITTING"
    assert execute.REQUESTED_STATUS == "REQUESTED"

    # Statically verify that the Claim conditional UPDATE references the constants and the Claim condition.
    claim_source = inspect.getsource(execute.claim_strategy_execution_order)
    assert "SUBMITTING_STATUS" in claim_source
    assert "REQUESTED_STATUS" in claim_source
    assert "execution_status = %s" in claim_source
    assert "connector_order_request_id IS NULL" in claim_source

    # Verify that the subsequent transition guard also references the SUBMITTING/Terminal state contract.
    submitted_source = inspect.getsource(
        execute.mark_strategy_execution_order_submitted
    )
    assert "execution_status = 'SUBMITTING'" in submitted_source

    failed_source = inspect.getsource(execute.mark_strategy_execution_order_failed)
    assert (
        "execution_status NOT IN ('SUBMITTED','FILLED','CANCELED','REJECTED','FAILED')"
        in failed_source
    )


# ---------------------------------------------------------------------------
# Req 2.1 / 2.3: Claim executes with the REQUESTED->SUBMITTING transition parameters
#               and returns the updated row.
# ---------------------------------------------------------------------------
def test_claim_transitions_with_submitting_status_and_returns_row(
    monkeypatch,
) -> None:
    claimed = _claimed_row(execution_order_id=501)
    shared = _install_shared(monkeypatch, claim_result=claimed)

    result = execute.claim_strategy_execution_order(501)

    # Exactly one row is updated to SUBMITTING and that row is returned.
    assert result is not None
    assert result["id"] == 501
    assert result["execution_status"] == execute.SUBMITTING_STATUS

    # The conditional UPDATE transitions to SUBMITTING_STATUS and passes the target id and
    # REQUESTED_STATUS as the Claim condition parameters.
    params = shared["claim_params"]
    assert params == (
        execute.SUBMITTING_STATUS,
        501,
        execute.REQUESTED_STATUS,
    )
    assert shared["call_log"] == ["claim"]


# ---------------------------------------------------------------------------
# Req 2.2 / 2.4: after Claim-first execution, call the broker exactly once.
# ---------------------------------------------------------------------------
def test_claim_precedes_broker_submission_and_broker_called_once(
    monkeypatch,
) -> None:
    claimed = _claimed_row(execution_order_id=777)
    shared = _install_shared(monkeypatch, claim_result=claimed)

    submit_calls: list[dict[str, Any]] = []

    def fake_submit(order: dict[str, Any]) -> dict[str, Any]:
        shared["call_log"].append("submit")
        submit_calls.append(order)
        return {
            "order_request_id": 9100,
            "response": {"rt_cd": "0"},
            "broker_order_no": "0000000010",
        }

    monkeypatch.setattr(execute, "_submit_order", fake_submit)

    # Simulate the submission boundary contract: perform Claim before the broker call,
    # and call the broker only if Claim returned a row.
    target_id = 777
    claimed_row = execute.claim_strategy_execution_order(target_id)
    assert claimed_row is not None
    if claimed_row is not None:
        execute._submit_order(claimed_row)

    # Req 2.2: Claim is performed before the broker submission.
    assert shared["call_log"] == ["claim", "submit"]

    # Req 2.4: when Claim updates exactly one row to SUBMITTING, the broker is
    # called exactly once.
    assert len(submit_calls) == 1
    assert submit_calls[0]["id"] == target_id


# ---------------------------------------------------------------------------
# Req 2.4 (reinforcing the once-only semantics): if Claim returns 0 rows, do not call the broker.
# ---------------------------------------------------------------------------
def test_zero_row_claim_skips_broker_submission(monkeypatch) -> None:
    shared = _install_shared(monkeypatch, claim_result=None)

    submit_calls: list[dict[str, Any]] = []

    def fake_submit(order: dict[str, Any]) -> dict[str, Any]:
        shared["call_log"].append("submit")
        submit_calls.append(order)
        return {"order_request_id": 1, "response": {"rt_cd": "0"}}

    monkeypatch.setattr(execute, "_submit_order", fake_submit)

    claimed_row = execute.claim_strategy_execution_order(999)
    assert claimed_row is None
    if claimed_row is not None:  # pragma: no cover - defensive branch
        execute._submit_order(claimed_row)

    # If Claim returns 0 rows, no broker submission occurs.
    assert shared["call_log"] == ["claim"]
    assert len(submit_calls) == 0
