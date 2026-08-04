"""Example test: Claim 기반 run() 루프 배치 동작 (Task 8.2).

이 예시는 `connector_strategy_order_execute.run()`의 `--execute` 루프와
Dry_Run 경로를 mock 격리 상태에서 직접 실행해 다음을 검증한다.

- 혼합 배치 전체 순회 (Requirement 2.6):
  일부 주문이 수량 검증에 실패하고 일부 Claim이 0행(skip)이어도, 루프는
  대상 주문을 모두 순회하며 단위 실패/skip을 기록한 뒤 계속 진행한다.
- Dry_Run 미변경 (Requirement 2.12):
  `--execute`가 없으면 Claim과 모든 `execution_status` DB 변경을 수행하지
  않는다. claim/mark/broker 제출 `call_count == 0`을 확인한다.
- Claim DB 오류 (Requirement 2.13):
  Claim 조건부 UPDATE가 DB 오류로 실패하면 해당 주문은 브로커를 호출하지
  않고 단위 실패(mark_failed)로 기록되며, 배치는 나머지 주문으로 계속된다.

모든 검증은 broker/token/DB 함수를 mock으로 격리한 상태에서 수행하며,
실제 KIS API·token·운영 DB를 호출하지 않는다 (Requirement 6). run() 루프가
호출하는 module-level 함수(fetch·claim·mark·broker 제출)를 mock으로 대체하고,
실제 `get_conn`과 실제 broker 제출 진입점은 호출되면 즉시 실패하도록 가드한다.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path
from typing import Any


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
# Test doubles / spies
# ---------------------------------------------------------------------------
class _GetConnGuard:
    """실제 DB 접근을 즉시 실패시키는 가드.

    run() 경로의 모든 DB 함수를 mock으로 대체했으므로, 실제 get_conn이
    호출되면 mock 미적용을 의미하며 검증을 즉시 실패시킨다 (Requirement 6).
    """

    def __call__(self, *args: Any, **kwargs: Any):
        raise AssertionError("real get_conn must not be called in run() loop tests")


def _guard_real_broker_submit(*args: Any, **kwargs: Any):
    """실제 broker 제출 진입점 가드.

    run() 루프 테스트는 broker 제출을 rate-limit-retry wrapper 수준에서
    mock하므로, 실제 _submit_order가 호출되면 broker/token side effect 위험을
    의미하며 검증을 즉시 실패시킨다 (Requirement 6).
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
    """run() 루프가 호출하는 module-level 함수를 mock으로 격리한다."""

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

        # 실제 side-effect 진입점 가드.
        monkeypatch.setattr(execute, "get_conn", _GetConnGuard())
        monkeypatch.setattr(execute, "_submit_order", _guard_real_broker_submit)

        # --execute 환경 요구와 retry recovery는 DB에 접근하므로 격리한다.
        monkeypatch.setattr(execute, "_require_execute_environment", lambda: None)
        monkeypatch.setattr(
            execute,
            "normalize_retryable_rejected_orders",
            lambda **kwargs: [],
        )
        # Fatal_Max는 이 테스트 범위 밖이므로 비활성(None)으로 고정한다.
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
# Req 2.6: 혼합 배치 전체 순회
# ---------------------------------------------------------------------------
def test_mixed_batch_continues_through_failure_and_skip(monkeypatch) -> None:
    # id=1 정상 제출, id=2 수량 검증 실패, id=3 Claim 0행 skip, id=4 정상 제출.
    orders = [
        _make_order(1, 3),
        _make_order(2, 2.5),  # 소수부 -> QuantityValidationError (브로커 미호출)
        _make_order(3, 5),
        _make_order(4, 7),
    ]
    harness = _RunHarness(monkeypatch, orders)
    harness.claim_behavior[3] = "skip"

    rc = execute.run(_execute_args())

    assert rc == 0

    # 배치는 4개 주문을 모두 순회한다. id=2는 검증 실패로 Claim 이전에
    # 중단되므로 Claim은 검증을 통과한 1·3·4에 대해서만 시도된다.
    assert harness.claim_calls == [1, 3, 4]

    # 브로커 제출은 Claim이 행을 반환한 1·4에 대해서만 정확히 발생한다.
    # 검증 실패(2)와 skip(3)은 브로커를 호출하지 않는다.
    assert harness.submit_calls == [1, 4]

    # 단위 실패는 수량 검증 실패 주문(2)에 대해서만 기록된다.
    assert harness.failed_calls == [2]

    # 정상 제출된 주문(1·4)만 SUBMITTED로 기록된다. skip(3)은 상태 변경 없음.
    assert harness.submitted_calls == [1, 4]


