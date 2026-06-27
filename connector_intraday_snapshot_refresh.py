"""Intraday-safe KIS balance/position snapshot refresh.

Purpose:
  - Refresh connector_balance_snapshot during market hours.
  - Refresh connector_position_snapshot only when KIS output1 contains positions.
  - If KIS output1 is empty:
      * Treat as normal only when execution.strategy_position_state has no OPEN positions.
      * Treat as mismatch/blocker when OPEN strategy positions still exist.
  - No strategy judgment.
  - No order creation.
  - No broker order submission.

This entrypoint is intentionally separate from connector_balance.py because
Daily Step1 and intraday monitoring have different safety requirements.
"""

import argparse
import json
import sys
import time
from datetime import datetime

import requests

from config import APP_KEY, APP_SECRET, BASE_URL, PAPER_ACNT, ACNT_PRDT_CD
from connector_db import (
    delete_connector_position_snapshots,
    ensure_connector_account,
    get_conn,
    insert_api_call_log,
    to_jsonb,
    upsert_connector_balance_snapshot,
    upsert_connector_position_snapshots,
)
from token_manager import check_and_refresh_token, get_access_token


SOURCE_VERSION = "connector-intraday-snapshot-refresh-1.0.0"
API_NAME = "inquire-balance"
TR_ID = "VTTC8434R"
ENDPOINT = "/uapi/domestic-stock/v1/trading/inquire-balance"


EXIT_OK = 0
EXIT_API_ERROR = 20
EXIT_OPEN_POSITION_MISMATCH = 30
EXIT_VALIDATION_ERROR = 40


def _to_float(value, default=0.0):
    try:
        if value in (None, "", " "):
            return default
        return float(str(value).replace(",", ""))
    except Exception:
        return default


def _to_int(value, default=0):
    try:
        if value in (None, "", " "):
            return default
        return int(float(str(value).replace(",", "")))
    except Exception:
        return default


def _json_dumps(value):
    return json.dumps(value, ensure_ascii=False, default=str)


def _count_open_strategy_positions(account_no: str) -> int:
    sql = """
        SELECT count(*)
          FROM execution.strategy_position_state
         WHERE position_status = 'OPEN'
           AND account_no = %s
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (account_no,))
            row = cur.fetchone()
    return int(row[0] or 0)


def _fetch_open_strategy_positions(account_no: str, limit: int = 20):
    sql = """
        SELECT
            id,
            account_no,
            ticker_code,
            stock_name,
            entry_date,
            entry_price,
            remaining_qty,
            position_status,
            updated_at
          FROM execution.strategy_position_state
         WHERE position_status = 'OPEN'
           AND account_no = %s
         ORDER BY id
         LIMIT %s
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (account_no, limit))
            rows = cur.fetchall()

    result = []
    for row in rows:
        result.append(
            {
                "id": row[0],
                "account_no": row[1],
                "ticker_code": row[2],
                "stock_name": row[3],
                "entry_date": row[4].isoformat() if row[4] else None,
                "entry_price": str(row[5]) if row[5] is not None else None,
                "remaining_qty": row[6],
                "position_status": row[7],
                "updated_at": row[8].isoformat() if row[8] else None,
            }
        )
    return result


def _request_balance(account_id: int, token: str):
    url = f"{BASE_URL}{ENDPOINT}"
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Authorization": f"Bearer {token}",
        "appKey": APP_KEY,
        "appSecret": APP_SECRET,
        "tr_id": TR_ID,
        "custtype": "P",
    }
    params = {
        "CANO": PAPER_ACNT,
        "ACNT_PRDT_CD": ACNT_PRDT_CD,
        "AFHR_FLPR_YN": "N",
        "OFL_YN": "",
        "INQR_DVSN": "02",
        "UNPR_DVSN": "01",
        "FUND_STTL_ICLD_YN": "N",
        "FNCG_AMT_AUTO_RDPT_YN": "N",
        "PRCS_DVSN": "00",
        "CTX_AREA_FK100": "",
        "CTX_AREA_NK100": "",
    }

    started = time.time()
    try:
        response = requests.get(url, headers=headers, params=params, timeout=20)
        latency_ms = int((time.time() - started) * 1000)
        return response, params, latency_ms
    except Exception as exc:
        insert_api_call_log(
            account_id=account_id,
            api_category="BALANCE",
            api_name=API_NAME,
            http_method="GET",
            endpoint=ENDPOINT,
            tr_id=TR_ID,
            request_params=params,
            response_message=str(exc),
            is_success=False,
        )
        print(f"[ERROR] balance request exception: {exc}")
        return None, params, None


