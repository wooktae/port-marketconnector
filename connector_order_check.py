"""KIS 주문/체결 조회와 DB 동기화 흐름.

주문/체결 내역 API 응답을 order event, fill, legacy 주문 테이블에 반영한다.
직접 조회 결과와 broad search fallback을 구분해 broker 주문번호 매핑을 보정한다.
"""

import json
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

SOURCE_VERSION = "connector-order-check-2.0.1"
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
    summary fallback용 주문 context 탐색.

    우선순위:
    1) order_no가 있으면 broker_order_no 기준
    2) stock_code가 있으면 ticker_code 기준 최신 주문
    3) 아무 조건도 없으면 당일 ACCEPTED/SUBMITTED 상태의 최신 BUY/SELL 주문

    핵심:
    - request_type은 현재 row의 타입이다. BUY / SELL / MODIFY / CANCEL 가능.
    - trade_side는 parent chain을 따라 올라가서 찾은 원 주문의 BUY / SELL 방향이다.
    - MODIFY / CANCEL 이벤트의 side는 request_type이 아니라 trade_side를 사용해야 한다.

    주의:
    - psycopg3 + PostgreSQL에서 "%s IS NULL" 형태는 파라미터 타입 추론 실패 가능.
    - 그래서 선택 조건 파라미터는 반드시 %s::text 또는 NULLIF(%s::text, '') 형태로 캐스팅한다.
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
            # 인자 없이 실행한 경우에도 당일 ACCEPTED/SUBMITTED 주문을 잡는다.
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
        "SLL_BUY_DVSN_CD": "00",   # 전체
        "INQR_DVSN": "00",
        "PDNO": stock_code or "",
        "CCLD_DVSN": "00",         # 전체
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

    res, latency_ms = _request_history(account_id, token, params)
    if res is None:
        return None, params, latency_ms

    new_tok = check_and_refresh_token(res.text)
    if new_tok:
        print("🔄 토큰 오류 감지 → 재발급 + 재요청")
        res, latency_ms = _request_history(account_id, new_tok, params)
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

    return data, params, latency_ms


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
    output1 empty + output2 summary fallback 적용 가능 후보를 계산한다.

    안전 원칙:
    - summary fallback은 active 주문 후보가 정확히 1건일 때만 허용한다.
    - active 주문 후보가 0건이면 매핑 실패로 생략한다.
    - active 주문 후보가 2건 이상이면 output2가 전체 aggregate summary일 수 있으므로
      connector_order_request / connector_order_event / connector_fill 변경을 금지한다.
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
    """상세 주문 목록이 없고 summary만 있는 조회 결과를 단일 주문 event/fill로 보정한다."""
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
    # summary fallback side 결정 규칙
    # ---------------------------------------------------------
    # BUY / SELL 주문 자체는 반드시 현재 주문의 request_type을 side로 사용해야 한다.
    #
    # 기존 로직은 parent chain에서 계산한 trade_side를 우선 사용했기 때문에,
    # BUY로 생성된 포지션을 SELL 하는 주문이 parent_order_request_id를 통해
    # BUY 주문과 연결되어 있으면 SELL 주문의 event/fill side가 BUY로 저장될 수 있었다.
    #
    # MODIFY / CANCEL은 자체 request_type이 매수/매도가 아니므로 원 주문 방향인
    # trade_side를 사용한다.
    # ---------------------------------------------------------
    if request_type in ("BUY", "SELL"):
        fallback_side = request_type
    elif request_type in ("MODIFY", "CANCEL") and trade_side in ("BUY", "SELL"):
        fallback_side = trade_side
    elif trade_side in ("BUY", "SELL"):
        fallback_side = trade_side
    else:
        fallback_side = "UNKNOWN"

    # LIMIT 주문에서 output1이 비어 있고 summary만 있는 경우,
    # output2는 조회조건 전체 합계일 수 있으므로 체결로 간주하지 않음.
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
    # CANCEL row는 일반 주문 접수(ACCEPTED)가 아니라
    # "취소 요청 접수"로 별도 표현한다.
    #
    # 주의:
    # - 여기서는 KIS history output1 상세가 없고 output2 summary만 있으므로
    #   최종 취소 완료(CANCELED)까지 확정하지 않는다.
    # - 그래서 request_status/event_type은 CANCEL_ACCEPTED로 둔다.
    # - 원 주문/정정 주문을 CANCELED로 바꾸는 후처리는 다음 단계에서 처리한다.
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


def fetch_and_save_orders(
    start_date: str,
    end_date: str,
    stock_code: Optional[str] = None,
    order_no: Optional[str] = None,
    branch_code: Optional[str] = None,
    try_broad_search_if_empty: bool = True,
):
    """
    1) 가능하면 특정 주문번호/종목으로 직접조회
    2) output1이 비면 broad search 1회 재시도
    3) 그래도 비면 output2 summary로 fallback 업데이트
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
    # direct 조회에서 output1은 비었지만 output2 summary가 있는 경우
    # ---------------------------------------------------------
    # 특정 주문번호/종목코드로 조회한 direct output2는 해당 주문의 체결 요약일 가능성이 높다.
    # 이 상태에서 broad search를 먼저 수행하면, broad output2의 전체 합계/평균가가
    # 특정 주문에 섞여 잘못된 event/fill 금액이 저장될 수 있다.
    #
    # 예:
    # - HMM 직접 조회 output2: 54주 / 1,109,950원 / 평균 20,554.6296
    # - broad 조회 output2: 현대해상+HMM 합계 76주 / 1,925,500원 / 평균 25,335.5263
    # 기존 로직은 broad summary를 HMM 주문에 적용할 수 있었음.
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
                # 특정 주문번호/종목 필터링
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

            # broad도 detail 없으면 broad summary 기준 fallback
            _process_summary_fallback(
                account_id=account_id,
                data=broad_data,
                stock_code=stock_code,
                order_no=order_no,
                branch_code=branch_code,
            )
            return broad_data

    # direct만 있고 detail 없으면 direct summary 기준 fallback
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

    args = parser.parse_args()

    fetch_and_save_orders(
        start_date=args.start,
        end_date=args.end,
        stock_code=args.code,
        order_no=args.order_no,
        branch_code=args.branch_code,
        try_broad_search_if_empty=not args.no_broad,
    )
