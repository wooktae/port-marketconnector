"""KIS order/fill query and DB synchronization flow.

Reflects the order/fill history API response into the order event, fill and legacy order tables.
Distinguishes direct query results from the broad search fallback to correct broker order-number mapping.
"""

import json
import os
import sys
import time
from datetime import datetime
from typing import Optional
import argparse

import pandas as pd
import requests

from config import APP_KEY, APP_SECRET, BASE_URL, PAPER_ACNT, ACNT_PRDT_CD

from connector_db import (
    build_order_event_key,
    ensure_connector_account,
    get_conn,
    insert_api_call_log,
    insert_order_event,
    save_trade_orders_legacy,
    to_jsonb,
    update_order_request_after_response,
    upsert_fill,
)
from token_manager import check_and_refresh_token, get_access_token

SOURCE_VERSION = "connector-order-check-2.0.3"

ACTIVE_ORDER_STATUSES = {
    "ACCEPTED",
    "SUBMITTED",
    "PENDING",
    "PARTIAL_FILLED",
    "PARTIALLY_FILLED",
}
SUCCESS_TERMINAL_ORDER_STATUSES = {"FILLED"}
FAILURE_TERMINAL_ORDER_STATUSES = {"REJECTED", "CANCELED", "CANCELLED", "FAILED"}
DEFAULT_ACTIVE_POLL_COUNT = int(os.environ.get("STEP13_ACTIVE_POLL_COUNT", "3"))
DEFAULT_ACTIVE_POLL_INTERVAL_SECONDS = float(
    os.environ.get("STEP13_ACTIVE_POLL_INTERVAL_SECONDS", "10.0")
)
INTER_ORDER_WAIT_SECONDS = float(os.environ.get("STEP13_INTER_ORDER_WAIT_SECONDS", "5.0"))
API_NAME = "inquire-daily-ccld"
TR_ID = "VTTC8001R"
ENDPOINT = "/uapi/domestic-stock/v1/trading/inquire-daily-ccld"


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


def _parse_order_datetime(date_str: str, time_str: str):
    if not date_str or not time_str:
        return None
    try:
        return datetime.strptime(f"{date_str}{time_str[:6]}", "%Y%m%d%H%M%S")
    except Exception:
        return None


def _find_order_request_id(broker_order_no: Optional[str], broker_branch_code: Optional[str]):
    if not broker_order_no:
        return None

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id
                  FROM connector_order_request
                 WHERE broker_order_no = %s
                   AND (
                        broker_branch_code = %s
                        OR %s IS NULL
                        OR broker_branch_code IS NULL
                   )
                 ORDER BY requested_at DESC
                 LIMIT 1
                """,
                (broker_order_no, broker_branch_code, broker_branch_code),
            )
            row = cur.fetchone()

    return row[0] if row else None


def _row_to_order_context(row):
    if not row:
        return None

    return {
        "order_request_id": row[0],
        "broker_order_no": row[1],
        "broker_branch_code": row[2],
        "ticker_code": row[3],
        "order_qty": row[4],
        "request_type": row[5],
        "order_method": row[6],
        "order_price": row[7],
        "trade_side": row[8],
    }


def _find_latest_order_context(
    stock_code: Optional[str] = None,
    order_no: Optional[str] = None,
    branch_code: Optional[str] = None,
):
    """
    Searches the order context for the summary fallback.

    Priority:
    1) If order_no is present, by broker_order_no
    2) If stock_code is present, the latest order by ticker_code
    3) If no condition is present, the latest BUY/SELL order in ACCEPTED/SUBMITTED status for the day

    Key points:
    - request_type is the type of the current row. Can be BUY / SELL / MODIFY / CANCEL.
    - trade_side is the BUY / SELL direction of the original order found by walking up the parent chain.
    - The side of a MODIFY / CANCEL event must use trade_side, not request_type.

    Note:
    - In psycopg3 + PostgreSQL, the "%s IS NULL" form can fail parameter type inference.
    - Therefore, optional-condition parameters must be cast as %s::text or NULLIF(%s::text, '').
    """

    base_select = """
        WITH RECURSIVE target AS (
            SELECT
                id,
                broker_order_no,
                broker_branch_code,
                ticker_code,
                order_qty,
                request_type,
                order_method,
                order_price,
                parent_order_request_id,
                requested_at,
                request_status
            FROM connector_order_request
            WHERE {where_clause}
            ORDER BY
                CASE
                    WHEN request_status IN ('ACCEPTED', 'SUBMITTED', 'PENDING') THEN 1
                    WHEN request_status IN ('PARTIAL_FILLED') THEN 2
                    ELSE 9
                END ASC,
                requested_at DESC,
                id DESC
            LIMIT 1
        ),
        parent_chain AS (
            SELECT
                id,
                parent_order_request_id,
                request_type,
                0 AS depth
            FROM connector_order_request
            WHERE id = (SELECT id FROM target)

            UNION ALL

            SELECT
                p.id,
                p.parent_order_request_id,
                p.request_type,
                pc.depth + 1 AS depth
            FROM connector_order_request p
            JOIN parent_chain pc
              ON pc.parent_order_request_id = p.id
        ),
        root_side AS (
            SELECT request_type AS trade_side
            FROM parent_chain
            WHERE request_type IN ('BUY', 'SELL')
            ORDER BY depth DESC
            LIMIT 1
        )
        SELECT
            t.id,
            t.broker_order_no,
            t.broker_branch_code,
            t.ticker_code,
            t.order_qty,
            t.request_type,
            t.order_method,
            t.order_price,
            COALESCE(rs.trade_side, t.request_type) AS trade_side
        FROM target t
        LEFT JOIN root_side rs ON TRUE
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            if order_no:
                where_clause = """
                    broker_order_no = %s::text
                    AND (
                        NULLIF(%s::text, '') IS NULL
                        OR broker_branch_code = %s::text
                        OR broker_branch_code IS NULL
                    )
                """
                sql = base_select.format(where_clause=where_clause)
                cur.execute(sql, (order_no, branch_code, branch_code))
                row = cur.fetchone()
                if row:
                    return _row_to_order_context(row)

            if stock_code:
                where_clause = """
                    ticker_code = %s::text
                    AND (
                        NULLIF(%s::text, '') IS NULL
                        OR broker_branch_code = %s::text
                        OR broker_branch_code IS NULL
                    )
                """
                sql = base_select.format(where_clause=where_clause)
                cur.execute(sql, (stock_code, branch_code, branch_code))
                row = cur.fetchone()
                if row:
                    return _row_to_order_context(row)

            # -------------------------------------------------
            # Even when run without arguments, capture the day's ACCEPTED/SUBMITTED orders.
            # -------------------------------------------------
            where_clause = """
                request_type IN ('BUY', 'SELL')
                AND request_status IN ('ACCEPTED', 'SUBMITTED', 'PENDING', 'PARTIAL_FILLED')
                AND requested_at::date = CURRENT_DATE
                AND broker_order_no IS NOT NULL
            """
            sql = base_select.format(where_clause=where_clause)
            cur.execute(sql)
            row = cur.fetchone()
            if row:
                return _row_to_order_context(row)

    return None


