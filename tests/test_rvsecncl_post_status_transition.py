"""Example test: cancel/modify success post-processing state transition (Requirement 5.7).

This test validates the post-processing state transition on the success path of
`submit_rvsecncl_order()`.

- Verify that `apply_rvsecncl_parent_status_after_success()` calls
  `update_order_request_status_if_not_terminal` with the expected (id, status) arguments
  to transition the active order from a non-Terminal state to `CANCELED` for CANCEL and
  to `MODIFIED` for MODIFY.
- Verify that the CANCEL_ACCEPTED transition on the success path of
  `submit_rvsecncl_order()` calls `update_order_request_status_if_not_terminal` with the
  `CANCEL_ACCEPTED` status for the cancel request row.

All checks are performed with the broker API (`requests.post`/`requests.get`), token
functions, and DB functions isolated by mocks. No real broker/DB/token calls occur, and
for the state transition decision a recording spy records only the arguments (no real
local DB access).
"""

from __future__ import annotations

import ast
import os
from pathlib import Path
from typing import Any


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

        # os.getenv("KEY") and os.environ.get("KEY")
        if isinstance(node, ast.Call):
            func = node.func

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


def _active_order() -> dict[str, Any]:
    return {
        "id": 77,
        "account_id": 1,
        "account_no": "00000000",
        "ticker_code": "000000",
        "stock_name": "TEST",
        "request_type": "BUY",
        "order_method": "MARKET",
        "order_price": None,
        "order_qty": 39,
        "parent_order_request_id": None,
        "strategy_name": "strategy_ai",
        "strategy_version": "test",
        "strategy_run_id": "test-run",
        "strategy_signal_id": None,
        "signal_date": "2026-08-03",
        "signal_type": "BUY",
        "signal_score": None,
        "signal_position_size": None,
        "request_status": "ACCEPTED",
        "broker_order_no": "0000000001",
        "broker_branch_code": "00000",
    }


def _install_status_transition_spy(monkeypatch, recorded: list[tuple[Any, ...]]) -> None:
    """Replace `update_order_request_status_if_not_terminal` with an argument-recording spy.

    Does not access the real local DB and records only the (order_request_id,
    request_status) arguments. Returns True, meaning the update succeeded.
    """

    def _spy(order_request_id, request_status, message=None):
        recorded.append((order_request_id, request_status))
        return True

    monkeypatch.setattr(
        common,
        "update_order_request_status_if_not_terminal",
        _spy,
    )


def test_apply_parent_status_cancel_drives_active_order_to_canceled(
    monkeypatch,
) -> None:
    recorded: list[tuple[Any, ...]] = []
    _install_status_transition_spy(monkeypatch, recorded)

    common.apply_rvsecncl_parent_status_after_success(
        action_type="CANCEL",
        active_order_id=77,
        root_original_id=77,
    )

    # CANCEL post-processing attempts to transition the active order (77) from non-Terminal to CANCELED.
    assert recorded == [(77, "CANCELED")]


def test_apply_parent_status_modify_drives_active_order_to_modified(
    monkeypatch,
) -> None:
    recorded: list[tuple[Any, ...]] = []
    _install_status_transition_spy(monkeypatch, recorded)

    common.apply_rvsecncl_parent_status_after_success(
        action_type="MODIFY",
        active_order_id=77,
        root_original_id=77,
    )

    # MODIFY post-processing attempts to transition the active order (77) from non-Terminal to MODIFIED.
    assert recorded == [(77, "MODIFIED")]


def _install_submit_mocks(
    monkeypatch,
    captured: dict[str, Any],
    recorded: list[tuple[Any, ...]],
) -> None:
    """Mock isolation to run `submit_rvsecncl_order()` without broker/DB/token.

    The state transition decision is replaced with a spy that records only the arguments
    into the recorded list.
    """
    active = _active_order()

    monkeypatch.setattr(
        common,
        "load_order_request_for_action",
        lambda order_request_id: dict(active),
    )
    monkeypatch.setattr(
        common,
        "resolve_active_order_for_action",
        lambda order_request_id: dict(active),
    )
    monkeypatch.setattr(
        common,
        "ensure_connector_account",
        lambda **kwargs: 1,
    )
    monkeypatch.setattr(
        common,
        "insert_order_request",
        lambda record: 9001,
    )
    monkeypatch.setattr(
        common,
        "_insert_signal_map_if_needed",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        common,
        "update_order_request_status_only",
        lambda *args, **kwargs: None,
    )
    # Replace the conditional UPDATE used by the CANCEL_ACCEPTED transition and post-processing with an argument-recording spy.
    _install_status_transition_spy(monkeypatch, recorded)
    monkeypatch.setattr(
        common,
        "apply_rvsecncl_parent_status_after_success",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        common,
        "_finalize_order_request_from_response",
        lambda **kwargs: (True, "0000000002", "00000"),
    )

    captured["request_api_call_count"] = 0

    def fake_request_api(**kwargs):
        captured["request_api_call_count"] += 1
        captured["request_payload"] = kwargs["request_payload"]

        return (
            object(),
            {
                "rt_cd": "0",
                "msg_cd": "0",
                "msg1": "SUCCESS",
                "output": {
                    "ODNO": "0000000002",
                    "KRX_FWDG_ORD_ORGNO": "00000",
                },
            },
            1,
        )

    monkeypatch.setattr(
        common,
        "_request_api",
        fake_request_api,
    )

    # Fail-fast guard that immediately fails if the real broker/token boundary is entered.
    def _fail_real_broker_call(*args, **kwargs):
        raise AssertionError("real requests.post must not be called during validation")

    def _fail_real_token_call(*args, **kwargs):
        raise AssertionError("real token function must not be called during validation")

    monkeypatch.setattr(common.requests, "post", _fail_real_broker_call)
    monkeypatch.setattr(common.requests, "get", _fail_real_broker_call)
    monkeypatch.setattr(common, "get_access_token", _fail_real_token_call)
    monkeypatch.setattr(common, "check_and_refresh_token", _fail_real_token_call)


def test_submit_cancel_success_marks_cancel_request_as_cancel_accepted(
    monkeypatch,
) -> None:
    captured: dict[str, Any] = {}
    recorded: list[tuple[Any, ...]] = []
    _install_submit_mocks(monkeypatch, captured, recorded)

    common.submit_rvsecncl_order(
        action_type="CANCEL",
        api_name="order-cancel",
        tr_id="TEST_TR_ID",
        rvse_cncl_dvsn_cd="02",
        original_order_request_id=77,
        qty=None,
    )

    # Transition the cancel request row (order_request_id=9001) from non-Terminal to CANCEL_ACCEPTED.
    assert (9001, "CANCEL_ACCEPTED") in recorded
    # The mock broker entry point is called exactly once, and the real broker/token boundary is not called.
    assert captured["request_api_call_count"] == 1
