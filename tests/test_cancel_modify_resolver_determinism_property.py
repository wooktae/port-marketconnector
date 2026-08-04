"""Property test: Cancel/Modify resolver는 결정적이고 부작용이 없다 (Property 9).

이 테스트는 브로커 주문 제출 경계의 순수 함수 `resolve_cancel_modify_quantity`만
검증한다. 동일 입력에 대해 반복 호출해도 동일한 payload 또는 동일한 오류를
결정적으로 반환하는지, 그리고 호출 과정에서 broker API·token·DB 부작용이 전혀
발생하지 않는지 확인한다.

import 시 실제 side effect가 발생하지 않도록 config 환경변수를 import-only 더미
값으로 주입한 뒤 대상 모듈을 import한다. 각 예시에서는 broker 호출 진입점
(`requests`)과 token 함수, DB 연결 helper(`get_conn`)를 mock으로 대체하고, resolver
호출 이후 이들의 `call_count`가 각각 정확히 0인지 assert한다. resolver는 순수 함수이므로
이 진입점들은 어떤 입력에서도 호출되어서는 안 된다.
"""

from __future__ import annotations

import ast
import os
from decimal import Decimal
from pathlib import Path
from unittest import mock

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

# action_type 후보: 유효(CANCEL/MODIFY, 대소문자 무관)와 무효(그 외 문자열)를 함께 생성한다.
_action_types = st.one_of(
    st.sampled_from(["CANCEL", "MODIFY", "cancel", "modify", "CanCel", "Modify"]),
    st.text(max_size=8),
)

# 수량 후보: None(전량/생략), 유효 양의 정수, 무효(0·음수·bool·소수·비숫자 문자열·float)를
# 폭넓게 섞어 valid/invalid 입력 공간을 모두 덮는다.
_quantities = st.one_of(
    st.none(),
    st.integers(min_value=-5, max_value=10**6),
    st.booleans(),
    st.decimals(
        allow_nan=False,
        allow_infinity=False,
        places=2,
        min_value=Decimal(-1000),
        max_value=Decimal(1000),
    ),
    st.text(max_size=6),
    st.floats(allow_nan=False, allow_infinity=False, min_value=-1000, max_value=1000),
    # 정수와 정확히 동일한 문자열·Decimal 표현.
    st.integers(min_value=1, max_value=10**6).map(str),
    st.integers(min_value=1, max_value=10**6).map(Decimal),
)

# 활성 주문 수량 후보: 정상 양의 정수 위주이되 None과 무효 값도 포함한다.
_active_order_quantities = st.one_of(
    st.none(),
    st.integers(min_value=-5, max_value=10**6),
    st.booleans(),
    st.text(max_size=6),
)


def _outcome(action_type: object, qty: object, active_order_qty: object) -> object:
    """resolver 호출 결과를 결정성 비교용 값으로 변환한다.

    성공 시 `("ok", payload)`, 실패 시 `("err", 예외타입)`을 반환한다. payload와
    예외 타입은 모두 해시·동등 비교가 가능하므로 반복 호출 결과 비교에 쓸 수 있다.
    """
    try:
        return ("ok", common.resolve_cancel_modify_quantity(action_type, qty, active_order_qty))
    except Exception as exc:  # noqa: BLE001 - 결정성 비교를 위해 예외 타입만 캡처한다.
        return ("err", type(exc))


# Feature: connector-order-submission-guards, Property 9: Cancel/Modify resolver는 결정적이고 부작용이 없다
# Validates: Requirements 4.1, 4.9
@settings(max_examples=200)
@given(
    action_type=_action_types,
    qty=_quantities,
    active_order_qty=_active_order_quantities,
)
def test_cancel_modify_resolver_is_deterministic_and_side_effect_free(
    action_type: object,
    qty: object,
    active_order_qty: object,
) -> None:
    """동일 입력에 대해 resolver를 반복 호출해도 동일한 payload 또는 동일한 오류를

    결정적으로 반환하며, 호출 과정에서 broker API·token·DB 부작용이 전혀 발생하지
    않는다. valid/invalid 입력을 모두 생성하여 성공·오류 두 경로에서 결정성과
    무부작용을 함께 확인한다.
    """
    with (
        mock.patch.object(common, "requests") as mock_requests,
        mock.patch.object(common, "get_conn") as mock_get_conn,
        mock.patch.object(common, "check_and_refresh_token") as mock_check_token,
        mock.patch.object(common, "get_access_token") as mock_get_token,
    ):
        # 동일 입력으로 여러 번 호출해 결정성을 확인한다.
        outcomes = [_outcome(action_type, qty, active_order_qty) for _ in range(3)]

        # 결정성: 모든 호출이 동일한 payload 또는 동일한 오류 타입을 반환한다.
        first = outcomes[0]
        for other in outcomes[1:]:
            assert other == first

        # 오류가 발생한 경우 계약상 유일한 예외 계열이어야 한다.
        if first[0] == "err":
            assert issubclass(first[1], common.CancelModifyPayloadError)
        else:
            # 성공 payload는 (ord_qty, qty_all_ord_yn) 문자열 튜플이다.
            ord_qty, qty_all_ord_yn = first[1]
            assert isinstance(ord_qty, str)
            assert qty_all_ord_yn in ("Y", "N")

        # 무부작용: broker API·token·DB 진입점이 한 번도 호출되지 않는다.
        assert mock_requests.post.call_count == 0
        assert mock_requests.get.call_count == 0
        assert mock_get_conn.call_count == 0
        assert mock_check_token.call_count == 0
        assert mock_get_token.call_count == 0
