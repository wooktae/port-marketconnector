"""KIS balance/holdings query and snapshot save flow.

Calls the broker balance API and can save to the connector and legacy balance/holding tables.
This is an entrypoint where token handling, external API calls and DB writes can all occur on execution.
"""

import json
import time
from datetime import datetime

import pandas as pd
import requests

from config import APP_KEY, APP_SECRET, BASE_URL, PAPER_ACNT, ACNT_PRDT_CD
from connector_db import (
    delete_connector_position_snapshots,
    ensure_connector_account,
    insert_api_call_log,
    save_balance_summary_legacy,
    save_holdings_legacy,
    to_jsonb,
    upsert_connector_balance_snapshot,
    upsert_connector_position_snapshots,
)
from token_manager import check_and_refresh_token, get_access_token

SOURCE_VERSION = "connector-balance-1.0.0"
API_NAME = "inquire-balance"
TR_ID = "VTTC8434R"
ENDPOINT = "/uapi/domestic-stock/v1/trading/inquire-balance"


def _to_float(v, default=0.0):
    try:
        if v in (None, "", " "):
            return default
        return float(str(v).replace(",", ""))
    except Exception:
        return default


def _to_int(v, default=0):
    try:
        if v in (None, "", " "):
            return default
        return int(float(str(v).replace(",", "")))
    except Exception:
        return default