def _build_params(
    start_date: str,
    end_date: str,
    stock_code: Optional[str],
    order_no: Optional[str],
    branch_code: Optional[str],
):
    return {
        "CANO": PAPER_ACNT,
        "ACNT_PRDT_CD": ACNT_PRDT_CD,
        "INQR_STRT_DT": start_date,
        "INQR_END_DT": end_date,
        "SLL_BUY_DVSN_CD": "00",   # All
        "INQR_DVSN": "00",
        "PDNO": stock_code or "",
        "CCLD_DVSN": "00",         # All
        "ORD_GNO_BRNO": branch_code or "",
        "ODNO": order_no or "",
        "INQR_DVSN_3": "00",
        "INQR_DVSN_1": "",
        "INQR_DVSN_2": "",
        "CTX_AREA_FK100": "",
        "CTX_AREA_NK100": "",
    }


def _request_history(account_id: int, token: str, params: dict):
    url = f"{BASE_URL}{ENDPOINT}"
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Authorization": f"Bearer {token}",
        "appKey": APP_KEY,
        "appSecret": APP_SECRET,
        "tr_id": TR_ID,
        "custtype": "P",
    }

    started = time.time()
    try:
        res = requests.get(url, headers=headers, params=params, timeout=20)
        latency_ms = int((time.time() - started) * 1000)
        return res, latency_ms
    except Exception as e:
        insert_api_call_log(
            account_id=account_id,
            api_category="ORDER",
            api_name=API_NAME,
            http_method="GET",
            endpoint=ENDPOINT,
            tr_id=TR_ID,
            request_params=params,
            response_message=str(e),
            is_success=False,
        )
        print("❌ 조회 요청 에러:", e)
        return None, None


