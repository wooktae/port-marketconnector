"""Property test: quantity validation is performed before the Fatal_Max check (Property 5).

This test validates only the execution order of the broker order submission boundary's
pure functions `normalize_order_qty` (quantity validation, C1) and
`check_fatal_max_order_qty` (Fatal Max check, C2).

`connector_order_common.py` does not yet have a compose helper that combines the two
functions, and wiring the enforcement is the responsibility of a follow-up task (the
strategy order `--execute` loop). Therefore, without modifying the production wiring,
this test defines within itself a minimal composition that reflects the order the design
specifies (quantity validation -> Fatal Max). By observing with a spy (call counter)
whether the Fatal Max check was actually performed, it confirms that on an invalid
quantity input the quantity validation error is returned first and the Fatal Max check is
not performed at all.

It does not call broker API, token, or DB functions, and to avoid real side effects on
import, it injects import-only dummy values for the config environment variables before
importing the target module.
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


def _normalize_then_check_fatal_max(
    value: object,
    limit: int | None,
    fatal_check: Callable[[int, int | None], int],
) -> int:
    """A minimal composition that reflects the order the design specifies.

    It performs quantity validation (C1, `normalize_order_qty`) first, and performs the
    Fatal Max check (C2) only on success. `fatal_check` is injected as a spy wrapping the
    real `check_fatal_max_order_qty`, so that on a quantity validation failure one can
    observe that the Fatal Max check was not called.
    """
    qty = common.normalize_order_qty(value)
    return fatal_check(qty, limit)


def _make_fatal_max_spy():
    """Create a spy that counts the number of `check_fatal_max_order_qty` calls."""
    calls = {"count": 0}

    def spy(qty: int, limit: int | None) -> int:
        calls["count"] += 1
        return common.check_fatal_max_order_qty(qty, limit)

    return spy, calls


def _is_unparseable_or_non_positive_integer_string(text: str) -> bool:
    """True if the string cannot be normalized to an integer of 1 or greater."""
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


# --- Invalid quantity generators (same family as Property 2) --------------------------------

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

# General invalid quantity + an active Fatal Max limit (positive integer).
_general_case = st.tuples(
    _invalid_quantities,
    st.integers(min_value=1, max_value=10**6),
)


@st.composite
def _exceeding_case(draw):
    """Generate an invalid quantity of a magnitude exceeding the limit together with an active limit.

    This case explicitly verifies that even if the magnitude exceeds Fatal Max, the
    quantity validation error must be returned first because it is an invalid quantity.
    """
    limit = draw(st.integers(min_value=1, max_value=10**6))
    magnitude = draw(st.integers(min_value=limit + 1, max_value=limit + 10**6))
    value = draw(
        st.one_of(
            st.just(-magnitude),  # negative integer exceeding the magnitude
            st.just(str(-magnitude)),  # negative integer string exceeding the magnitude
            st.just(Decimal(magnitude) + Decimal("0.5")),  # fractional exceeding the magnitude
            st.just(float(magnitude) + 0.5),  # float fractional exceeding the magnitude
            st.just(True),  # bool (a subtype of int but invalid)
        )
    )
    return value, limit


_value_and_limit = st.one_of(_general_case, _exceeding_case())


# Feature: connector-order-submission-guards, Property 5: quantity validation is performed before the Fatal_Max check
# Validates: Requirements 3.13, 3.14
@settings(max_examples=200)
@given(case=_value_and_limit)
def test_quantity_validation_precedes_fatal_max_check(case) -> None:
    """For an invalid quantity input (including magnitudes exceeding the limit),

    when composed in the order quantity validation (C1) -> Fatal Max check (C2), a
    `QuantityValidationError` (quantity validation error) must always occur first, and not
    a `FatalMaxExceededError`. Also, since quantity validation failed, the Fatal Max check
    must not be performed at all (spy call_count == 0).
    """
    value, limit = case
    fatal_check, calls = _make_fatal_max_spy()

    with pytest.raises(common.QuantityValidationError):
        _normalize_then_check_fatal_max(value, limit, fatal_check)

    # Since quantity validation failed first, the Fatal Max check was not performed.
    assert calls["count"] == 0
