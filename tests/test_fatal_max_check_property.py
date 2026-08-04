"""Property test: Fatal_Max 검사는 수량을 조정하지 않는다 (Property 4).

이 테스트는 브로커 주문 제출 경계의 순수 함수 `check_fatal_max_order_qty`만 검증한다.
broker API, token, DB 함수를 호출하지 않으며, import 시 실제 side effect가 발생하지
않도록 config 환경변수를 import-only 더미 값으로 주입한 뒤 대상 모듈을 import한다.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest
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


# Feature: connector-order-submission-guards, Property 4: Fatal_Max 검사는 수량을 조정하지 않는다
# Validates: Requirements 3.11
@settings(max_examples=200)
@given(data=st.data())
def test_fatal_max_check_never_adjusts_quantity(data: st.DataObject) -> None:
    """양의 상한 c와 정규화 수량 q에 대해, q <= c이면 통과하고 q를 값 변경 없이

    그대로 반환하며, q > c이면 `FatalMaxExceededError`를 발생시킨다. 어떤 경우에도
    q를 상한으로 축소·조정하지 않는다. 상한이 `None`이면 검사가 비활성이므로 q를
    값 변경 없이 그대로 반환한다.
    """
    limit = data.draw(st.integers(min_value=1, max_value=10**12))

    # q <= c 와 q > c 두 영역을 모두 덮도록 q를 생성한다.
    qty = data.draw(st.integers(min_value=1, max_value=10**12 + 10**6))

    if qty <= limit:
        # 통과 경로: q를 값 변경 없이 그대로 반환한다(상한으로 조정하지 않음).
        result = common.check_fatal_max_order_qty(qty, limit)
        assert result == qty
        assert type(result) is int
    else:
        # 초과 경로: 예외를 발생시키고, 상한으로 축소된 값을 반환하지 않는다.
        with pytest.raises(common.FatalMaxExceededError):
            common.check_fatal_max_order_qty(qty, limit)


# Feature: connector-order-submission-guards, Property 4: Fatal_Max 검사는 수량을 조정하지 않는다
# Validates: Requirements 3.11
@settings(max_examples=200)
@given(qty=st.integers(min_value=1, max_value=10**12))
def test_fatal_max_check_disabled_passes_through(qty: int) -> None:
    """상한이 `None`(검사 비활성)이면 어떤 양의 수량 q도 값 변경 없이 그대로 반환한다."""
    result = common.check_fatal_max_order_qty(qty, None)
    assert result == qty
    assert type(result) is int
