import requests
import json
import pandas as pd
import psycopg
from token_manager import get_access_token, force_issue_new_token, check_and_refresh_token
from config import APP_KEY, APP_SECRET, BASE_URL, PAPER_ACNT, ACNT_PRDT_CD

# —————————————————————————————
# DB 연결 설정 (psycopg v3)
# —————————————————————————————
DB_CONN_STR = """
host=localhost
port=5433
dbname=interest_crawler
user=postgres
password=doflwhsk3768!
"""

# —————————————————————————————
# trade_orders 저장
# —————————————————————————————
def save_trade_orders(records):
    with psycopg.connect(DB_CONN_STR) as conn:
        with conn.cursor() as cur:
            for r in records:
                cur.execute("""
                    INSERT INTO trade_orders (
                        account_no, order_no, branch_code,
                        stock_code, stock_name, order_type,
                        side, order_qty, executed_qty,
                        avg_exec_price, total_exec_amount,
                        order_time, cancel_flag
                    ) VALUES (
                        %(account_no)s, %(order_no)s, %(branch_code)s,
                        %(stock_code)s, %(stock_name)s, %(order_type)s,
                        %(side)s, %(order_qty)s, %(executed_qty)s,
                        %(avg_exec_price)s, %(total_exec_amount)s,
                        %(order_time)s, %(cancel_flag)s
                    )
                    ON CONFLICT (order_no, branch_code)
                    DO UPDATE SET
                        executed_qty = EXCLUDED.executed_qty,
                        avg_exec_price = EXCLUDED.avg_exec_price,
                        total_exec_amount = EXCLUDED.total_exec_amount,
                        cancel_flag = EXCLUDED.cancel_flag,
                        order_time = EXCLUDED.order_time
                """, r)
        conn.commit()

# —————————————————————————————
# 주문/체결 조회 + 저장 + 로그 출력
# —————————————————————————————
def fetch_and_save_orders(start_date: str, end_date: str):
    token = get_access_token()
    if not token:
        print("❌ 토큰 없음")
        return

    def request_history(tok):
        url = f"{BASE_URL}/uapi/domestic-stock/v1/trading/inquire-daily-ccld"
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Bearer {tok}",
            "appKey": APP_KEY,         # 대소문자 정확히
            "appSecret": APP_SECRET,   # 대소문자 정확히
            "tr_id": "VTTC8001R",
            "custtype": "P"
        }
        params = {
            "CANO": PAPER_ACNT,
            "ACNT_PRDT_CD": ACNT_PRDT_CD,
            "INQR_STRT_DT": start_date,
            "INQR_END_DT": end_date,
            "SLL_BUY_DVSN_CD": "00",
            "INQR_DVSN": "00",
            "PDNO": "",
            "CCLD_DVSN": "00",
            "ORD_GNO_BRNO": "",
            "ODNO": "",
            "INQR_DVSN_3": "00",
            "INQR_DVSN_1": "",
            "INQR_DVSN_2": "",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": ""
        }
        try:
            return requests.get(url, headers=headers, params=params)
        except Exception as e:
            print("❌ 조회 요청 에러:", e)
            return None

    # 최초 요청
    res = request_history(token)
    if res is None:
        return

    # 토큰 오류 판별 + 자동 재발급
    new_tok = check_and_refresh_token(res.text)
    if new_tok:
        print("🔄 토큰 오류 감지 → 재발급 + 재요청")
        token = new_tok
        res = request_history(token)
        if res is None:
            return

    print("📌 주문/체결 조회 Status:", res.status_code)

    try:
        data = res.json()
    except Exception as e:
        print("❌ JSON 파싱 실패:", e)
        return

    # **📍 RAW JSON 로그**
    print("\n🔹 주문/체결 원본 JSON ↓")
    print(json.dumps(data, indent=2, ensure_ascii=False))

    if data.get("rt_cd") != "0":
        print("❌ 조회 오류:", data.get("msg1"))
        return

    # 3) 주문/체결 추출
    orders = data.get("output1", [])

    print("\n🔸 orders 배열 ↓")
    print(json.dumps(orders, indent=2, ensure_ascii=False))

    # 4) DB 저장 리스트 생성
    save_list = []
    for o in orders:
        date_str = o.get("ord_dt", "")
        time_str = o.get("ord_tmd", "")
        if date_str and time_str:
            order_time = f"{date_str} {time_str[:2]}:{time_str[2:4]}:{time_str[4:6]}"
        else:
            order_time = ""

        rec = {
            "account_no": o.get("acnt_no", PAPER_ACNT),
            "order_no": o.get("odno", ""),
            "branch_code": o.get("ord_gno_brno", ""),
            "stock_code": o.get("pdno", ""),
            "stock_name": o.get("prdt_name", ""),
            "order_type": o.get("ord_dvsn_name", ""),
            "side": o.get("sll_buy_dvsn_cd_name", ""),
            "order_qty": int(o.get("ord_qty", "0")),
            "executed_qty": int(o.get("tot_ccld_qty", "0")),
            "avg_exec_price": float(o.get("avg_prvs", "0")),
            "total_exec_amount": float(o.get("tot_ccld_amt", "0")),
            "order_time": order_time,
            "cancel_flag": o.get("cncl_yn", "N")
        }
        save_list.append(rec)

    # 5) DB 저장
    if save_list:
        save_trade_orders(save_list)
        print(f"\n✅ trade_orders 저장 완료 ({len(save_list)}건)")
    else:
        print("\n⚠ 저장할 주문/체결 데이터 없음")

# —————————————————————————————
# 실행
# —————————————————————————————
if __name__ == "__main__":
    # 조회할 날짜 범위 설정
    fetch_and_save_orders("20260208", "20260228")
