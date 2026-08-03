from __future__ import annotations

import ast
import os
from pathlib import Path
from typing import Any


def _install_import_only_environment() -> None:
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

        # os.getenv("KEY")
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


def _install_common_mocks(
    monkeypatch,
    captured: dict[str, Any],
) -> None:
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

    def fake_request_api(**kwargs):
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


def test_full_cancel_uses_zero_quantity_and_all_order_flag(
    monkeypatch,
) -> None:
    captured: dict[str, Any] = {}
    _install_common_mocks(monkeypatch, captured)

    common.submit_rvsecncl_order(
        action_type="CANCEL",
        api_name="order-cancel",
        tr_id="TEST_TR_ID",
        rvse_cncl_dvsn_cd="02",
        original_order_request_id=77,
        qty=None,
    )

    payload = captured["request_payload"]

    assert payload["ORD_QTY"] == "0"
    assert payload["QTY_ALL_ORD_YN"] == "Y"
    assert payload["RVSE_CNCL_DVSN_CD"] == "02"
    assert payload["ORD_UNPR"] == "0"


def test_partial_cancel_uses_requested_quantity(
    monkeypatch,
) -> None:
    captured: dict[str, Any] = {}
    _install_common_mocks(monkeypatch, captured)

    common.submit_rvsecncl_order(
        action_type="CANCEL",
        api_name="order-cancel",
        tr_id="TEST_TR_ID",
        rvse_cncl_dvsn_cd="02",
        original_order_request_id=77,
        qty=10,
    )

    payload = captured["request_payload"]

    assert payload["ORD_QTY"] == "10"
    assert payload["QTY_ALL_ORD_YN"] == "N"