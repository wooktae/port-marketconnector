"""Property test: Cancel/Modify resolver는 계약대로 payload 수량을 구성한다 (Property 7).

이 테스트는 브로커 주문 제출 경계의 순수 함수 `resolve_cancel_modify_quantity`만
검증한다. 이 함수는 CANCEL/MODIFY 요청의 수량 분기를 계산하며, broker API·token·DB
함수를 호출하지 않는 순수 함수다.

검증 대상 계약(유효 입력 → payload 구성):
    - CANCEL, qty 생략(None) → `("0", "Y")`(전량 취소).
    - CANCEL, 1 이상 active_order_qty 이하 정수 → `(str(qty), "N")`(부분 취소).
    - MODIFY, 1 이상 양의 정수 → `(str(qty), "N")`.
    - MODIFY, qty 생략(None) → `(str(active_order_qty), "N")`.

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


def _int_representations(n: int):
    """1 이상 정수 n의 동등 표현(int, Decimal, str)을 무작위로 선택하는 전략.

    resolver는 수량 검증(C1)과 동일한 의미로 정수와 정확히 같은 Decimal·문자열을
    허용하므로, 표현이 달라도 동일한 payload를 구성해야 한다.
    """
    return st.sampled_from([n, Decimal(n), str(n)])


# Feature: connector-order-submission-guards, Property 7: Cancel/Modify resolver는 계약대로 payload 수량을 구성한다
# Validates: Requirements 4.2, 4.3, 4.6, 4.7
@settings(max_examples=200)
@given(active_qty=st.integers(min_value=1, max_value=10**9))
def test_cancel_full_omitted_quantity_builds_all_order_payload(active_qty: int) -> None:
    """CANCEL & qty 생략(None) → 전량 취소 payload `("0", "Y")`.

    전량 취소는 활성 주문 수량과 무관하게 항상 `ORD_QTY="0"`, `QTY_ALL_ORD_YN="Y"`다.
    """
    ord_qty, qty_all_ord_yn = common.resolve_cancel_modify_quantity(
        "CANCEL", None, active_qty
    )
    assert ord_qty == "0"
    assert qty_all_ord_yn == "Y"


# Feature: connector-order-submission-guards, Property 7: Cancel/Modify resolver는 계약대로 payload 수량을 구성한다
# Validates: Requirements 4.2, 4.3, 4.6, 4.7
@settings(max_examples=200)
@given(data=st.data())
def test_cancel_partial_quantity_builds_partial_payload(data: st.DataObject) -> None:
    """CANCEL & 1 <= qty <= active인 정수 → 부분 취소 payload `(str(qty), "N")`.

    int, Decimal, str 표현 모두 값 보존되어 동일한 문자열 수량을 구성해야 한다.
    """
    active_qty = data.draw(st.integers(min_value=1, max_value=10**9))
    qty = data.draw(st.integers(min_value=1, max_value=active_qty))
    value = data.draw(_int_representations(qty))

    ord_qty, qty_all_ord_yn = common.resolve_cancel_modify_quantity(
        "CANCEL", value, active_qty
    )
    assert ord_qty == str(qty)
    assert qty_all_ord_yn == "N"


# Feature: connector-order-submission-guards, Property 7: Cancel/Modify resolver는 계약대로 payload 수량을 구성한다
# Validates: Requirements 4.2, 4.3, 4.6, 4.7
@settings(max_examples=200)
@given(data=st.data())
def test_modify_positive_quantity_builds_modify_payload(data: st.DataObject) -> None:
    """MODIFY & 1 이상 양의 정수 qty → 정정 payload `(str(qty), "N")`.

    active_order_qty와 무관하게 지정한 정정 수량을 그대로 사용한다.
    """
    qty = data.draw(st.integers(min_value=1, max_value=10**9))
    active_qty = data.draw(st.integers(min_value=1, max_value=10**9))
    value = data.draw(_int_representations(qty))

    ord_qty, qty_all_ord_yn = common.resolve_cancel_modify_quantity(
        "MODIFY", value, active_qty
    )
    assert ord_qty == str(qty)
    assert qty_all_ord_yn == "N"


# Feature: connector-order-submission-guards, Property 7: Cancel/Modify resolver는 계약대로 payload 수량을 구성한다
# Validates: Requirements 4.2, 4.3, 4.6, 4.7
@settings(max_examples=200)
@given(active_qty=st.integers(min_value=1, max_value=10**9))
def test_modify_omitted_quantity_uses_active_order_quantity(active_qty: int) -> None:
    """MODIFY & qty 생략(None) → 활성 주문 수량 사용 `(str(active), "N")`."""
    ord_qty, qty_all_ord_yn = common.resolve_cancel_modify_quantity(
        "MODIFY", None, active_qty
    )
    assert ord_qty == str(active_qty)
    assert qty_all_ord_yn == "N"