def _call_order_history(account_id: int, params: dict):
    token = get_access_token()
    if not token:
        print("❌ 토큰 없음")
        return None, None, None

    # Initial request + up to 2 retries when EGW00201 occurs
    max_rate_limit_retries = 2
    rate_limit_retry_wait_seconds = (1.5, 5.0)
    rate_limit_retry_count = 0

    while True:
        res, latency_ms = _request_history(account_id, token, params)
        if res is None:
            return None, params, latency_ms

        new_tok = check_and_refresh_token(res.text)
        if new_tok:
            print("🔄 토큰 오류 감지 → 재발급 + 재요청")
            token = new_tok
            res, latency_ms = _request_history(account_id, token, params)
            if res is None:
                return None, params, latency_ms

        print("📌 주문/체결 조회 Status:", res.status_code)

        try:
            data = res.json()
        except Exception as e:
            print("❌ JSON 파싱 실패:", e)
            return None, params, latency_ms

        insert_api_call_log(
            account_id=account_id,
            api_category="ORDER",
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

        message_code = str(data.get("msg_cd") or "").strip()
        if message_code != "EGW00201":
            return data, params, latency_ms

        if rate_limit_retry_count >= max_rate_limit_retries:
            print(
                "❌ 주문/체결 조회 rate limit 재시도 소진: "
                f"msg_cd={message_code}, retries={rate_limit_retry_count}"
            )
            return data, params, latency_ms

        wait_seconds = rate_limit_retry_wait_seconds[rate_limit_retry_count]
        rate_limit_retry_count += 1

        print(
            "⚠ 주문/체결 조회 rate limit 감지: "
            f"msg_cd={message_code}, "
            f"retry={rate_limit_retry_count}/{max_rate_limit_retries}, "
            f"retry_after={wait_seconds}s"
        )
        time.sleep(wait_seconds)


def _derive_status(order_qty: int, executed_qty: int, cancel_flag: str):
    if executed_qty == 0 and cancel_flag == "Y":
        return "CANCELED", "CANCELED"
    if executed_qty == 0:
        return "ORDER_ACCEPTED", "ACCEPTED"
    if executed_qty < order_qty:
        return "PARTIAL_FILLED", "PARTIAL_FILLED"
    return "FILLED", "FILLED"


def _process_detail_orders(account_id: int, orders: list):
    legacy_rows = []

    for idx, o in enumerate(orders, start=1):
        date_str = o.get("ord_dt", "")
        time_str = o.get("ord_tmd", "")
        order_dt = _parse_order_datetime(date_str, time_str)

        order_time_text = ""
        if date_str and time_str and len(time_str) >= 6:
            order_time_text = f"{date_str} {time_str[:2]}:{time_str[2:4]}:{time_str[4:6]}"

        broker_order_no = o.get("odno", "")
        broker_branch_code = o.get("ord_gno_brno", "")
        order_request_id = _find_order_request_id(broker_order_no, broker_branch_code)

        order_qty = _to_int(o.get("ord_qty", "0"))
        executed_qty = _to_int(o.get("tot_ccld_qty", "0"))
        remaining_qty = max(order_qty - executed_qty, 0)
        avg_exec_price = _to_float(o.get("avg_prvs", "0"))
        total_exec_amount = _to_float(o.get("tot_ccld_amt", "0"))
        cancel_flag = o.get("cncl_yn", "N")

        event_type, request_status = _derive_status(order_qty, executed_qty, cancel_flag)

        event_key = build_order_event_key(
            broker_order_no=broker_order_no,
            broker_branch_code=broker_branch_code,
            event_type=event_type,
            ticker_code=o.get("pdno", ""),
            order_qty=order_qty,
            executed_qty=executed_qty,
            total_exec_amount=total_exec_amount,
            cancel_flag=cancel_flag,
        )

        legacy_rows.append(
            {
                "account_no": o.get("acnt_no", PAPER_ACNT),
                "order_no": broker_order_no,
                "branch_code": broker_branch_code,
                "stock_code": o.get("pdno", ""),
                "stock_name": o.get("prdt_name", ""),
                "order_type": o.get("ord_dvsn_name", ""),
                "side": o.get("sll_buy_dvsn_cd_name", ""),
                "order_qty": order_qty,
                "executed_qty": executed_qty,
                "avg_exec_price": avg_exec_price,
                "total_exec_amount": total_exec_amount,
                "order_time": order_time_text,
                "cancel_flag": cancel_flag,
            }
        )

        inserted_event_id = insert_order_event(
            {
                "order_request_id": order_request_id,
                "account_id": account_id,
                "account_no": o.get("acnt_no", PAPER_ACNT),
                "broker_order_no": broker_order_no,
                "broker_branch_code": broker_branch_code,
                "ticker_code": o.get("pdno", ""),
                "stock_name": o.get("prdt_name", ""),
                "event_type": event_type,
                "side": o.get("sll_buy_dvsn_cd_name", ""),
                "order_type_name": o.get("ord_dvsn_name", ""),
                "order_qty": order_qty,
                "executed_qty": executed_qty,
                "remaining_qty": remaining_qty,
                "avg_exec_price": avg_exec_price,
                "total_exec_amount": total_exec_amount,
                "cancel_flag": cancel_flag,
                "order_date": pd.to_datetime(date_str).date() if date_str else None,
                "order_time": order_dt,
                "event_ts": datetime.now(),
                "event_key": event_key,
                "raw_json": to_jsonb(o),
                "source_api": API_NAME,
                "source_version": SOURCE_VERSION,
            }
        )

        if order_request_id:
            update_order_request_after_response(
                order_request_id,
                {
                    "request_status": request_status,
                    "broker_order_no": broker_order_no,
                    "broker_branch_code": broker_branch_code,
                    "rejection_code": None,
                    "rejection_message": None,
                    "response_payload": to_jsonb(o),
                    "accepted_at": order_dt,
                    "last_event_at": datetime.now(),
                },
            )

        if executed_qty > 0:
            upsert_fill(
                {
                    "order_request_id": order_request_id,
                    "order_event_id": inserted_event_id,
                    "account_id": account_id,
                    "broker_order_no": broker_order_no,
                    "broker_branch_code": broker_branch_code,
                    "ticker_code": o.get("pdno", ""),
                    "side": o.get("sll_buy_dvsn_cd_name", ""),
                    "fill_seq": idx,
                    "fill_qty": executed_qty,
                    "fill_price": avg_exec_price,
                    "fill_ts": order_dt or datetime.now(),
                    "fee_amount": None,
                    "tax_amount": None,
                    "raw_json": to_jsonb(o),
                }
            )

    if legacy_rows:
        save_trade_orders_legacy(legacy_rows)
        print(f"\n✅ trade_orders + connector_order_event + connector_fill 저장 완료 ({len(legacy_rows)}건)")
    else:
        print("\n⚠ 저장할 주문/체결 데이터 없음(detail)")

    return legacy_rows


def _count_summary_fallback_candidates(
    stock_code: Optional[str] = None,
    order_no: Optional[str] = None,
    branch_code: Optional[str] = None,
):
    """
    Computes candidates eligible for the output1 empty + output2 summary fallback.

    Safety principles:
    - The summary fallback is allowed only when there is exactly 1 active order candidate.
    - If there are 0 active order candidates, skip it as a mapping failure.
    - If there are 2 or more active order candidates, output2 may be a full aggregate summary,
      so prohibit changes to connector_order_request / connector_order_event / connector_fill.
    """

    conditions = [
        "request_type IN ('BUY', 'SELL')",
        "request_status IN ('ACCEPTED', 'SUBMITTED', 'PENDING', 'PARTIAL_FILLED')",
        "requested_at::date = CURRENT_DATE",
        "broker_order_no IS NOT NULL",
    ]
    params = []

    if order_no:
        conditions.append("broker_order_no = %s::text")
        params.append(order_no)

    if stock_code:
        conditions.append("ticker_code = %s::text")
        params.append(stock_code)

    if branch_code:
        conditions.append(
            "(broker_branch_code = %s::text OR broker_branch_code IS NULL)"
        )
        params.append(branch_code)

    sql = f"""
        SELECT
            id,
            broker_order_no,
            broker_branch_code,
            ticker_code,
            order_qty,
            request_type,
            order_method,
            order_price,
            request_status,
            requested_at
        FROM connector_order_request
        WHERE {" AND ".join(conditions)}
        ORDER BY requested_at DESC, id DESC
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, tuple(params))
            rows = cur.fetchall()

    return rows


def _process_summary_fallback(
    account_id: int,
    data: dict,
    stock_code: Optional[str],
    order_no: Optional[str],
    branch_code: Optional[str],
):
    """Corrects a query result that has no detailed order list but only a summary into a single order's event/fill."""
    summary = data.get("output2", {}) or {}
    detail_orders = data.get("output1", []) or []

    tot_ord_qty = _to_int(summary.get("tot_ord_qty", "0"))
    tot_ccld_qty = _to_int(summary.get("tot_ccld_qty", "0"))
    tot_ccld_amt = _to_float(summary.get("tot_ccld_amt", "0"))
    avg_price = _to_float(summary.get("pchs_avg_pric", "0"))

    if detail_orders:
        print("⚠ summary fallback guard: output1 상세행이 있어 fallback 생략")
        return

    if tot_ord_qty <= 0:
        print("⚠ summary에도 주문수량이 없어 fallback 생략")
        return

    fallback_candidates = _count_summary_fallback_candidates(
        stock_code=stock_code,
        order_no=order_no,
        branch_code=branch_code,
    )

    if len(fallback_candidates) != 1:
        print(
            "⚠ summary fallback 생략: "
            f"active_order_candidates={len(fallback_candidates)}, "
            "output1 empty + output2 summary only 상태에서는 "
            "후보가 정확히 1건일 때만 event/fill/status 변경 허용"
        )
        for row in fallback_candidates[:10]:
            print(
                "  - candidate "
                f"id={row[0]}, broker_order_no={row[1]}, "
                f"branch={row[2]}, ticker={row[3]}, qty={row[4]}, "
                f"type={row[5]}, method={row[6]}, status={row[8]}"
            )
        return

    context = _find_latest_order_context(
        stock_code=stock_code,
        order_no=order_no,
        branch_code=branch_code,
    )

    if not context:
        print("⚠ summary는 있지만 connector_order_request 매핑 실패")
        return

    order_request_id = context["order_request_id"]
    broker_order_no = context["broker_order_no"] or order_no
    broker_branch_code = context["broker_branch_code"] or branch_code
    fallback_ticker = context["ticker_code"] or stock_code
    order_qty = context["order_qty"] or tot_ord_qty

    request_type = (context.get("request_type") or "").upper()
    order_method = (context.get("order_method") or "").upper()
    trade_side = (context.get("trade_side") or "").upper()

    # ---------------------------------------------------------
    # Rule for deciding the summary fallback side
    # ---------------------------------------------------------
    # A BUY / SELL order itself must use the current order's request_type as the side.
    #
    # The previous logic preferred the trade_side computed from the parent chain, so
    # if an order selling a position created by a BUY was linked to the BUY order via
    # parent_order_request_id, the SELL order's event/fill side could be saved as BUY.
    #
    # MODIFY / CANCEL have a request_type that is not buy/sell, so they use the
    # original order's direction, trade_side.
    # ---------------------------------------------------------
    if request_type in ("BUY", "SELL"):
        fallback_side = request_type
    elif request_type in ("MODIFY", "CANCEL") and trade_side in ("BUY", "SELL"):
        fallback_side = trade_side
    elif trade_side in ("BUY", "SELL"):
        fallback_side = trade_side
    else:
        fallback_side = "UNKNOWN"

    # For a LIMIT order where output1 is empty and only a summary is present,
    # output2 may be the total of the query conditions, so do not treat it as a fill.
    if order_method == "LIMIT":
        fallback_executed_qty = 0
        fallback_total_exec_amount = 0
    else:
        fallback_executed_qty = min(order_qty, tot_ccld_qty)

        if avg_price > 0 and fallback_executed_qty > 0:
            fallback_total_exec_amount = avg_price * fallback_executed_qty
        else:
            fallback_total_exec_amount = tot_ccld_amt

    cancel_flag = "N"

    # ---------------------------------------------------------
    # A CANCEL row is not a normal order acceptance (ACCEPTED); it is represented
    # separately as a "cancellation request acceptance".
    #
    # Note:
    # - Here, there is no KIS history output1 detail, only an output2 summary, so
    #   we do not finalize as fully canceled (CANCELED).
    # - Therefore request_status/event_type is left as CANCEL_ACCEPTED.
    # - The post-processing that changes the original/modification order to CANCELED is handled in the next step.
    # ---------------------------------------------------------
    if request_type == "CANCEL":
        event_type = "CANCEL_ACCEPTED"
        request_status = "CANCEL_ACCEPTED"
    else:
        event_type, request_status = _derive_status(
            order_qty,
            fallback_executed_qty,
            cancel_flag,
        )

    summary_event_type = f"SUMMARY_ONLY_{event_type}"

    event_key = build_order_event_key(
        broker_order_no=broker_order_no,
        broker_branch_code=broker_branch_code,
        event_type=summary_event_type,
        ticker_code=fallback_ticker,
        order_qty=order_qty,
        executed_qty=fallback_executed_qty,
        total_exec_amount=fallback_total_exec_amount,
        cancel_flag=cancel_flag,
    )

    now_ts = datetime.now()

    update_order_request_after_response(
        order_request_id,
        {
            "request_status": request_status,
            "broker_order_no": broker_order_no,
            "broker_branch_code": broker_branch_code,
            "rejection_code": None,
            "rejection_message": "output1 empty; updated from output2 summary",
            "response_payload": to_jsonb(data),
            "accepted_at": None,
            "last_event_at": now_ts,
        },
    )

    inserted_event_id = insert_order_event(
        {
            "order_request_id": order_request_id,
            "account_id": account_id,
            "account_no": PAPER_ACNT,
            "broker_order_no": broker_order_no,
            "broker_branch_code": broker_branch_code,
            "ticker_code": fallback_ticker,
            "stock_name": None,
            "event_type": summary_event_type,
            "side": fallback_side,
            "order_type_name": None,
            "order_qty": order_qty,
            "executed_qty": fallback_executed_qty,
            "remaining_qty": max(order_qty - fallback_executed_qty, 0),
            "avg_exec_price": avg_price,
            "total_exec_amount": fallback_total_exec_amount,
            "cancel_flag": cancel_flag,
            "order_date": now_ts.date(),
            "order_time": None,
            "event_ts": now_ts,
            "event_key": event_key,
            "raw_json": to_jsonb(data),
            "source_api": API_NAME,
            "source_version": SOURCE_VERSION,
        }
    )

    if fallback_executed_qty > 0:
        upsert_fill(
            {
                "order_request_id": order_request_id,
                "order_event_id": inserted_event_id,
                "account_id": account_id,
                "broker_order_no": broker_order_no,
                "broker_branch_code": broker_branch_code,
                "ticker_code": fallback_ticker,
                "side": fallback_side,
                "fill_seq": 1,
                "fill_qty": fallback_executed_qty,
                "fill_price": avg_price,
                "fill_ts": now_ts,
                "fee_amount": None,
                "tax_amount": None,
                "raw_json": to_jsonb(data),
            }
        )

    print(
        f"✅ summary fallback 처리 완료: "
        f"status={request_status}, side={fallback_side}, "
        f"qty={fallback_executed_qty}, amt={fallback_total_exec_amount}, "
        f"event_id={inserted_event_id}"
    )



def _find_active_order_contexts(limit: Optional[int] = None):
    """
    Queries the active order list for the default Step 13 execution.

    Operational principles:
    - Do not use the broad query as the default DB-reflection path.
    - Query active orders that have broker_order_no / ticker_code one at a time.
    - Process each order with fetch_and_save_orders(..., try_broad_search_if_empty=False).
    """

    limit_clause = ""
    params = []
    if limit and limit > 0:
        limit_clause = "LIMIT %s"
        params.append(limit)

    sql = f"""
        SELECT
            id,
            broker_order_no,
            broker_branch_code,
            ticker_code,
            order_qty,
            request_type,
            order_method,
            order_price,
            request_status,
            requested_at
        FROM connector_order_request
        WHERE request_type IN ('BUY', 'SELL')
          AND request_status IN (
                'ACCEPTED',
                'SUBMITTED',
                'PENDING',
                'PARTIAL_FILLED',
                'PARTIALLY_FILLED'
          )
          AND requested_at::date = CURRENT_DATE
          AND broker_order_no IS NOT NULL
          AND ticker_code IS NOT NULL
        ORDER BY requested_at ASC, id ASC
        {limit_clause}
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, tuple(params))
            rows = cur.fetchall()

    contexts = []
    for row in rows:
        contexts.append(
            {
                "order_request_id": row[0],
                "broker_order_no": row[1],
                "broker_branch_code": row[2],
                "ticker_code": row[3],
                "order_qty": row[4],
                "request_type": row[5],
                "order_method": row[6],
                "order_price": row[7],
                "request_status": row[8],
                "requested_at": row[9],
            }
        )

    return contexts


def _get_order_request_status(order_request_id: int) -> Optional[str]:
    """Queries the latest status of the connector order reflected in the DB."""

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT request_status
                FROM connector_order_request
                WHERE id = %s
                """,
                (order_request_id,),
            )
            row = cur.fetchone()

    if not row or row[0] is None:
        return None
    return str(row[0]).strip().upper()


