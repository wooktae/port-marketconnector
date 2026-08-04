"""Property test: 유효 수량은 값 보존 정규화된다 (Property 1).

이 테스트는 브로커 주문 제출 경계의 순수 함수 `normalize_order_qty`만 검증한다.
broker API, token, DB 함수를 호출하지 않으며, import 시 실제 side effect가 발생하지
않도록 config 환경변수를 import-only 더미 값으로 주입한 뒤 대상 모듈을 import한다.
"""

from __future__ import annotations

import ast
import os
from decimal import Decimal
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


# Feature: connector-order-submission-guards, Property 1: 유효 수량은 값 보존 정규화된다
# Validates: Requirements 1.1, 3.1, 3.7, 3.8
@settings(max_examples=200)
@given(n=st.integers(min_value=1, max_value=10**12))
def test_valid_quantity_is_value_preserving_normalized(n: int) -> None:
    """1 이상의 정수 n에 대해 int, Decimal(n), str(n) 세 표현 모두

    값 변경 없이 정확히 정수 n으로 정규화되어야 한다. 정규화 결과는 브로커 호출까지
    그대로 전달되는 값이므로, 세 표현이 동일한 int n을 반환하는지 확인한다.
    """
    from_int = common.normalize_order_qty(n)
    from_decimal = common.normalize_order_qty(Decimal(n))
    from_str = common.normalize_order_qty(str(n))

    # 값 보존: 세 표현 모두 정확히 정수 n을 반환한다.
    assert from_int == n
    assert from_decimal == n
    assert from_str == n

    # 타입 보존: 반환 타입은 int이며 float/Decimal로 변형되지 않는다.
    assert type(from_int) is int
    assert type(from_decimal) is int
    assert type(from_str) is int

    # 세 입력 표현은 동일한 정규화 결과를 낸다(표현 무관 결정성).
    assert from_int == from_decimal == from_str
