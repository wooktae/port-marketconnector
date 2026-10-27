"""Property test: Fatal_Max configuration value resolution branches deterministically (Property 3).

This test validates only the pure function `resolve_fatal_max_order_qty` at the broker
order submission boundary. It does not call broker API, token, or DB functions, and to
avoid real side effects on import, it injects import-only dummy values for the config
environment variables before importing the target module.

For each example it sets/deletes only the `STEP12_FATAL_MAX_ORDER_QTY` environment
variable and, at the end of the example, restores the original value as-is to ensure
isolation between examples.
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

# A "missing" marker used to restore the original state after an example ends.
_MISSING = object()

# A marker representing the case where the environment variable is not set at all.
_UNSET = object()

# Expected result markers.
_EXPECT_NONE = ("none",)
_EXPECT_ERROR = ("error",)


def _is_non_numeric(text: str) -> bool:
    """True if the string cannot be interpreted as a number and thus triggers an explicit configuration error.

    An empty string after strip is disabled (None), same as unset, so it is excluded
    from the error cases. Strings that successfully parse as Decimal are also excluded so
    that this generator always produces only non-numeric errors.
    """
    # An environment variable value cannot contain a null byte, so exclude it from the error cases.
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


# Raw value cases that trigger a disabled check (None).
_disabled_cases = st.one_of(
    # unset
    st.just((_UNSET, _EXPECT_NONE)),
    # empty strings / whitespace strings
    st.sampled_from(["", " ", "   ", "\t", "\n", " \t "]).map(
        lambda s: (s, _EXPECT_NONE)
    ),
    # 0 (leading/trailing whitespace allowed)
    st.sampled_from(["0", " 0", "0 ", " 0 ", "00", " 00 "]).map(
        lambda s: (s, _EXPECT_NONE)
    ),
)

# Cases that return a positive integer limit. Leading/trailing whitespace must give the same result after strip.
_positive_cases = st.tuples(
    st.integers(min_value=1, max_value=10**12),
    st.sampled_from(["{}", " {}", "{} ", " {} "]),
).map(lambda t: (t[1].format(t[0]), ("limit", t[0])))

# Negative integer strings: explicit configuration error.
_negative_int_cases = st.integers(max_value=-1).map(lambda n: (str(n), _EXPECT_ERROR))

# Decimal strings with a fractional part: explicit configuration error.
_non_integer_decimals = st.decimals(
    allow_nan=False,
    allow_infinity=False,
    places=2,
    min_value=Decimal(-10000),
    max_value=Decimal(10000),
).filter(lambda d: d != d.to_integral_value())
_decimal_cases = _non_integer_decimals.map(lambda d: (str(d), _EXPECT_ERROR))

# Strings that cannot be interpreted as numbers: explicit configuration error.
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


# Feature: connector-order-submission-guards, Property 3: Fatal_Max configuration value resolution branches deterministically
# Validates: Requirements 3.10, 3.12
@settings(max_examples=200)
@given(case=_fatal_max_cases)
def test_fatal_max_resolution_is_deterministically_branched(case: object) -> None:
    """The `STEP12_FATAL_MAX_ORDER_QTY` string branches deterministically into three paths.

    If unset, empty, or `"0"`, the check is disabled (`None`); if a positive integer
    string, it returns that integer limit; and if a negative, fractional, or non-numeric
    string, it raises `FatalMaxConfigError` (it does not silently ignore or disable). Each
    example sets/deletes the environment variable and then restores the original value to
    keep isolation between examples.
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
            # ("limit", n): return the positive integer limit unchanged.
            _, limit = expected
            result = common.resolve_fatal_max_order_qty()
            assert result == limit
            assert type(result) is int
    finally:
        if original is _MISSING:
            os.environ.pop(env_key, None)
        else:
            os.environ[env_key] = original
