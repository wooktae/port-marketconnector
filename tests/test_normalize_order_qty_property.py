"""Property test: valid quantities are value-preserving normalized (Property 1).

This test validates only the pure function `normalize_order_qty` at the broker
order submission boundary. It does not call broker APIs, token, or DB functions,
and to avoid real side effects on import, it injects import-only dummy values for
the config environment variables before importing the target module.
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


# Feature: connector-order-submission-guards, Property 1: valid quantities are value-preserving normalized
# Validates: Requirements 1.1, 3.1, 3.7, 3.8
@settings(max_examples=200)
@given(n=st.integers(min_value=1, max_value=10**12))
def test_valid_quantity_is_value_preserving_normalized(n: int) -> None:
    """For an integer n of 1 or greater, all three representations int, Decimal(n), str(n)

    must normalize to exactly the integer n without changing the value. Since the
    normalization result is the value passed through to the broker call, verify that
    all three representations return the same int n.
    """
    from_int = common.normalize_order_qty(n)
    from_decimal = common.normalize_order_qty(Decimal(n))
    from_str = common.normalize_order_qty(str(n))

    # Value preservation: all three representations return exactly the integer n.
    assert from_int == n
    assert from_decimal == n
    assert from_str == n

    # Type preservation: the return type is int and is not converted to float/Decimal.
    assert type(from_int) is int
    assert type(from_decimal) is int
    assert type(from_str) is int

    # The three input representations produce the same normalization result (representation-independent determinism).
    assert from_int == from_decimal == from_str
