"""Property test: Fatal_Max 구성값 해석은 결정적으로 분기된다 (Property 3).

이 테스트는 브로커 주문 제출 경계의 순수 함수 `resolve_fatal_max_order_qty`만
검증한다. broker API, token, DB 함수를 호출하지 않으며, import 시 실제 side effect가
발생하지 않도록 config 환경변수를 import-only 더미 값으로 주입한 뒤 대상 모듈을
import한다.

각 예시마다 `STEP12_FATAL_MAX_ORDER_QTY` 환경변수만 설정/삭제하고, 예시 종료 시
원래 값을 그대로 복원해 예시 간 격리를 보장한다.
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

# 예시 종료 후 원상 복원을 위한 "미설정" 표식.
_MISSING = object()

# 환경변수를 아예 설정하지 않는 케이스를 표현하는 표식.
_UNSET = object()

# 기대 결과 표식.
_EXPECT_NONE = ("none",)
_EXPECT_ERROR = ("error",)


def _is_non_numeric(text: str) -> bool:
    """문자열이 숫자로 해석 불가하여 명시적 구성 오류를 유발하면 True.

    strip 후 빈 문자열은 미설정과 동일한 비활성(None)이므로 오류 케이스에서 제외한다.
    Decimal 파싱에 성공하는 문자열도 제외하여, 이 생성기가 항상 비숫자 오류만
    만들도록 보장한다.
    """
    # 환경변수 값은 null 바이트를 담을 수 없으므로 오류 케이스에서 제외한다.
    if "\x00" in text:
        return False
    stripped = text.strip()
    if stripped == "":
        return False
    try:
        Decimal(stripped)
    except (InvalidOperation, ValueError):
        return True
    return False


# 검사 비활성(None)을 유발하는 원시 값 케이스.
_disabled_cases = st.one_of(
    # 미설정
    st.just((_UNSET, _EXPECT_NONE)),
    # 빈 문자열·공백 문자열
    st.sampled_from(["", " ", "   ", "\t", "\n", " \t "]).map(
        lambda s: (s, _EXPECT_NONE)
    ),
    # 0 (앞뒤 공백 허용)
    st.sampled_from(["0", " 0", "0 ", " 0 ", "00", " 00 "]).map(
        lambda s: (s, _EXPECT_NONE)
    ),
)

# 양의 정수 상한을 반환하는 케이스. 앞뒤 공백은 strip 후 동일 결과여야 한다.
_positive_cases = st.tuples(
    st.integers(min_value=1, max_value=10**12),
    st.sampled_from(["{}", " {}", "{} ", " {} "]),
).map(lambda t: (t[1].format(t[0]), ("limit", t[0])))

# 음수 정수 문자열: 명시적 구성 오류.
_negative_int_cases = st.integers(max_value=-1).map(lambda n: (str(n), _EXPECT_ERROR))

# 소수부가 있는 Decimal 문자열: 명시적 구성 오류.
_non_integer_decimals = st.decimals(
    allow_nan=False,
    allow_infinity=False,
    places=2,
    min_value=Decimal(-10000),
    max_value=Decimal(10000),
).filter(lambda d: d != d.to_integral_value())
_decimal_cases = _non_integer_decimals.map(lambda d: (str(d), _EXPECT_ERROR))

# 숫자로 해석할 수 없는 문자열: 명시적 구성 오류.
_non_numeric_cases = st.text(min_size=1, max_size=12).filter(_is_non_numeric).map(
    lambda s: (s, _EXPECT_ERROR)
)

_fatal_max_cases = st.one_of(
    _disabled_cases,
    _positive_cases,
    _negative_int_cases,
    _decimal_cases,
    _non_numeric_cases,
)


# Feature: connector-order-submission-guards, Property 3: Fatal_Max 구성값 해석은 결정적으로 분기된다
# Validates: Requirements 3.10, 3.12
@settings(max_examples=200)
@given(case=_fatal_max_cases)
def test_fatal_max_resolution_is_deterministically_branched(case: object) -> None:
    """`STEP12_FATAL_MAX_ORDER_QTY` 문자열은 결정적으로 3분기된다.

    미설정·빈 문자열·`"0"`이면 검사 비활성(`None`), 양의 정수 문자열이면 그 정수
    상한을 반환하고, 음수·소수·비숫자 문자열이면 `FatalMaxConfigError`를 발생시킨다
    (조용히 무시하거나 비활성화하지 않는다). 각 예시는 환경변수를 설정/삭제한 뒤
    원래 값으로 복원해 예시 간 격리를 유지한다.
    """
    raw, expected = case
    env_key = common.FATAL_MAX_ORDER_QTY_ENV
    original = os.environ.get(env_key, _MISSING)

    try:
        if raw is _UNSET:
            os.environ.pop(env_key, None)
        else:
            os.environ[env_key] = raw

        if expected is _EXPECT_NONE:
            assert common.resolve_fatal_max_order_qty() is None
        elif expected is _EXPECT_ERROR:
            with pytest.raises(common.FatalMaxConfigError):
                common.resolve_fatal_max_order_qty()
        else:
            # ("limit", n): 양의 정수 상한을 값 변경 없이 반환한다.
            _, limit = expected
            result = common.resolve_fatal_max_order_qty()
            assert result == limit
            assert type(result) is int
    finally:
        if original is _MISSING:
            os.environ.pop(env_key, None)
        else:
            os.environ[env_key] = original