def fetch_and_save_balance():
    """Parses the balance API response and saves it to the account/holdings snapshot and legacy tables."""
    account_id = ensure_connector_account(
        account_no=PAPER_ACNT,
        account_product_code=ACNT_PRDT_CD,
        broker_name="koreainvestment",
        environment="paper",
        account_alias="main-paper",
    )

    token = get_access_token()
    if not token:
        print("❌ 토큰 없음")
        return None

    def request_balance(tok):
        url = f"{BASE_URL}{ENDPOINT}"
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Bearer {tok}",
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
            res = requests.get(url, headers=headers, params=params, timeout=20)
            latency_ms = int((time.time() - started) * 1000)
            return res, params, latency_ms
        except Exception as e:
            insert_api_call_log(
                account_id=account_id,
                api_category="BALANCE",
                api_name=API_NAME,
                http_method="GET",
                endpoint=ENDPOINT,
                tr_id=TR_ID,
                request_params=params,
                response_message=str(e),
                is_success=False,
            )
            print("❌ 잔고 요청 에러:", e)
            return None, params, None

    res, params, latency_ms = request_balance(token)
    if res is None:
        return None

    new_tok = check_and_refresh_token(res.text)
    if new_tok:
        print("🔄 토큰 오류 감지 → 재발급 + 재요청")
        res, params, latency_ms = request_balance(new_tok)
        if res is None:
            return None

    print("📌 잔고 조회 Status:", res.status_code)

    try:
        data = res.json()
    except Exception as e:
        print("❌ JSON 파싱 실패:", e)
        return None

    insert_api_call_log(
        account_id=account_id,
        api_category="BALANCE",
        api_name=API_NAME,
        http_method="GET",
        endpoint=ENDPOINT,
        tr_id=TR_ID,
        request_params=params,
        response_status=res.status_code,
        response_code=data.get("rt_cd"),
        response_message=data.get("msg1"),
        response_body=data,
        is_success=(data.get("rt_cd") == "0"),
        latency_ms=latency_ms,
    )

    print("\n🔹 잔고 원본 JSON ↓")
    print(json.dumps(data, indent=2, ensure_ascii=False))

    if data.get("rt_cd") != "0":
        print("❌ 조회 오류:", data.get("msg1", data))
        return None

    summary_list = data.get("output2", [])
    summary = summary_list[0] if isinstance(summary_list, list) and summary_list else {}

    ord_dt = summary.get("ord_dt", "")
    try:
        as_of_date = pd.to_datetime(ord_dt).date() if ord_dt else pd.Timestamp.now().date()
    except Exception:
        as_of_date = pd.Timestamp.now().date()

    as_of_ts = datetime.now()

    legacy_balance_record = {
        "account_no": PAPER_ACNT,
        "cash_balance": _to_float(summary.get("dnca_tot_amt", 0)),
        "nextday_exec_amt": _to_float(summary.get("nxdy_excc_amt", 0)),
        "prev_closing_amt": _to_float(summary.get("prvs_rcdl_excc_amt", 0)),
        "total_eval_amount": _to_float(summary.get("tot_evlu_amt", 0)),
        "eval_profit": _to_float(summary.get("evlu_pfls_smtl_amt", 0)),
        "buy_amount_today": _to_float(summary.get("thdt_buy_amt", 0)),
        "sell_amount_today": _to_float(summary.get("thdt_sll_amt", 0)),
        "fee_total_today": _to_float(summary.get("tot_stln_slng_chgs", 0)),
        "as_of_date": as_of_date,
    }

    connector_balance_record = {
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

    # AWS paper: legacy balance_summary write disabled
    # save_balance_summary_legacy(legacy_balance_record)
    upsert_connector_balance_snapshot(connector_balance_record)
    print("connector_balance_snapshot saved OK")

    legacy_holdings = []
    connector_positions = []

    for h in data.get("output1", []):
        qty = _to_int(h.get("hldg_qty", "0"))
        avg_buy = _to_float(h.get("pchs_avg_pric", "0"))
        eval_profit = _to_float(h.get("evlu_pfls_amt", "0"))
        buy_amount = _to_float(h.get("pchs_amt", "0"))
        current_price = _to_float(h.get("prpr", "0"))

        legacy_holdings.append(
            {
                "account_no": PAPER_ACNT,
                "stock_code": h.get("pdno", ""),
                "stock_name": h.get("prdt_name", ""),
                "quantity": qty,
                "avg_buy_price": avg_buy,
                "buy_amount": buy_amount,
                "current_price": current_price,
                "eval_amount": _to_float(h.get("evlu_amt", "0")),
                "eval_profit": eval_profit,
                "as_of_date": as_of_date,
            }
        )

        profit_rate = 0.0
        if buy_amount:
            profit_rate = eval_profit / buy_amount

        connector_positions.append(
            {
                "account_id": account_id,
                "account_no": PAPER_ACNT,
                "ticker_code": h.get("pdno", ""),
                "stock_name": h.get("prdt_name", ""),
                "market": None,
                "as_of_date": as_of_date,
                "as_of_ts": as_of_ts,
                "quantity": qty,
                "sellable_quantity": _to_int(h.get("ord_psbl_qty", qty)),
                "avg_buy_price": avg_buy,
                "buy_amount": buy_amount,
                "current_price": current_price,
                "eval_amount": _to_float(h.get("evlu_amt", "0")),
                "eval_profit": eval_profit,
                "eval_profit_rate": profit_rate,
                "raw_json": to_jsonb(h),
                "source_api": API_NAME,
                "source_version": SOURCE_VERSION,
            }
        )

    # ---------------------------------------------------------
    # position snapshot replace
    # ---------------------------------------------------------
    # Important:
    # connector_position_snapshot is treated as a "full snapshot of current holdings"
    # keyed by account_no + as_of_date.
    #
    # Therefore, first delete the existing snapshot for that date, then
    # save again based on this API's output1.
    #
    # Without this handling:
    # - if there were holdings in the morning
    # - and everything was sold in the afternoon so output1=[]
    # - the existing position rows would remain and a stale position could be shown in the View.
    deleted_position_count = delete_connector_position_snapshots(
        account_no=PAPER_ACNT,
        as_of_date=as_of_date,
    )

    if deleted_position_count:
        print(f"🧹 기존 connector_position_snapshot 삭제 완료: {deleted_position_count}건")

    if legacy_holdings:
        save_holdings_legacy(legacy_holdings)

    if connector_positions:
        upsert_connector_position_snapshots(connector_positions)
        print(f"✅ connector_position_snapshot 저장 완료: {len(connector_positions)}건")
    else:
        print("✅ connector_position_snapshot 비움 완료: 현재 보유종목 0건")

    if legacy_holdings:
        print("✅ holdings legacy 저장 완료")
    else:
        print("⚠ 저장할 legacy holdings 데이터 없음")

    return {
        "balance": connector_balance_record,
        "positions": connector_positions,
        "raw": data,
    }


if __name__ == "__main__":
    fetch_and_save_balance()
