"""Property test: execution_status guard transitions never revert a Terminal state (Property 6).

This test validates the state guard transition functions in
`connector_strategy_order_execute.py` (`claim_strategy_execution_order`,
`mark_strategy_execution_order_submitted`, `mark_strategy_execution_order_failed`)
with an in-memory fake DB store.

It does not call the real broker API, token, or operating DB. It replaces `get_conn`,
`_table_info`, and `_select_exprs` with fakes, and the fake cursor models each function's
actual conditional UPDATE WHERE guard as-is to validate only the guard semantics. To
avoid side effects on import, it injects import-only dummy values for the config
environment variables before importing the target module.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path
from typing import Any, Self

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st


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

import connector_strategy_order_execute as execmod

TERMINAL_STATUSES = ("SUBMITTED", "FILLED", "CANCELED", "REJECTED", "FAILED")
ALL_STATUSES = ("REQUESTED", "SUBMITTING", *TERMINAL_STATUSES)


class _Store:
    """An in-memory store modeling a single strategy_execution_order row."""

    def __init__(self) -> None:
        self.row: dict[str, Any] | None = None


class _FakeCursor:
    """A fake cursor that models the target functions' actual conditional UPDATE WHERE guards.

    Rather than a full SQL parser, it identifies the three statements by keyword and
    applies the modeled guards based on params. The guards must have exactly the same
    semantics as the original SQL.
    """

    def __init__(self, store: _Store) -> None:
        self._store = store
        self._result: dict[str, Any] | None = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def execute(self, sql: str, params: Any = ()) -> None:
        normalized = " ".join(sql.split())
        params = tuple(params or ())
        row = self._store.row

        # mark_strategy_execution_order_submitted:
        #   SET execution_status = 'SUBMITTED' ... WHERE id = %s AND execution_status = 'SUBMITTING'
        #   params = (connector_order_request_id, payload, execution_order_id)
        if "SET execution_status = 'SUBMITTED'" in normalized:
            connector_order_request_id, _payload, order_id = params
            if (
                row is not None
                and row["id"] == order_id
                and row["execution_status"] == "SUBMITTING"
            ):
                row["execution_status"] = "SUBMITTED"
                row["connector_order_request_id"] = connector_order_request_id
                self._result = {
                    "id": row["id"],
                    "execution_status": row["execution_status"],
                    "connector_order_request_id": row["connector_order_request_id"],
                }
            else:
                self._result = None
            return

        # mark_strategy_execution_order_failed:
        #   SET execution_status = 'FAILED' ...
        #   WHERE id = %s AND execution_status NOT IN (terminal set)
        #   connector_order_request_id = COALESCE(%s, connector_order_request_id)
        #   params = (connector_order_request_id, payload, execution_order_id)
        if "SET execution_status = 'FAILED'" in normalized:
            connector_order_request_id, _payload, order_id = params
            if (
                row is not None
                and row["id"] == order_id
                and row["execution_status"] not in TERMINAL_STATUSES
            ):
                row["execution_status"] = "FAILED"
                if connector_order_request_id is not None:
                    row["connector_order_request_id"] = connector_order_request_id
                self._result = {
                    "id": row["id"],
                    "execution_status": row["execution_status"],
                    "connector_order_request_id": row["connector_order_request_id"],
                }
            else:
                self._result = None
            return

        # claim_strategy_execution_order:
        #   SET execution_status = %s ...
        #   WHERE id = %s AND execution_status = %s AND connector_order_request_id IS NULL
        #   params = (SUBMITTING_STATUS, execution_order_id, REQUESTED_STATUS)
        if (
            "SET execution_status = %s" in normalized
            and "connector_order_request_id IS NULL" in normalized
        ):
            next_status, order_id, required_status = params
            if (
                row is not None
                and row["id"] == order_id
                and row["execution_status"] == required_status
                and row["connector_order_request_id"] is None
            ):
                row["execution_status"] = next_status
                self._result = dict(row)
            else:
                self._result = None
            return

        raise AssertionError(f"Unexpected SQL in fake store: {normalized}")

    def fetchone(self) -> dict[str, Any] | None:
        return self._result


class _FakeConn:
    def __init__(self, store: _Store) -> None:
        self._store = store
        self.commit_count = 0

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def cursor(self, row_factory: Any = None) -> _FakeCursor:
        return _FakeCursor(self._store)

    def commit(self) -> None:
        self.commit_count += 1


@pytest.fixture()
def fake_store(monkeypatch: pytest.MonkeyPatch) -> _Store:
    """Block real DB access by replacing get_conn and the schema helpers with fakes."""
    store = _Store()

    monkeypatch.setattr(execmod, "get_conn", lambda: _FakeConn(store))
    monkeypatch.setattr(
        execmod,
        "_table_info",
        lambda table_name: ("fake.strategy_execution_order", set()),
    )
    monkeypatch.setattr(
        execmod,
        "_select_exprs",
        lambda columns: ("id, execution_status, connector_order_request_id", []),
    )
    return store


# Feature: connector-order-submission-guards, Property 6: execution_status guard transitions never revert a Terminal state
# Validates: Requirements 2.3, 2.5, 2.6, 2.8, 2.9, 2.10, 2.11
@settings(
    max_examples=200,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    order_id=st.integers(min_value=1, max_value=10**9),
    initial_status=st.sampled_from(ALL_STATUSES),
    initial_corid=st.one_of(st.none(), st.integers(min_value=1, max_value=10**9)),
)
def test_execution_status_guard_transitions_never_revert_terminal(
    fake_store: _Store,
    order_id: int,
    initial_status: str,
    initial_corid: int | None,
) -> None:
    """Validate the Claim/mark guard transition semantics from an arbitrary initial execution_status.

    - Claim transitions exactly one row to SUBMITTING only when it is REQUESTED and
      connector_order_request_id IS NULL; otherwise it changes no state (0 rows / None).
    - mark_submitted transitions to SUBMITTED only when the current state is SUBMITTING.
    - mark_failed does not overwrite with FAILED when the current state is Terminal, keeping the existing state.
    """

    # --- Claim: succeeds only when REQUESTED + connector_order_request_id IS NULL ---
    fake_store.row = {
        "id": order_id,
        "execution_status": initial_status,
        "connector_order_request_id": initial_corid,
    }
    claim_result = execmod.claim_strategy_execution_order(order_id)
    claim_should_win = initial_status == "REQUESTED" and initial_corid is None

    if claim_should_win:
        assert claim_result is not None
        assert claim_result["execution_status"] == execmod.SUBMITTING_STATUS
        assert fake_store.row["execution_status"] == execmod.SUBMITTING_STATUS
    else:
        assert claim_result is None
        # State unchanged (skip): the initial value is kept as-is
        assert fake_store.row["execution_status"] == initial_status
        assert fake_store.row["connector_order_request_id"] == initial_corid

    # --- mark_submitted: transitions to SUBMITTED only when the current state is SUBMITTING ---
    fake_store.row = {
        "id": order_id,
        "execution_status": initial_status,
        "connector_order_request_id": initial_corid,
    }
    submitted_result = execmod.mark_strategy_execution_order_submitted(
        order_id, 555, {"ok": True}
    )
    submitted_should_win = initial_status == "SUBMITTING"

    if submitted_should_win:
        assert submitted_result is not None
        assert submitted_result["execution_status"] == execmod.SUBMITTED_STATUS
        assert fake_store.row["execution_status"] == execmod.SUBMITTED_STATUS
        assert fake_store.row["connector_order_request_id"] == 555
    else:
        assert submitted_result is None
        assert fake_store.row["execution_status"] == initial_status
        assert fake_store.row["connector_order_request_id"] == initial_corid

    # --- mark_failed: does not overwrite a Terminal state with FAILED ---
    fake_store.row = {
        "id": order_id,
        "execution_status": initial_status,
        "connector_order_request_id": initial_corid,
    }
    failed_result = execmod.mark_strategy_execution_order_failed(
        order_id, {"error": "boom"}
    )
    failed_is_terminal = initial_status in TERMINAL_STATUSES

    if failed_is_terminal:
        # Block Terminal reversal: 0 rows, keep the existing state
        assert failed_result is None
        assert fake_store.row["execution_status"] == initial_status
        assert fake_store.row["connector_order_request_id"] == initial_corid
    else:
        # REQUESTED and SUBMITTING are not Terminal, so the FAILED transition is allowed
        assert failed_result is not None
        assert failed_result["execution_status"] == execmod.FAILED_STATUS
        assert fake_store.row["execution_status"] == execmod.FAILED_STATUS
