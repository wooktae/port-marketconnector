"""Example test: 취소·정정 성공 후처리 상태 전이 (Requirement 5.7).

이 테스트는 `submit_rvsecncl_order()` 성공 경로의 후처리 상태 전이를 검증한다.

- `apply_rvsecncl_parent_status_after_success()`가 CANCEL이면 활성 주문을 비-Terminal
  상태에서 `CANCELED`로, MODIFY이면 `MODIFIED`로 전이하려고
  `update_order_request_status_if_not_terminal`을 기대한 (id, status) 인자로 호출하는지
  확인한다.
- `submit_rvsecncl_order()` 성공 경로의 CANCEL_ACCEPTED 전이가 취소 요청 row에 대해
  `update_order_request_status_if_not_terminal`을 `CANCEL_ACCEPTED` 상태로 호출하는지
  확인한다.

모든 검증은 broker API(`requests.post`/`requests.get`), token 함수, DB 함수를 mock으로
격리한 상태에서 수행한다. 실제 broker/DB/token 호출은 발생하지 않으며, 상태 전이 판정은
recording spy가 인자만 기록한다(실제 로컬 DB 미접근).
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

        # os.getenv("KEY") 및 os.environ.get("KEY")
        if isinstance(node, ast.Call):
            func = node.func

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

import connector_order_common as common


def _active_order() -> dict[str, Any]:
    return {
        "id": 77,
        "account_id": 1,
        "account_no": "00000000",
        "ticker_code": "000000",
        "stock_name": "TEST",
        "request_type": "BUY",
        "order_method": "MARKET",
        "order_price": None,
        "order_qty": 39,
        "parent_order_request_id": None,
        "strategy_name": "strategy_ai",
        "strategy_version": "test",
        "strategy_run_id": "test-run",
        "strategy_signal_id": None,
        "signal_date": "2026-08-03",
        "signal_type": "BUY",
        "signal_score": None,
        "signal_position_size": None,
        "request_status": "ACCEPTED",
        "broker_order_no": "0000000001",
        "broker_branch_code": "00000",
    }


def _install_status_transition_spy(monkeypatch, recorded: list[tuple[Any, ...]]) -> None:
    """`update_order_request_status_if_not_terminal`을 인자 기록 spy로 대체한다.

    실제 로컬 DB를 접근하지 않고 (order_request_id, request_status) 인자만 기록한다.
    갱신 성공을 의미하는 True를 반환한다.
    """

    def _spy(order_request_id, request_status, message=None):
        recorded.append((order_request_id, request_status))
        return True

    monkeypatch.setattr(
        common,
        "update_order_request_status_if_not_terminal",
        _spy,
    )


def test_apply_parent_status_cancel_drives_active_order_to_canceled(
    monkeypatch,
) -> None:
    recorded: list[tuple[Any, ...]] = []
    _install_status_transition_spy(monkeypatch, recorded)

    common.apply_rvsecncl_parent_status_after_success(
        action_type="CANCEL",
        active_order_id=77,
        root_original_id=77,
    )

    # CANCEL 후처리는 활성 주문(77)을 비-Terminal → CANCELED로 전이하려고 시도한다.
    assert recorded == [(77, "CANCELED")]


def test_apply_parent_status_modify_drives_active_order_to_modified(
    monkeypatch,
) -> None:
    recorded: list[tuple[Any, ...]] = []
    _install_status_transition_spy(monkeypatch, recorded)

    common.apply_rvsecncl_parent_status_after_success(
        action_type="MODIFY",
        active_order_id=77,
        root_original_id=77,
    )

    # MODIFY 후처리는 활성 주문(77)을 비-Terminal → MODIFIED로 전이하려고 시도한다.
    assert recorded == [(77, "MODIFIED")]


def _install_submit_mocks(
    monkeypatch,
    captured: dict[str, Any],
    recorded: list[tuple[Any, ...]],
) -> None:
    """`submit_rvsecncl_order()`를 broker/DB/token 없이 실행하기 위한 mock 격리.

    상태 전이 판정은 recorded 리스트에 인자만 기록하는 spy로 대체한다.
    """
    active = _active_order()

    monkeypatch.setattr(
        common,
        "load_order_request_for_action",
        lambda order_request_id: dict(active),
    )
    monkeypatch.setattr(
        common,
        "resolve_active_order_for_action",
        lambda order_request_id: dict(active),
    )
    monkeypatch.setattr(
        common,
        "ensure_connector_account",
        lambda **kwargs: 1,
    )
    monkeypatch.setattr(
        common,
        "insert_order_request",
        lambda record: 9001,
    )
    monkeypatch.setattr(
        common,
        "_insert_signal_map_if_needed",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        common,
        "update_order_request_status_only",
        lambda *args, **kwargs: None,
    )
    # CANCEL_ACCEPTED 전이와 후처리가 사용하는 조건부 UPDATE를 인자 기록 spy로 대체한다.
    _install_status_transition_spy(monkeypatch, recorded)
    monkeypatch.setattr(
        common,
        "apply_rvsecncl_parent_status_after_success",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        common,
        "_finalize_order_request_from_response",
        lambda **kwargs: (True, "0000000002", "00000"),
    )

    captured["request_api_call_count"] = 0

    def fake_request_api(**kwargs):
        captured["request_api_call_count"] += 1
        captured["request_payload"] = kwargs["request_payload"]

        return (
            object(),
            {
                "rt_cd": "0",
                "msg_cd": "0",
                "msg1": "SUCCESS",
                "output": {
                    "ODNO": "0000000002",
                    "KRX_FWDG_ORD_ORGNO": "00000",
                },
            },
            1,
        )

    monkeypatch.setattr(
        common,
        "_request_api",
        fake_request_api,
    )

    # 실제 broker/token 경계 진입 시 즉시 실패시키는 fail-fast 가드.
    def _fail_real_broker_call(*args, **kwargs):
        raise AssertionError("real requests.post must not be called during validation")

    def _fail_real_token_call(*args, **kwargs):
        raise AssertionError("real token function must not be called during validation")

    monkeypatch.setattr(common.requests, "post", _fail_real_broker_call)
    monkeypatch.setattr(common.requests, "get", _fail_real_broker_call)
    monkeypatch.setattr(common, "get_access_token", _fail_real_token_call)
    monkeypatch.setattr(common, "check_and_refresh_token", _fail_real_token_call)


def test_submit_cancel_success_marks_cancel_request_as_cancel_accepted(
    monkeypatch,
) -> None:
    captured: dict[str, Any] = {}
    recorded: list[tuple[Any, ...]] = []
    _install_submit_mocks(monkeypatch, captured, recorded)

    common.submit_rvsecncl_order(
        action_type="CANCEL",
        api_name="order-cancel",
        tr_id="TEST_TR_ID",
        rvse_cncl_dvsn_cd="02",
        original_order_request_id=77,
        qty=None,
    )

    # 취소 요청 row(order_request_id=9001)를 비-Terminal → CANCEL_ACCEPTED로 전이한다.
    assert (9001, "CANCEL_ACCEPTED") in recorded
    # mock broker 진입점은 정확히 1회 호출되고, 실제 broker/token 경계는 호출되지 않는다.
    assert captured["request_api_call_count"] == 1