# ---------------------------------------------------------------------------
# Req 2.12: Dry_Run은 Claim과 execution_status DB 변경, 브로커 호출을
#           수행하지 않는다.
# ---------------------------------------------------------------------------
def test_dry_run_performs_no_claim_mark_or_broker_call(monkeypatch) -> None:
    orders = [
        _make_order(11, 3),
        _make_order(12, 9),
    ]
    harness = _RunHarness(monkeypatch, orders)

    rc = execute.run(_dry_run_args())

    assert rc == 0

    # Dry_Run은 순수 검증만 수행하고 Claim·mark·broker 제출을 하지 않는다.
    assert len(harness.claim_calls) == 0
    assert len(harness.submit_calls) == 0
    assert len(harness.submitted_calls) == 0
    assert len(harness.failed_calls) == 0
    assert len(harness.sell_ordered_calls) == 0


# ---------------------------------------------------------------------------
# Req 2.13: Claim DB 오류 시 브로커 미호출·단위 실패, 배치 계속
# ---------------------------------------------------------------------------
def test_claim_db_error_skips_broker_and_continues_batch(monkeypatch) -> None:
    # id=10 Claim DB 오류, id=11 정상 제출.
    orders = [
        _make_order(10, 3),
        _make_order(11, 4),
    ]
    harness = _RunHarness(monkeypatch, orders)
    harness.claim_behavior[10] = "db_error"

    rc = execute.run(_execute_args())

    assert rc == 0

    # Claim은 두 주문 모두 시도된다(배치가 오류로 중단되지 않음).
    assert harness.claim_calls == [10, 11]

    # Claim DB 오류 주문(10)은 브로커를 호출하지 않는다.
    # 정상 주문(11)만 브로커를 정확히 1회 호출한다.
    assert harness.submit_calls == [11]

    # Claim DB 오류 주문(10)은 단위 실패로 기록된다.
    assert harness.failed_calls == [10]

    # 배치는 계속되어 정상 주문(11)이 SUBMITTED로 기록된다.
    assert harness.submitted_calls == [11]


# ---------------------------------------------------------------------------
# 브로커 성공 후 SUBMITTED 상태 동기화가 갱신 행 없이(None) 실패하는 경우.
#
# 브로커는 이미 성공 응답을 반환했으므로 이를 재제출 가능한 FAILED로
# 덮어쓰지 않는다. SUBMITTED_STATE_SYNC_FAILED로 구분 기록하고, SELL 후속
# 처리와 [SUBMITTED] 성공 로그를 수행하지 않은 채 다음 주문으로 계속한다.
# ---------------------------------------------------------------------------
def test_broker_success_then_mark_submitted_returns_none(monkeypatch, capsys) -> None:
    # id=21 SELL: broker 성공, mark_submitted None. id=22 정상 제출로 배치 지속 확인.
    sell_order = _make_order(21, 3, action="SELL")
    sell_order["source_position_state_id"] = 555  # None-return 가드로 미호출 검증
    orders = [
        sell_order,
        _make_order(22, 4, action="SELL"),
    ]
    harness = _RunHarness(monkeypatch, orders)
    harness.mark_submitted_behavior[21] = "none"

    rc = execute.run(_execute_args())
    out = capsys.readouterr().out

    assert rc == 0

    # broker mock 호출은 정확히 1회(id=21). id=22도 정상 제출된다.
    assert harness.submit_calls == [21, 22]

    # SUBMITTED 반환이 None이면 SELL_ORDERED 후속 처리를 하지 않는다.
    assert harness.sell_ordered_calls == []

    # 재제출 가능한 FAILED로 덮어쓰지 않는다.
    assert harness.failed_calls == []

    # SUBMITTED_STATE_SYNC_FAILED 구분 기록이 남는다.
    assert "SUBMITTED_STATE_SYNC_FAILED" in out
    assert "execution_order_id=21" in out

    # id=21의 [SUBMITTED] 성공 로그는 출력되지 않고, id=22만 성공 처리된다.
    assert "[SUBMITTED] execution_order_id=21" not in out
    assert harness.submitted_calls == [21, 22]
    assert 22 in harness.sell_ordered_calls or "[SUBMITTED] execution_order_id=22" in out


