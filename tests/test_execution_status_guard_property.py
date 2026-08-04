"""Property test: execution_status 가드 전이는 Terminal 상태를 역행시키지 않는다 (Property 6).

이 테스트는 `connector_strategy_order_execute.py`의 상태 가드 전이 함수
(`claim_strategy_execution_order`, `mark_strategy_execution_order_submitted`,
`mark_strategy_execution_order_failed`)를 in-memory fake DB store로 검증한다.

실제 broker API, token, 운영 DB를 호출하지 않는다. `get_conn`, `_table_info`,
`_select_exprs`를 fake로 대체하고, fake cursor가 각 함수의 실제 조건부 UPDATE
WHERE 가드를 그대로 모델링하여 가드 의미만 검증한다. import 시 side effect가
발생하지 않도록 config 환경변수를 import-only 더미 값으로 주입한 뒤 대상 모듈을
import한다.
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
    """config.py가 import 시 요구하는 환경변수를 더미 값으로만 채운다.

    실제 KIS 키·계좌 값을 읽거나 기록하지 않고, broker/DB/token side effect도
    유발하지 않는다. 이미 설정된 키는 덮어쓰지 않는다.
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
    """단일 strategy_execution_order 행을 모델링하는 in-memory store."""

    def __init__(self) -> None:
        self.row: dict[str, Any] | None = None


class _FakeCursor:
    """대상 함수의 실제 조건부 UPDATE WHERE 가드를 모델링하는 fake cursor.

    전체 SQL 파서가 아니라, 세 statement를 keyword로 식별하고 params 기반으로
    modeled 가드를 적용한다. 가드는 원본 SQL과 정확히 동일한 의미여야 한다.
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
    """get_conn과 스키마 helper를 fake로 대체하여 실제 DB 접근을 차단한다."""
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


# Feature: connector-order-submission-guards, Property 6: execution_status 가드 전이는 Terminal 상태를 역행시키지 않는다
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
    """임의 초기 execution_status에서 Claim·mark 가드 전이 의미를 검증한다.

    - Claim은 REQUESTED이고 connector_order_request_id IS NULL일 때만 정확히 한 행을
      SUBMITTING으로 전이하고, 그 외에는 0행(None)으로 상태를 변경하지 않는다.
    - mark_submitted는 현재 SUBMITTING일 때만 SUBMITTED로 전이한다.
    - mark_failed는 현재 상태가 Terminal이면 FAILED로 덮어쓰지 않고 기존 상태를 유지한다.
    """

    # --- Claim: REQUESTED + connector_order_request_id IS NULL 일 때만 성공 ---
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
        # 상태 불변(skip): 초기값 그대로 유지
        assert fake_store.row["execution_status"] == initial_status
        assert fake_store.row["connector_order_request_id"] == initial_corid

    # --- mark_submitted: 현재 SUBMITTING 일 때만 SUBMITTED 전이 ---
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

    # --- mark_failed: Terminal 상태는 FAILED로 덮어쓰지 않음 ---
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
        # Terminal 역행 차단: 0행, 기존 상태 유지
        assert failed_result is None
        assert fake_store.row["execution_status"] == initial_status
        assert fake_store.row["connector_order_request_id"] == initial_corid
    else:
        # REQUESTED, SUBMITTING 은 Terminal 이 아니므로 FAILED 전이 허용
        assert failed_result is not None
        assert failed_result["execution_status"] == execmod.FAILED_STATUS
        assert fake_store.row["execution_status"] == execmod.FAILED_STATUS