def _load_balance_json(account_id: int):
    token = get_access_token()
    if not token:
        print("[ERROR] missing KIS access token")
        return None, None, None

    response, params, latency_ms = _request_balance(account_id, token)
    if response is None:
        return None, params, latency_ms

    new_token = check_and_refresh_token(response.text)
    if new_token:
        print("[INFO] token refresh triggered. retrying balance request")
        response, params, latency_ms = _request_balance(account_id, new_token)
        if response is None:
            return None, params, latency_ms

    try:
        data = response.json()
    except Exception as exc:
        insert_api_call_log(
            account_id=account_id,
            api_category="BALANCE",
            api_name=API_NAME,
            http_method="GET",
            endpoint=ENDPOINT,
            tr_id=TR_ID,
            request_params=params,
            response_status=response.status_code,
            response_message=f"JSON parse failed: {exc}",
            response_body=None,
            is_success=False,
            latency_ms=latency_ms,
        )
        print(f"[ERROR] JSON parse failed: {exc}")
        return None, params, latency_ms

    insert_api_call_log(
        account_id=account_id,
        api_category="BALANCE",
        api_name=API_NAME,
        http_method="GET",
        endpoint=ENDPOINT,
        tr_id=TR_ID,
        request_params=params,
        response_status=response.status_code,
        response_code=data.get("rt_cd"),
        response_message=data.get("msg1"),
        response_body=data,
        is_success=(data.get("rt_cd") == "0"),
        latency_ms=latency_ms,
    )

    return data, params, latency_ms


def _parse_as_of_date(summary: dict):
    ord_dt = summary.get("ord_dt", "")
    if ord_dt:
        try:
            return datetime.strptime(ord_dt, "%Y%m%d").date()
        except Exception:
            pass
    return datetime.now().date()


def _build_balance_record(account_id: int, summary: dict, data: dict, as_of_date, as_of_ts):
    return {
        "account_id": account_id,
        "account_no": PAPER_ACNT,
        "as_of_date": as_of_date,
        "as_of_ts": as_of_ts,
        "cash_balance": _to_float(summary.get("dnca_tot_amt", 0)),
        "withdrawable_cash": _to_float(summary.get("dnca_tot_amt", 0)),
        "orderable_cash": _to_float(summary.get("dnca_tot_amt", 0)),
        "nextday_exec_amt": _to_float(summary.get("nxdy_excc_amt", 0)),
        "prev_closing_amt": _to_float(summary.get("prvs_rcdl_excc_amt", 0)),
        "total_eval_amount": _to_float(summary.get("tot_evlu_amt", 0)),
        "eval_profit": _to_float(summary.get("evlu_pfls_smtl_amt", 0)),
        "buy_amount_today": _to_float(summary.get("thdt_buy_amt", 0)),
        "sell_amount_today": _to_float(summary.get("thdt_sll_amt", 0)),
        "fee_total_today": _to_float(summary.get("tot_stln_slng_chgs", 0)),
        "raw_json": to_jsonb(summary),
        "source_api": API_NAME,
        "source_version": SOURCE_VERSION,
    }


def _build_position_records(account_id: int, holdings: list, as_of_date, as_of_ts):
    records = []

    for holding in holdings:
        qty = _to_int(holding.get("hldg_qty", "0"))
        avg_buy_price = _to_float(holding.get("pchs_avg_pric", "0"))
        buy_amount = _to_float(holding.get("pchs_amt", "0"))
        eval_profit = _to_float(holding.get("evlu_pfls_amt", "0"))
        current_price = _to_float(holding.get("prpr", "0"))

        eval_profit_rate = 0.0
        if buy_amount:
            eval_profit_rate = eval_profit / buy_amount

        records.append(
            {
                "account_id": account_id,
                "account_no": PAPER_ACNT,
                "ticker_code": holding.get("pdno", ""),
                "stock_name": holding.get("prdt_name", ""),
                "market": None,
                "as_of_date": as_of_date,
                "as_of_ts": as_of_ts,
                "quantity": qty,
                "sellable_quantity": _to_int(holding.get("ord_psbl_qty", qty)),
                "avg_buy_price": avg_buy_price,
                "buy_amount": buy_amount,
                "current_price": current_price,
                "eval_amount": _to_float(holding.get("evlu_amt", "0")),
                "eval_profit": eval_profit,
                "eval_profit_rate": eval_profit_rate,
                "raw_json": to_jsonb(holding),
                "source_api": API_NAME,
                "source_version": SOURCE_VERSION,
            }
        )

    return records


