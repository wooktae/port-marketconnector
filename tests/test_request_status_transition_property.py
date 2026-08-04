"""Property test: 로컬 주문 요청 상태는 Terminal에서 역행하지 않는다 (Property 10).

이 테스트는 `submit_rvsecncl_order()` 경로의 순수 함수
`resolve_request_status_transition`만 검증한다. broker API, token, DB 함수를
호출하지 않으며, import 시 실제 side effect가 발생하지 않도록 config 환경변수를
import-only 더미 값으로 주입한 뒤 대상 모듈을 import한다.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

from hypothesis import given, settings
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

import connector_order_common as common

# Terminal 상태와 대표 비-Terminal 상태를 함께 포함하는 상태 집합.
_TERMINAL_STATUSES = ("FILLED", "CANCELED", "REJECTED", "FAILED")
_NON_TERMINAL_STATUSES = ("PENDING", "ACCEPTED", "CANCEL_ACCEPTED", "MODIFIED")
_ALL_STATUSES = _TERMINAL_STATUSES + _NON_TERMINAL_STATUSES

# requested_status는 항상 str이며, current_status는 None을 포함할 수 있다.
_requested_status = st.sampled_from(_ALL_STATUSES)
_current_status = st.one_of(st.none(), st.sampled_from(_ALL_STATUSES))


# Feature: connector-order-submission-guards, Property 10: 로컬 주문 요청 상태는 Terminal에서 역행하지 않는다
# Validates: Requirements 5.1, 5.2, 5.3, 5.4, 5.5, 5.6
@settings(max_examples=200)
@given(current_status=_current_status, requested_status=_requested_status)
def test_request_status_transition_never_reverts_terminal(
    current_status: str | None,
    requested_status: str,
) -> None:
    """Terminal 상태에서 역행하지 않는 단조성 계약을 검증한다.

    - 현재 상태가 비-Terminal(또는 None)이면 갱신을 허용하고 결과는 요청 상태다.
    - 현재 상태가 Terminal이고 요청 상태가 현재와 같으면 오류 없이 허용(no-op)하고
      결과는 그 Terminal 상태를 유지한다.
    - 현재 상태가 Terminal이고 요청 상태가 다르면 갱신을 차단(성공 아님)하고 결과는
      현재 Terminal 상태를 변경 없이 유지한다.
    """
    result = common.resolve_request_status_transition(current_status, requested_status)

    if current_status in _TERMINAL_STATUSES:
        if requested_status == current_status:
            # 동일 Terminal 재적용: 허용(no-op), 결과는 그 Terminal 상태.
            assert result.allowed is True
            assert result.result_status == current_status
        else:
            # Terminal 역행 차단: 성공 아님, 현재 Terminal 상태 그대로 유지.
            assert result.allowed is False
            assert result.result_status == current_status
    else:
        # 비-Terminal(또는 None): 갱신 허용, 결과는 요청 상태.
        assert result.allowed is True
        assert result.result_status == requested_status