def reconcile_active_orders(
    start_date: str,
    end_date: str,
    limit: Optional[int] = None,
    poll_count: int = DEFAULT_ACTIVE_POLL_COUNT,
    poll_interval_seconds: float = DEFAULT_ACTIVE_POLL_INTERVAL_SECONDS,
):
    """
    Step 13 default mode.

    Queries active orders one at a time by order number/stock code. If an
    ACCEPTED/PARTIAL_FILLED-family status remains after the query, poll up to the limited count.

    Success condition:
    - Each target order transitions to FILLED

    Failure conditions:
    - API/DB processing failure
    - A failure terminal status such as REJECTED/CANCELED/FAILED
    - The active status remains after polling is exhausted
    - A missing status row or an unknown status
    """

    if poll_count < 0:
        raise ValueError("poll_count must be >= 0")
    if poll_interval_seconds < 0:
        raise ValueError("poll_interval_seconds must be >= 0")

    active_orders = _find_active_order_contexts(limit=limit)

    if not active_orders:
        print("\n✅ Step 13 skip: active connector order 없음")
        return 0

    max_attempts = poll_count + 1
    print(
        f"\n📌 Step 13 active 주문 단건 순차 조회 시작: {len(active_orders)}건, "
        f"max_attempts={max_attempts}, poll_interval={poll_interval_seconds}s"
    )

    success_count = 0
    failure_count = 0
    pending_exhausted_count = 0

    for idx, order in enumerate(active_orders, start=1):
        if idx > 1 and INTER_ORDER_WAIT_SECONDS > 0:
            print(
                f"⏳ Step 13 주문별 조회 간격 대기: "
                f"{INTER_ORDER_WAIT_SECONDS}s"
            )
            time.sleep(INTER_ORDER_WAIT_SECONDS)

        order_request_id = order["order_request_id"]
        ticker_code = order["ticker_code"]
        broker_order_no = order["broker_order_no"]
        broker_branch_code = order["broker_branch_code"]

        print(
            "\n"
            + "=" * 80
            + f"\n🔎 Step 13 per-order check {idx}/{len(active_orders)} "
            f"id={order_request_id}, code={ticker_code}, "
            f"order_no={broker_order_no}, branch={broker_branch_code or ''}, "
            f"qty={order['order_qty']}, initial_status={order['request_status']}"
            + "\n"
            + "=" * 80
        )

        order_succeeded = False
        order_failed = False

        for attempt in range(1, max_attempts + 1):
            if attempt > 1 and poll_interval_seconds > 0:
                print(
                    f"⏳ Step 13 active 상태 polling 대기: "
                    f"id={order_request_id}, attempt={attempt}/{max_attempts}, "
                    f"wait={poll_interval_seconds}s"
                )
                time.sleep(poll_interval_seconds)

            try:
                result = fetch_and_save_orders(
                    start_date=start_date,
                    end_date=end_date,
                    stock_code=ticker_code,
                    order_no=broker_order_no,
                    branch_code=broker_branch_code,
                    try_broad_search_if_empty=False,
                )
                if result is None:
                    print(
                        f"❌ Step 13 per-order API/DB 처리 실패: "
                        f"id={order_request_id}, attempt={attempt}/{max_attempts}"
                    )
                    order_failed = True
                    break

                latest_status = _get_order_request_status(order_request_id)
                print(
                    f"📌 Step 13 polling 결과: id={order_request_id}, "
                    f"attempt={attempt}/{max_attempts}, status={latest_status}"
                )

                if latest_status in SUCCESS_TERMINAL_ORDER_STATUSES:
                    order_succeeded = True
                    print(
                        f"✅ Step 13 주문 체결 완료: "
                        f"id={order_request_id}, status={latest_status}"
                    )
                    break

                if latest_status in FAILURE_TERMINAL_ORDER_STATUSES:
                    print(
                        f"❌ Step 13 주문 실패 terminal 상태: "
                        f"id={order_request_id}, status={latest_status}"
                    )
                    order_failed = True
                    break

                if latest_status in ACTIVE_ORDER_STATUSES:
                    if attempt < max_attempts:
                        continue

                    pending_exhausted_count += 1
                    print(
                        f"❌ Step 13 polling 소진 후 active 상태 유지: "
                        f"id={order_request_id}, status={latest_status}, "
                        f"attempts={max_attempts}"
                    )
                    order_failed = True
                    break

                print(
                    f"❌ Step 13 알 수 없는 주문 상태: "
                    f"id={order_request_id}, status={latest_status}"
                )
                order_failed = True
                break

            except Exception as e:
                print(
                    f"❌ Step 13 per-order check 예외: "
                    f"id={order_request_id}, attempt={attempt}/{max_attempts}, error={e}"
                )
                order_failed = True
                break

        if order_succeeded:
            success_count += 1
        elif order_failed:
            failure_count += 1
        else:
            failure_count += 1
            print(
                f"❌ Step 13 주문 최종 상태 미결정: "
                f"id={order_request_id}"
            )

    print(
        "\n📌 Step 13 active 주문 polling 요약: "
        f"success={success_count}, failed={failure_count}, "
        f"pending_exhausted={pending_exhausted_count}, total={len(active_orders)}"
    )

    return 1 if failure_count > 0 else 0


