"""Property test: the Fatal_Max check does not adjust the quantity (Property 4).

This test validates only the pure function `check_fatal_max_order_qty` at the broker
order submission boundary. It does not call broker APIs, token, or DB functions,
and to avoid real side effects on import, it injects import-only dummy values for
the config environment variables before importing the target module.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest
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


# Feature: connector-order-submission-guards, Property 4: the Fatal_Max check does not adjust the quantity
# Validates: Requirements 3.11
@settings(max_examples=200)
@given(data=st.data())
def test_fatal_max_check_never_adjusts_quantity(data: st.DataObject) -> None:
    """For a positive limit c and a normalized quantity q, if q <= c it passes and returns q

    unchanged, and if q > c it raises `FatalMaxExceededError`. In no case does it
    shrink or adjust q down to the limit. If the limit is `None`, the check is disabled,
    so q is returned unchanged.
    """
    limit = data.draw(st.integers(min_value=1, max_value=10**12))

    # Generate q so that it covers both the q <= c and q > c regions.
    qty = data.draw(st.integers(min_value=1, max_value=10**12 + 10**6))

    if qty <= limit:
        # Pass path: return q unchanged (not adjusted to the limit).
        result = common.check_fatal_max_order_qty(qty, limit)
        assert result == qty
        assert type(result) is int
    else:
        # Exceed path: raise the exception and do not return a value shrunk to the limit.
        with pytest.raises(common.FatalMaxExceededError):
            common.check_fatal_max_order_qty(qty, limit)


# Feature: connector-order-submission-guards, Property 4: the Fatal_Max check does not adjust the quantity
# Validates: Requirements 3.11
@settings(max_examples=200)
@given(qty=st.integers(min_value=1, max_value=10**12))
def test_fatal_max_check_disabled_passes_through(qty: int) -> None:
    """If the limit is `None` (check disabled), any positive quantity q is returned unchanged."""
    result = common.check_fatal_max_order_qty(qty, None)
    assert result == qty
    assert type(result) is int
