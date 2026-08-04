"""Example test: Claim·mark 계약 (Task 7.4).

이 예시는 브로커 주문 제출 경계의 Claim 계약을 검증한다.

- `SUBMITTING_STATUS` 값과 Claim 조건부 UPDATE 참조 확인 (Requirement 2.1)
- Claim 선수행과 브로커 정확히 1회 호출 확인 (Requirements 2.2, 2.4)

Claim 기반 `run()` 처리 루프 통합은 Task 8에서 수행하므로, 이 예시는
`run()` 전체 배치 동작이 아니라 Claim + 브로커-1회 호출 + 순서 계약에
집중한다. 혼합 배치 지속·Dry_Run·Claim DB 오류 등 `run()` 루프 동작은
Task 8.2에서 다룬다.

모든 검증은 broker/token/DB 함수를 mock 또는 in-memory fake로 격리한
상태에서 수행하며, 실제 KIS API·token·운영 DB를 호출하지 않는다
(Requirement 6). 실제 `get_conn`은 fake로 대체하고, 대체되지 않은 채
실제 DB에 접근하려 하면 즉시 실패시킨다.
"""

from __future__ import annotations

import ast
import inspect
import os
from pathlib import Path
from typing import Any, Self


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

import connector_strategy_order_execute as execute


# ---------------------------------------------------------------------------
# In-memory fake DB
# ---------------------------------------------------------------------------
class _FakeCursor:
    """psycopg 커서를 흉내내는 최소 fake.

    - information_schema.columns 조회: strategy_execution_order 컬럼 목록 반환.
    - 조건부 UPDATE(claim): 사전 설정한 claim 결과 행을 RETURNING으로 반환.
    실제 DB나 네트워크에 접근하지 않는다.
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
            # Claim 조건부 UPDATE 실행 기록과 파라미터 캡처.
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
    """Claim 성공 후 RETURNING으로 돌아오는 행(모든 컬럼 포함)."""
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
# Req 2.1: SUBMITTING_STATUS 값과 Claim 쿼리 참조
# ---------------------------------------------------------------------------
def test_submitting_status_constant_and_query_references() -> None:
    # SUBMITTING_STATUS 상수 값 확인.
    assert execute.SUBMITTING_STATUS == "SUBMITTING"
    assert execute.REQUESTED_STATUS == "REQUESTED"

    # Claim 조건부 UPDATE가 상수와 Claim 조건을 참조하는지 정적 확인.
    claim_source = inspect.getsource(execute.claim_strategy_execution_order)
    assert "SUBMITTING_STATUS" in claim_source
    assert "REQUESTED_STATUS" in claim_source
    assert "execution_status = %s" in claim_source
    assert "connector_order_request_id IS NULL" in claim_source

    # 후속 전이 가드도 SUBMITTING/Terminal 상태 계약을 참조하는지 확인.
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
# Req 2.1 / 2.3: Claim은 REQUESTED→SUBMITTING 전이 파라미터로 실행되고
#               갱신된 행을 반환한다.
# ---------------------------------------------------------------------------
def test_claim_transitions_with_submitting_status_and_returns_row(
    monkeypatch,
) -> None:
    claimed = _claimed_row(execution_order_id=501)
    shared = _install_shared(monkeypatch, claim_result=claimed)

    result = execute.claim_strategy_execution_order(501)

    # 정확히 한 행이 SUBMITTING으로 갱신되어 그 행이 반환된다.
    assert result is not None
    assert result["id"] == 501
    assert result["execution_status"] == execute.SUBMITTING_STATUS

    # 조건부 UPDATE는 SUBMITTING_STATUS로 전이하고, 대상 id와
    # REQUESTED_STATUS를 Claim 조건 파라미터로 전달한다.
    params = shared["claim_params"]
    assert params == (
        execute.SUBMITTING_STATUS,
        501,
        execute.REQUESTED_STATUS,
    )
    assert shared["call_log"] == ["claim"]


# ---------------------------------------------------------------------------
# Req 2.2 / 2.4: Claim 선수행 후 브로커를 정확히 1회 호출한다.
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

    # 제출 경계 계약을 모사한다: 브로커 호출 이전에 Claim을 먼저 수행하고,
    # Claim이 한 행을 반환한 경우에만 브로커를 호출한다.
    target_id = 777
    claimed_row = execute.claim_strategy_execution_order(target_id)
    assert claimed_row is not None
    if claimed_row is not None:
        execute._submit_order(claimed_row)

    # Req 2.2: Claim이 브로커 제출보다 먼저 수행된다.
    assert shared["call_log"] == ["claim", "submit"]

    # Req 2.4: Claim이 정확히 한 행을 SUBMITTING으로 갱신하면 브로커를
    # 정확히 1회 호출한다.
    assert len(submit_calls) == 1
    assert submit_calls[0]["id"] == target_id


# ---------------------------------------------------------------------------
# Req 2.4 (once-only 의미 보강): Claim 0행이면 브로커를 호출하지 않는다.
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
    if claimed_row is not None:  # pragma: no cover - 방어적 분기
        execute._submit_order(claimed_row)

    # Claim이 0행이면 브로커 제출은 발생하지 않는다.
    assert shared["call_log"] == ["claim"]
    assert len(submit_calls) == 0