def fetch_and_save_orders(
    start_date: str,
    end_date: str,
    stock_code: Optional[str] = None,
    order_no: Optional[str] = None,
    branch_code: Optional[str] = None,
    try_broad_search_if_empty: bool = True,
):
    """
    1) If possible, query directly by a specific order number/stock
    2) If output1 is empty, retry the broad search once
    3) If still empty, update via fallback using the output2 summary
    """

    account_id = ensure_connector_account(
        account_no=PAPER_ACNT,
        account_product_code=ACNT_PRDT_CD,
        broker_name="koreainvestment",
        environment="paper",
        account_alias="main-paper",
    )

    direct_params = _build_params(
        start_date=start_date,
        end_date=end_date,
        stock_code=stock_code,
        order_no=order_no,
        branch_code=branch_code,
    )

    data, _, _ = _call_order_history(account_id, direct_params)
    if data is None:
        return None

    print("\n🔹 주문/체결 원본 JSON ↓")
    print(json.dumps(data, indent=2, ensure_ascii=False))

    if data.get("rt_cd") != "0":
        print("❌ 조회 오류:", data.get("msg1"))
        return None

    orders = data.get("output1", []) or []
    print("\n🔸 direct orders 배열 ↓")
    print(json.dumps(orders, indent=2, ensure_ascii=False))

    if orders:
        _process_detail_orders(account_id, orders)
        return data

    # ---------------------------------------------------------
    # When output1 is empty in a direct query but an output2 summary exists
    # ---------------------------------------------------------
    # The direct output2 queried by a specific order number/stock code is likely the fill summary of that order.
    # If a broad search is performed first in this state, the total/average price of the broad output2 can be
    # mixed into the specific order and an incorrect event/fill amount can be saved.
    #
    # Example:
    # - HMM direct-query output2: 54 shares / 1,109,950 KRW / avg 20,554.6296
    # - broad-query output2: 현대해상+HMM total 76 shares / 1,925,500 KRW / avg 25,335.5263
    # The previous logic could apply the broad summary to the HMM order.
    # ---------------------------------------------------------
    direct_summary = data.get("output2", {}) or {}
    direct_tot_ord_qty = _to_int(direct_summary.get("tot_ord_qty", "0"))
    direct_tot_ccld_qty = _to_int(direct_summary.get("tot_ccld_qty", "0"))
    direct_tot_ccld_amt = _to_float(direct_summary.get("tot_ccld_amt", "0"))

    if (stock_code or order_no or branch_code) and (
        direct_tot_ord_qty > 0 or direct_tot_ccld_qty > 0 or direct_tot_ccld_amt > 0
    ):
        print("\n⚠ direct 조회에서 output1은 비었지만 output2 summary가 있어 direct fallback 처리")
        _process_summary_fallback(
            account_id=account_id,
            data=data,
            stock_code=stock_code,
            order_no=order_no,
            branch_code=branch_code,
        )
        return data

    if try_broad_search_if_empty and (stock_code or order_no or branch_code):
        print("\n⚠ direct 조회에서 output1 비었고 direct summary도 부족해. broad search 1회 재시도")

        broad_params = _build_params(
            start_date=start_date,
            end_date=end_date,
            stock_code=None,
            order_no=None,
            branch_code=None,
        )

        broad_data, _, _ = _call_order_history(account_id, broad_params)
        if broad_data is not None and broad_data.get("rt_cd") == "0":
            print("\n🔹 broad search 원본 JSON ↓")
            print(json.dumps(broad_data, indent=2, ensure_ascii=False))

            broad_orders = broad_data.get("output1", []) or []
            print("\n🔸 broad orders 배열 ↓")
            print(json.dumps(broad_orders, indent=2, ensure_ascii=False))

            if broad_orders:
                # Filter by a specific order number/stock
                filtered_orders = []
                for o in broad_orders:
                    if stock_code and o.get("pdno") != stock_code:
                        continue
                    if order_no and o.get("odno") != order_no:
                        continue
                    if branch_code and o.get("ord_gno_brno") != branch_code:
                        continue
                    filtered_orders.append(o)

                if filtered_orders:
                    _process_detail_orders(account_id, filtered_orders)
                    return broad_data
                else:
                    print("⚠ broad search에서는 상세행이 있었지만 대상 주문과 일치하는 건 없음")

            # If broad also has no detail, fall back based on the broad summary
            _process_summary_fallback(
                account_id=account_id,
                data=broad_data,
                stock_code=stock_code,
                order_no=order_no,
                branch_code=branch_code,
            )
            return broad_data

    # If only direct exists with no detail, fall back based on the direct summary
    _process_summary_fallback(
        account_id=account_id,
        data=data,
        stock_code=stock_code,
        order_no=order_no,
        branch_code=branch_code,
    )
    return data