def refresh_intraday_snapshot(*, fail_on_open_mismatch: bool = True):
    print("===== INTRADAY_SNAPSHOT_REFRESH START =====")
    print(f"[INFO] source_version={SOURCE_VERSION}")
    print(f"[INFO] account_no={PAPER_ACNT}")

    account_id = ensure_connector_account(
        account_no=PAPER_ACNT,
        account_product_code=ACNT_PRDT_CD,
        broker_name="koreainvestment",
        environment="paper",
        account_alias="main-paper",
    )

    data, _, _ = _load_balance_json(account_id)
    if data is None:
        print("[ERROR] KIS balance API failed")
        return EXIT_API_ERROR

    rt_cd = data.get("rt_cd")
    output1 = data.get("output1") or []
    output2 = data.get("output2") or []
    summary = output2[0] if isinstance(output2, list) and output2 else {}

    print(
        "[INFO] KIS response summary: "
        + _json_dumps(
            {
                "rt_cd": rt_cd,
                "msg_cd": data.get("msg_cd"),
                "output1_count": len(output1) if isinstance(output1, list) else None,
                "output2_count": len(output2) if isinstance(output2, list) else None,
            }
        )
    )

    if rt_cd != "0":
        print(f"[ERROR] KIS balance API returned non-zero rt_cd={rt_cd}, msg={data.get('msg1')}")
        return EXIT_API_ERROR

    as_of_date = _parse_as_of_date(summary)
    as_of_ts = datetime.now()

    balance_record = _build_balance_record(
        account_id=account_id,
        summary=summary,
        data=data,
        as_of_date=as_of_date,
        as_of_ts=as_of_ts,
    )
    upsert_connector_balance_snapshot(balance_record)
    print(f"[OK] connector_balance_snapshot upserted: as_of_date={as_of_date}, as_of_ts={as_of_ts.isoformat()}")

    if not isinstance(output1, list):
        print("[ERROR] KIS output1 is not a list")
        return EXIT_VALIDATION_ERROR

    output1_count = len(output1)
    open_position_count = _count_open_strategy_positions(PAPER_ACNT)

    print(
        "[INFO] position safety check: "
        + _json_dumps(
            {
                "as_of_date": as_of_date,
                "output1_count": output1_count,
                "open_strategy_position_count": open_position_count,
                "fail_on_open_mismatch": fail_on_open_mismatch,
            }
        )
    )

    if output1_count == 0:
        # If strategy also has no OPEN positions, empty broker holdings are normal.
        # Delete same-day position snapshot so same date cannot show stale rows.
        if open_position_count == 0:
            deleted = delete_connector_position_snapshots(
                account_no=PAPER_ACNT,
                as_of_date=as_of_date,
            )
            print(
                "[OK] KIS output1 empty and strategy OPEN positions = 0. "
                f"same-day position snapshot cleared: deleted={deleted}"
            )
            print("===== INTRADAY_SNAPSHOT_REFRESH END: EMPTY_NORMAL =====")
            return EXIT_OK

        open_positions = _fetch_open_strategy_positions(PAPER_ACNT)
        print(
            "[BLOCKER] KIS output1 empty but strategy OPEN positions exist: "
            + _json_dumps(open_positions)
        )
        print("===== INTRADAY_SNAPSHOT_REFRESH END: OPEN_POSITION_MISMATCH =====")
        return EXIT_OPEN_POSITION_MISMATCH if fail_on_open_mismatch else EXIT_OK

    position_records = _build_position_records(
        account_id=account_id,
        holdings=output1,
        as_of_date=as_of_date,
        as_of_ts=as_of_ts,
    )

    delete_count = delete_connector_position_snapshots(
        account_no=PAPER_ACNT,
        as_of_date=as_of_date,
    )
    upsert_connector_position_snapshots(position_records)

    print(
        "[OK] connector_position_snapshot refreshed: "
        + _json_dumps(
            {
                "as_of_date": as_of_date,
                "deleted_before_insert": delete_count,
                "inserted_or_updated": len(position_records),
            }
        )
    )
    print("===== INTRADAY_SNAPSHOT_REFRESH END: POSITIONS_REFRESHED =====")
    return EXIT_OK


def main():
    parser = argparse.ArgumentParser(description="Intraday-safe MarketConnector snapshot refresh")
    parser.add_argument(
        "--allow-open-mismatch",
        action="store_true",
        help="Do not fail when KIS output1 is empty while strategy OPEN positions exist. Not recommended.",
    )
    args = parser.parse_args()

    exit_code = refresh_intraday_snapshot(
        fail_on_open_mismatch=(not args.allow_open_mismatch)
    )
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()