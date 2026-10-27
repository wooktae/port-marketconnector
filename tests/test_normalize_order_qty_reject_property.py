"""Property test: invalid quantities are always rejected (Property 2).

This test validates only the pure function `normalize_order_qty` at the broker
order submission boundary. It does not call broker APIs, token, or DB functions,
and to avoid real side effects on import, it injects import-only dummy values for
the config environment variables before importing the target module.
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


def _is_unparseable_or_non_positive_integer_string(text: str) -> bool:
    """True if the string cannot be normalized to an integer of 1 or greater.

    Treats all of the following as invalid: Decimal parse failure (non-numeric),
    non-finite values, presence of a fractional part, and integers below 1.
    Ensures the test input is always a rejection target.
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


# Zero in several representations: int, Decimal, float, str
_zero_values = st.sampled_from([0, Decimal(0), 0.0, "0", " 0 "])

# Negative integers and their string representations
_negative_integers = st.integers(max_value=-1)
_negative_integer_strings = _negative_integers.map(str)

# Decimals with a fractional part (not integers)
_non_integer_decimals = st.decimals(
    allow_nan=False,
    allow_infinity=False,
    places=2,
    min_value=Decimal(-10000),
    max_value=Decimal(10000),
).filter(lambda d: d != d.to_integral_value())

# Floats with a fractional part (not integers)
_non_integer_floats = st.floats(
    allow_nan=False,
    allow_infinity=False,
    min_value=-10000.0,
    max_value=10000.0,
).filter(lambda f: not f.is_integer())

# Numeric strings with a fractional part
_non_integer_number_strings = _non_integer_decimals.map(str)

# bool values
_booleans = st.booleans()

# Strings that cannot be interpreted as numbers (also including zero/negative/fractional numeric strings)
_non_numeric_strings = st.text(min_size=0, max_size=12).filter(
    _is_unparseable_or_non_positive_integer_string
)

# Unsupported types that cannot be interpreted as numbers
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


# Feature: connector-order-submission-guards, Property 2: invalid quantities are always rejected
# Validates: Requirements 3.2, 3.3, 3.4, 3.5, 3.6
@settings(max_examples=200)
@given(value=_invalid_quantities)
def test_invalid_quantity_is_always_rejected(value: object) -> None:
    """For zero, negative, non-integer fractional, bool, non-numeric, and unsupported-type inputs,

    `normalize_order_qty` must raise `QuantityValidationError` without truncating or
    rounding, and must not return a normalized quantity. Confirm with pytest.raises
    that no value is ever returned and it always terminates only via the exception.
    """
    with pytest.raises(common.QuantityValidationError):
        common.normalize_order_qty(value)
