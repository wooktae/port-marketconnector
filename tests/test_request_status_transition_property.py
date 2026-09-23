"""Property test: local order request status never reverts from Terminal (Property 10).

This test validates only the pure function `resolve_request_status_transition` on the
`submit_rvsecncl_order()` path. It does not call broker API, token, or DB functions, and
to avoid real side effects on import, it injects import-only dummy values for the config
environment variables before importing the target module.
"""

from __future__ import annotations

import ast
import os
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

# A status set including both Terminal statuses and representative non-Terminal statuses.
_TERMINAL_STATUSES = ("FILLED", "CANCELED", "REJECTED", "FAILED")
_NON_TERMINAL_STATUSES = ("PENDING", "ACCEPTED", "CANCEL_ACCEPTED", "MODIFIED")
_ALL_STATUSES = _TERMINAL_STATUSES + _NON_TERMINAL_STATUSES

# requested_status is always a str, and current_status may include None.
_requested_status = st.sampled_from(_ALL_STATUSES)
_current_status = st.one_of(st.none(), st.sampled_from(_ALL_STATUSES))


# Feature: connector-order-submission-guards, Property 10: local order request status never reverts from Terminal
# Validates: Requirements 5.1, 5.2, 5.3, 5.4, 5.5, 5.6
@settings(max_examples=200)
@given(current_status=_current_status, requested_status=_requested_status)
def test_request_status_transition_never_reverts_terminal(
    current_status: str | None,
    requested_status: str,
) -> None:
    """Validate the monotonicity contract that never reverts from a Terminal state.

    - If the current state is non-Terminal (or None), the update is allowed and the result is the requested status.
    - If the current state is Terminal and the requested status equals the current one, it is allowed without error (no-op)
      and the result keeps that Terminal state.
    - If the current state is Terminal and the requested status differs, the update is blocked (not a success) and the result
      keeps the current Terminal state unchanged.
    """
    result = common.resolve_request_status_transition(current_status, requested_status)

    if current_status in _TERMINAL_STATUSES:
        if requested_status == current_status:
            # Reapplying the same Terminal: allowed (no-op), result is that Terminal state.
            assert result.allowed is True
            assert result.result_status == current_status
        else:
            # Block Terminal reversal: not a success, keep the current Terminal state as-is.
            assert result.allowed is False
            assert result.result_status == current_status
    else:
        # Non-Terminal (or None): update allowed, result is the requested status.
        assert result.allowed is True
        assert result.result_status == requested_status
