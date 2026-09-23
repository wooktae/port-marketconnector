"""Property test: the Cancel/Modify resolver builds the payload quantity per the contract (Property 7).

This test validates only the pure function `resolve_cancel_modify_quantity` at the
broker order submission boundary. This function computes the quantity branch of a
CANCEL/MODIFY request and is a pure function that does not call broker API, token, or
DB functions.

Contract under validation (valid input -> payload construction):
    - CANCEL, qty omitted (None) -> `("0", "Y")` (full cancel).
    - CANCEL, integer from 1 up to active_order_qty -> `(str(qty), "N")` (partial cancel).
    - MODIFY, positive integer of 1 or greater -> `(str(qty), "N")`.
    - MODIFY, qty omitted (None) -> `(str(active_order_qty), "N")`.

It does not call broker API, token, or DB functions, and to avoid real side effects on
import, it injects import-only dummy values for the config environment variables before
importing the target module.
"""

from __future__ import annotations

import ast
import os
from decimal import Decimal
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st


def _install_import_only_environment() -> None:
    """Fill only with dummy values the environment variables that config.py requires on import.

    Does not read or record real KIS key/account values, and does not trigger
    broker/DB/token side effects. Keys that are already set are not overwritten.
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
    """Strategy that randomly selects an equivalent representation (int, Decimal, str) of an integer n of 1 or greater.

    The resolver, with the same semantics as quantity validation (C1), accepts Decimals
    and strings exactly equal to an integer, so it must build the same payload regardless
    of representation.
    """
    return st.sampled_from([n, Decimal(n), str(n)])


# Feature: connector-order-submission-guards, Property 7: the Cancel/Modify resolver builds the payload quantity per the contract
# Validates: Requirements 4.2, 4.3, 4.6, 4.7
@settings(max_examples=200)
@given(active_qty=st.integers(min_value=1, max_value=10**9))
def test_cancel_full_omitted_quantity_builds_all_order_payload(active_qty: int) -> None:
    """CANCEL & qty omitted (None) -> full cancel payload `("0", "Y")`.

    A full cancel is always `ORD_QTY="0"`, `QTY_ALL_ORD_YN="Y"` regardless of the active order quantity.
    """
    ord_qty, qty_all_ord_yn = common.resolve_cancel_modify_quantity(
        "CANCEL", None, active_qty
    )
    assert ord_qty == "0"
    assert qty_all_ord_yn == "Y"


# Feature: connector-order-submission-guards, Property 7: the Cancel/Modify resolver builds the payload quantity per the contract
# Validates: Requirements 4.2, 4.3, 4.6, 4.7
@settings(max_examples=200)
@given(data=st.data())
def test_cancel_partial_quantity_builds_partial_payload(data: st.DataObject) -> None:
    """CANCEL & an integer with 1 <= qty <= active -> partial cancel payload `(str(qty), "N")`.

    The int, Decimal, and str representations must all be value-preserving and build the same string quantity.
    """
    active_qty = data.draw(st.integers(min_value=1, max_value=10**9))
    qty = data.draw(st.integers(min_value=1, max_value=active_qty))
    value = data.draw(_int_representations(qty))

    ord_qty, qty_all_ord_yn = common.resolve_cancel_modify_quantity(
        "CANCEL", value, active_qty
    )
    assert ord_qty == str(qty)
    assert qty_all_ord_yn == "N"


# Feature: connector-order-submission-guards, Property 7: the Cancel/Modify resolver builds the payload quantity per the contract
# Validates: Requirements 4.2, 4.3, 4.6, 4.7
@settings(max_examples=200)
@given(data=st.data())
def test_modify_positive_quantity_builds_modify_payload(data: st.DataObject) -> None:
    """MODIFY & a positive integer qty of 1 or greater -> modify payload `(str(qty), "N")`.

    Uses the specified modify quantity as-is, regardless of active_order_qty.
    """
    qty = data.draw(st.integers(min_value=1, max_value=10**9))
    active_qty = data.draw(st.integers(min_value=1, max_value=10**9))
    value = data.draw(_int_representations(qty))

    ord_qty, qty_all_ord_yn = common.resolve_cancel_modify_quantity(
        "MODIFY", value, active_qty
    )
    assert ord_qty == str(qty)
    assert qty_all_ord_yn == "N"


# Feature: connector-order-submission-guards, Property 7: the Cancel/Modify resolver builds the payload quantity per the contract
# Validates: Requirements 4.2, 4.3, 4.6, 4.7
@settings(max_examples=200)
@given(active_qty=st.integers(min_value=1, max_value=10**9))
def test_modify_omitted_quantity_uses_active_order_quantity(active_qty: int) -> None:
    """MODIFY & qty omitted (None) -> use the active order quantity `(str(active), "N")`."""
    ord_qty, qty_all_ord_yn = common.resolve_cancel_modify_quantity(
        "MODIFY", None, active_qty
    )
    assert ord_qty == str(active_qty)
    assert qty_all_ord_yn == "N"
