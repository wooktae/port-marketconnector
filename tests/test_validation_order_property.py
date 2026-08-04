"""Property test: 수량 검증은 Fatal_Max 검사보다 먼저 수행된다 (Property 5).

이 테스트는 브로커 주문 제출 경계의 순수 함수 `normalize_order_qty`(수량 검증, C1)와
`check_fatal_max_order_qty`(Fatal Max 검사, C2)의 수행 순서만 검증한다.

`connector_order_common.py`에는 두 함수를 조합하는 compose helper가 아직 없고,
enforcement 연결은 후속 task(전략 주문 `--execute` 루프)의 책임이다. 따라서 여기서는
production wiring을 수정하지 않고, 설계가 명시한 순서(수량 검증 → Fatal Max)를 그대로
반영하는 최소 조합을 테스트 안에서 정의한다. Fatal Max 검사가 실제로 수행되었는지를
spy(call counter)로 관찰하여, 무효 수량 입력 시 수량 검증 오류가 먼저 반환되고 Fatal
Max 검사는 아예 수행되지 않음을 확인한다.

broker API, token, DB 함수를 호출하지 않으며, import 시 실제 side effect가 발생하지
않도록 config 환경변수를 import-only 더미 값으로 주입한 뒤 대상 모듈을 import한다.
"""

from __future__ import annotations

import ast
import os
from collections.abc import Callable
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


def _normalize_then_check_fatal_max(
    value: object,
    limit: int | None,
    fatal_check: Callable[[int, int | None], int],
) -> int:
    """설계가 명시한 순서를 그대로 반영하는 최소 조합.

    수량 검증(C1, `normalize_order_qty`)을 먼저 수행하고, 성공한 경우에만 Fatal Max
    검사(C2)를 수행한다. `fatal_check`는 실제 `check_fatal_max_order_qty`를 감싼 spy로
    주입되어, 수량 검증 실패 시 Fatal Max 검사가 호출되지 않았음을 관찰할 수 있게 한다.
    """
    qty = common.normalize_order_qty(value)
    return fatal_check(qty, limit)


def _make_fatal_max_spy():
    """`check_fatal_max_order_qty` 호출 횟수를 세는 spy를 만든다."""
    calls = {"count": 0}

    def spy(qty: int, limit: int | None) -> int:
        calls["count"] += 1
        return common.check_fatal_max_order_qty(qty, limit)

    return spy, calls


def _is_unparseable_or_non_positive_integer_string(text: str) -> bool:
    """문자열이 1 이상의 정수로 정규화될 수 없으면 True."""
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


# --- 무효 수량 생성기 (Property 2와 동일 계열) --------------------------------

_zero_values = st.sampled_from([0, Decimal(0), 0.0, "0", " 0 "])
_negative_integers = st.integers(max_value=-1)
_negative_integer_strings = _negative_integers.map(str)
_non_integer_decimals = st.decimals(
    allow_nan=False,
    allow_infinity=False,
    places=2,
    min_value=Decimal(-10000),
    max_value=Decimal(10000),
).filter(lambda d: d != d.to_integral_value())
_non_integer_floats = st.floats(
    allow_nan=False,
    allow_infinity=False,
    min_value=-10000.0,
    max_value=10000.0,
).filter(lambda f: not f.is_integer())
_non_integer_number_strings = _non_integer_decimals.map(str)
_booleans = st.booleans()
_non_numeric_strings = st.text(min_size=0, max_size=12).filter(
    _is_unparseable_or_non_positive_integer_string
)
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

# 일반 무효 수량 + 활성 Fatal Max 상한(양의 정수).
_general_case = st.tuples(
    _invalid_quantities,
    st.integers(min_value=1, max_value=10**6),
)


@st.composite
def _exceeding_case(draw):
    """상한을 초과하는 규모의 무효 수량과 활성 상한을 함께 생성한다.

    규모상 Fatal Max를 초과하더라도, 무효 수량이므로 수량 검증 오류가 먼저 반환되어야
    한다는 점을 명시적으로 검증하기 위한 케이스다.
    """
    limit = draw(st.integers(min_value=1, max_value=10**6))
    magnitude = draw(st.integers(min_value=limit + 1, max_value=limit + 10**6))
    value = draw(
        st.one_of(
            st.just(-magnitude),  # 규모 초과 음수 정수
            st.just(str(-magnitude)),  # 규모 초과 음수 정수 문자열
            st.just(Decimal(magnitude) + Decimal("0.5")),  # 규모 초과 소수
            st.just(float(magnitude) + 0.5),  # 규모 초과 float 소수
            st.just(True),  # bool (int 하위 타입이지만 무효)
        )
    )
    return value, limit


_value_and_limit = st.one_of(_general_case, _exceeding_case())


# Feature: connector-order-submission-guards, Property 5: 수량 검증은 Fatal_Max 검사보다 먼저 수행된다
# Validates: Requirements 3.13, 3.14
@settings(max_examples=200)
@given(case=_value_and_limit)
def test_quantity_validation_precedes_fatal_max_check(case) -> None:
    """무효 수량 입력에 대해(상한을 초과하는 규모 포함),

    수량 검증(C1) → Fatal Max 검사(C2) 순서로 조합했을 때, 항상
    `QuantityValidationError`(수량 검증 오류)가 먼저 발생하고 `FatalMaxExceededError`가
    아니어야 한다. 또한 수량 검증이 실패했으므로 Fatal Max 검사는 아예 수행되지 않아야
    한다(spy call_count == 0).
    """
    value, limit = case
    fatal_check, calls = _make_fatal_max_spy()

    with pytest.raises(common.QuantityValidationError):
        _normalize_then_check_fatal_max(value, limit, fatal_check)

    # 수량 검증이 먼저 실패했으므로 Fatal Max 검사는 수행되지 않았다.
    assert calls["count"] == 0