# ---------------------------------------------------------------------------
# 브로커 성공 후 SUBMITTED 상태 반영 중 DB 예외가 발생하는 경우.
#
# 브로커 응답이 이미 성공일 수 있으므로 mark_failed를 호출하지 않고,
# SUBMITTED_STATE_SYNC_FAILED로 구분 기록한 뒤 다음 주문으로 계속한다.
# ---------------------------------------------------------------------------
def test_broker_success_then_mark_submitted_raises(monkeypatch, capsys) -> None:
    # id=31 SELL: broker 성공, mark_submitted 예외. id=32 정상 제출로 배치 지속 확인.
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

    # broker mock 호출은 id=31에 대해 정확히 1회, id=32도 정상 제출된다.
    assert harness.submit_calls == [31, 32]

    # 브로커 성공 후 상태 반영 예외는 FAILED로 덮어쓰지 않는다.
    assert harness.failed_calls == []

    # SELL_ORDERED 후속 처리도 하지 않는다.
    assert 31 not in harness.sell_ordered_calls

    # SUBMITTED_STATE_SYNC_FAILED 구분 기록이 남는다.
    assert "SUBMITTED_STATE_SYNC_FAILED" in out
    assert "execution_order_id=31" in out

    # 배치는 계속되어 id=32가 정상 제출된다.
    assert harness.submitted_calls == [31, 32]


# ---------------------------------------------------------------------------
# Claim 오류와 실패 상태 기록(mark_failed) 오류가 동시에 발생하는 이중 오류.
#
# 실패 상태 기록까지 실패해도 run() 바깥으로 예외가 전파되지 않고,
# CLAIM_FAILED_STATUS_WRITE_FAILED로 구분 기록한 뒤 배치를 계속한다.
# ---------------------------------------------------------------------------
def test_claim_error_and_mark_failed_error_do_not_abort_batch(
    monkeypatch, capsys
) -> None:
    # id=41 Claim 예외 + mark_failed 예외, id=42 정상 제출.
    orders = [
        _make_order(41, 3),
        _make_order(42, 4),
    ]
    harness = _RunHarness(monkeypatch, orders)
    harness.claim_behavior[41] = "db_error"
    harness.mark_failed_behavior[41] = "db_error"

    # run()이 예외로 종료되지 않아야 한다.
    rc = execute.run(_execute_args())
    out = capsys.readouterr().out

    assert rc == 0

    # 첫 주문(41)은 Claim 실패로 브로커를 호출하지 않는다.
    # 둘째 주문(42)만 브로커를 정확히 1회 호출한다.
    assert harness.submit_calls == [42]

    # 이중 오류 구분 기록이 남는다.
    assert "CLAIM_FAILED_STATUS_WRITE_FAILED" in out
    assert "execution_order_id=41" in out

    # 배치는 계속되어 둘째 주문(42)이 SUBMITTED로 기록된다.
    assert harness.submitted_calls == [42]
