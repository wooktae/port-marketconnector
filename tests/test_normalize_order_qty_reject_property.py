"""Property test: 무효 수량은 항상 거부된다 (Property 2).

이 테스트는 브로커 주문 제출 경계의 순수 함수 `normalize_order_qty`만 검증한다.
broker API, token, DB 함수를 호출하지 않으며, import 시 실제 side effect가 발생하지
않도록 config 환경변수를 import-only 더미 값으로 주입한 뒤 대상 모듈을 import한다.
"""

from __future__ import annotations

import ast
import os
from decimal import Decimal, InvalidOperation
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


def _is_unparseable_or_non_positive_integer_string(text: str) -> bool:
    """문자열이 1 이상의 정수로 정규화될 수 없으면 True.

    Decimal 파싱 실패(비숫자), 유한하지 않은 값, 소수부 존재, 정수지만 1 미만인
    경우를 모두 무효로 판정한다. 테스트 입력이 항상 거부 대상이 되도록 보장한다.
    """
    stripped = text.strip()
    try:
        parsed = Decimal(stripped)
    except (InvalidOperation, ValueError):
        return True
    if not parsed.is_finite():
        return True
    if parsed != parsed.to_integral_value():
        return True
    return int(parsed) < 1


# 0을 여러 표현으로: int, Decimal, float, str
_zero_values = st.sampled_from([0, Decimal(0), 0.0, "0", " 0 "])

# 음수 정수와 그 문자열 표현
_negative_integers = st.integers(max_value=-1)
_negative_integer_strings = _negative_integers.map(str)

# 소수부가 있는 Decimal (정수가 아님)
_non_integer_decimals = st.decimals(
    allow_nan=False,
    allow_infinity=False,
    places=2,
    min_value=Decimal(-10000),
    max_value=Decimal(10000),
).filter(lambda d: d != d.to_integral_value())

# 소수부가 있는 float (정수가 아님)
_non_integer_floats = st.floats(
    allow_nan=False,
    allow_infinity=False,
    min_value=-10000.0,
    max_value=10000.0,
).filter(lambda f: not f.is_integer())

# 소수부가 있는 숫자 문자열
_non_integer_number_strings = _non_integer_decimals.map(str)

# bool 값
_booleans = st.booleans()

# 숫자로 해석할 수 없는 문자열 (그리고 0·음수·소수 숫자 문자열도 포함)
_non_numeric_strings = st.text(min_size=0, max_size=12).filter(
    _is_unparseable_or_non_positive_integer_string
)

# 숫자로 해석할 수 없는 미지원 타입
_unsupported_types = st.one_of(
    st.none(),
    st.binary(max_size=8),
    st.lists(st.integers(), max_size=4),
    st.tuples(st.integers()),
    st.dictionaries(st.text(max_size=4), st.integers(), max_size=3),
    st.sets(st.integers(), max_size=4),
)

_invalid_quantities = st.one_of(
    _zero_values,
    _negative_integers,
    _negative_integer_strings,
    _non_integer_decimals,
    _non_integer_floats,
    _non_integer_number_strings,
    _booleans,
    _non_numeric_strings,
    _unsupported_types,
)


# Feature: connector-order-submission-guards, Property 2: 무효 수량은 항상 거부된다
# Validates: Requirements 3.2, 3.3, 3.4, 3.5, 3.6
@settings(max_examples=200)
@given(value=_invalid_quantities)
def test_invalid_quantity_is_always_rejected(value: object) -> None:
    """0·음수·정수가 아닌 소수·bool·비숫자·미지원 타입 입력에 대해

    `normalize_order_qty`는 절사·반올림 없이 `QuantityValidationError`를 발생시키고
    정규화 수량을 반환하지 않아야 한다. 어떤 값도 return되지 않고 항상 예외로만
    종료됨을 pytest.raises로 확인한다.
    """
    with pytest.raises(common.QuantityValidationError):
        common.normalize_order_qty(value)
