"""Property test: the Cancel/Modify resolver is deterministic and side-effect free (Property 9).

This test validates only the pure function `resolve_cancel_modify_quantity` at the
broker order submission boundary. It verifies that repeated calls with the same input
deterministically return the same payload or the same error, and that no broker API,
token, or DB side effects occur during the call.

To avoid real side effects on import, it injects import-only dummy values for the
config environment variables before importing the target module. In each example it
replaces the broker call entry point (`requests`), the token functions, and the DB
connection helper (`get_conn`) with mocks, and after the resolver call asserts that
each of their `call_count` values is exactly 0. Because the resolver is a pure
function, these entry points must not be called for any input.
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

# action_type candidates: generate both valid (CANCEL/MODIFY, case-insensitive) and invalid (other strings).
_action_types = st.one_of(
    st.sampled_from(["CANCEL", "MODIFY", "cancel", "modify", "CanCel", "Modify"]),
    st.text(max_size=8),
)

# Quantity candidates: broadly mix None (full/omitted), valid positive integers, and invalid
# values (zero, negative, bool, fractional, non-numeric strings, float) to cover the entire valid/invalid input space.
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
    # String/Decimal representations exactly equal to an integer.
    st.integers(min_value=1, max_value=10**6).map(str),
    st.integers(min_value=1, max_value=10**6).map(Decimal),
)

# Active order quantity candidates: mostly normal positive integers, but also include None and invalid values.
_active_order_quantities = st.one_of(
    st.none(),
    st.integers(min_value=-5, max_value=10**6),
    st.booleans(),
    st.text(max_size=6),
)


def _outcome(action_type: object, qty: object, active_order_qty: object) -> object:
    """Convert the resolver call result into a value for determinism comparison.

    Returns `("ok", payload)` on success and `("err", exception_type)` on failure. Both
    the payload and the exception type support hashing and equality comparison, so they
    can be used to compare results across repeated calls.
    """
    try:
        return ("ok", common.resolve_cancel_modify_quantity(action_type, qty, active_order_qty))
    except Exception as exc:  # noqa: BLE001 - capture only the exception type for determinism comparison.
        return ("err", type(exc))


# Feature: connector-order-submission-guards, Property 9: the Cancel/Modify resolver is deterministic and side-effect free
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
    """Repeated calls to the resolver with the same input deterministically return the same payload

    or the same error, and no broker API, token, or DB side effects occur during the
    call. Generate both valid and invalid inputs to confirm determinism and freedom from
    side effects across both the success and error paths.
    """
    with (
        mock.patch.object(common, "requests") as mock_requests,
        mock.patch.object(common, "get_conn") as mock_get_conn,
        mock.patch.object(common, "check_and_refresh_token") as mock_check_token,
        mock.patch.object(common, "get_access_token") as mock_get_token,
    ):
        # Call multiple times with the same input to check determinism.
        outcomes = [_outcome(action_type, qty, active_order_qty) for _ in range(3)]

        # Determinism: all calls return the same payload or the same error type.
        first = outcomes[0]
        for other in outcomes[1:]:
            assert other == first

        # When an error occurs, it must be the single contractual exception family.
        if first[0] == "err":
            assert issubclass(first[1], common.CancelModifyPayloadError)
        else:
            # A successful payload is an (ord_qty, qty_all_ord_yn) string tuple.
            ord_qty, qty_all_ord_yn = first[1]
            assert isinstance(ord_qty, str)
            assert qty_all_ord_yn in ("Y", "N")

        # Freedom from side effects: the broker API, token, and DB entry points are never called.
        assert mock_requests.post.call_count == 0
        assert mock_requests.get.call_count == 0
        assert mock_get_conn.call_count == 0
        assert mock_check_token.call_count == 0
        assert mock_get_token.call_count == 0