if __name__ == "__main__":
    today = datetime.now().strftime("%Y%m%d")

    parser = argparse.ArgumentParser(
        description="KIS 주문/체결 조회 후 connector_order_event / connector_fill 저장"
    )
    parser.add_argument("--start", default=today, help="조회 시작일 YYYYMMDD")
    parser.add_argument("--end", default=today, help="조회 종료일 YYYYMMDD")
    parser.add_argument("--code", default=None, help="종목코드. 예: 005930")
    parser.add_argument("--order-no", default=None, help="브로커 주문번호")
    parser.add_argument("--branch-code", default=None, help="주문점/지점 코드")
    parser.add_argument(
        "--no-broad",
        action="store_true",
        help="direct 조회가 비어도 broad search 재시도하지 않음",
    )
    parser.add_argument(
        "--broad",
        action="store_true",
        help=(
            "legacy broad 조회 모드. 지정하지 않으면 기본값은 "
            "active 주문 단건 순차 조회 또는 명시 주문 direct-only 조회"
        ),
    )
    parser.add_argument(
        "--active-limit",
        type=int,
        default=None,
        help="기본 active 주문 단건 순차 조회 최대 처리 건수",
    )
    parser.add_argument(
        "--active-poll-count",
        type=int,
        default=DEFAULT_ACTIVE_POLL_COUNT,
        help=(
            "최초 조회 후 active 상태일 때 추가 polling 횟수 "
            f"(기본값: {DEFAULT_ACTIVE_POLL_COUNT})"
        ),
    )
    parser.add_argument(
        "--active-poll-interval",
        type=float,
        default=DEFAULT_ACTIVE_POLL_INTERVAL_SECONDS,
        help=(
            "active 주문 polling 간 대기 초 "
            f"(기본값: {DEFAULT_ACTIVE_POLL_INTERVAL_SECONDS})"
        ),
    )

    args = parser.parse_args()

    has_direct_filter = bool(args.code or args.order_no or args.branch_code)

    if args.broad:
        result = fetch_and_save_orders(
            start_date=args.start,
            end_date=args.end,
            stock_code=args.code,
            order_no=args.order_no,
            branch_code=args.branch_code,
            try_broad_search_if_empty=not args.no_broad,
        )
        sys.exit(0 if result is not None else 1)

    if has_direct_filter:
        result = fetch_and_save_orders(
            start_date=args.start,
            end_date=args.end,
            stock_code=args.code,
            order_no=args.order_no,
            branch_code=args.branch_code,
            try_broad_search_if_empty=False,
        )
        sys.exit(0 if result is not None else 1)

    sys.exit(
        reconcile_active_orders(
            start_date=args.start,
            end_date=args.end,
            limit=args.active_limit,
            poll_count=args.active_poll_count,
            poll_interval_seconds=args.active_poll_interval,
        )
    )
