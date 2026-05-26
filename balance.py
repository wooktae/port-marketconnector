import requests
import json
import pandas as pd
import psycopg
from token_manager import get_access_token, check_and_refresh_token
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
# balance_summary 저장/업데이트
# —————————————————————————————
def save_balance_summary(record):
    with psycopg.connect(DB_CONN_STR) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO balance_summary (
                    account_no,
                    cash_balance, nextday_exec_amt,
                    prev_closing_amt, total_eval_amount,
                    eval_profit, buy_amount_today,
                    sell_amount_today, fee_total_today,
                    as_of_date
                ) VALUES (
                    %(account_no)s,
                    %(cash_balance)s, %(nextday_exec_amt)s,
                    %(prev_closing_amt)s, %(total_eval_amount)s,
                    %(eval_profit)s, %(buy_amount_today)s,
                    %(sell_amount_today)s, %(fee_total_today)s,
                    %(as_of_date)s
                )
                ON CONFLICT (account_no, as_of_date)
                DO UPDATE SET
                    cash_balance      = EXCLUDED.cash_balance,
                    nextday_exec_amt  = EXCLUDED.nextday_exec_amt,
                    prev_closing_amt  = EXCLUDED.prev_closing_amt,
                    total_eval_amount = EXCLUDED.total_eval_amount,
                    eval_profit       = EXCLUDED.eval_profit,
                    buy_amount_today  = EXCLUDED.buy_amount_today,
                    sell_amount_today = EXCLUDED.sell_amount_today,
                    fee_total_today   = EXCLUDED.fee_total_today
            """, record)
        conn.commit()

# —————————————————————————————
# holdings 저장/업데이트
# —————————————————————————————
def save_holdings(records):
    with psycopg.connect(DB_CONN_STR) as conn:
        with conn.cursor() as cur:
            for r in records:
                cur.execute("""
                    INSERT INTO holdings (
                        account_no, stock_code, stock_name,
                        quantity, avg_buy_price, buy_amount,
                        current_price, eval_amount, eval_profit,
                        as_of_date
                    ) VALUES (
                        %(account_no)s, %(stock_code)s, %(stock_name)s,
                        %(quantity)s, %(avg_buy_price)s, %(buy_amount)s,
                        %(current_price)s, %(eval_amount)s, %(eval_profit)s,
                        %(as_of_date)s
                    )
                    ON CONFLICT (account_no, stock_code, as_of_date)
                    DO UPDATE SET
                        quantity       = EXCLUDED.quantity,
                        avg_buy_price  = EXCLUDED.avg_buy_price,
                        buy_amount     = EXCLUDED.buy_amount,
                        current_price  = EXCLUDED.current_price,
                        eval_amount    = EXCLUDED.eval_amount,
                        eval_profit    = EXCLUDED.eval_profit
                """, r)
        conn.commit()

# —————————————————————————————
# 잔고 조회 + 저장
# —————————————————————————————
def fetch_and_save_balance():
    token = get_access_token()
    if not token:
        print("❌ 토큰 없음")
        return

    def request_balance(tok):
        url = f"{BASE_URL}/uapi/domestic-stock/v1/trading/inquire-balance"
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Bearer {tok}",
            "appKey": APP_KEY,        # 대소문자 정확히
            "appSecret": APP_SECRET,  # 대소문자 정확히
            "tr_id": "VTTC8434R",
            "custtype": "P"
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
            "CTX_AREA_NK100": ""
        }
        try:
            return requests.get(url, headers=headers, params=params)
        except Exception as e:
            print("❌ 잔고 요청 에러:", e)
            return None

    # 1) 첫 요청
    res = request_balance(token)
    if res is None:
        return

    # 2) 토큰 오류 감지 + 자동 재발급
    new_tok = check_and_refresh_token(res.text)
    if new_tok:
        print("🔄 토큰 오류 감지 → 재발급 + 재요청")
        res = request_balance(new_tok)
        if res is None:
            return

    print("📌 잔고 조회 Status:", res.status_code)

    try:
        data = res.json()
    except Exception as e:
        print("❌ JSON 파싱 실패:", e)
        return

    # — 원본 JSON 로그
    print("\n🔹 잔고 원본 JSON ↓")
    print(json.dumps(data, indent=2, ensure_ascii=False))

    if data.get("rt_cd") != "0":
        print("❌ 조회 오류:", data.get("msg1", data))
        return

    # — 잔고 summary 처리
    summary_list = data.get("output2", [])
    summary = summary_list[0] if isinstance(summary_list, list) and summary_list else {}

    # — as_of_date 처리
    ord_dt = summary.get("ord_dt", "")
    try:
        if ord_dt:
            as_of_date = pd.to_datetime(ord_dt).date()
        else:
            as_of_date = pd.Timestamp.now().date()
    except:
        as_of_date = pd.Timestamp.now().date()

    # — balance_summary 레코드 생성
    balance_record = {
        "account_no": PAPER_ACNT,
        "cash_balance": float(summary.get("dnca_tot_amt", 0)),
        "nextday_exec_amt": float(summary.get("nxdy_excc_amt", 0)),
        "prev_closing_amt": float(summary.get("prvs_rcdl_excc_amt", 0)),
        "total_eval_amount": float(summary.get("tot_evlu_amt", 0)),
        "eval_profit": float(summary.get("evlu_pfls_smtl_amt", 0)),
        "buy_amount_today": float(summary.get("thdt_buy_amt", 0)),
        "sell_amount_today": float(summary.get("thdt_sll_amt", 0)),
        "fee_total_today": float(summary.get("tot_stln_slng_chgs", 0)),
        "as_of_date": as_of_date
    }

    save_balance_summary(balance_record)
    print("✅ balance_summary 저장/업데이트 완료")

    # — 보유주식 처리
    hold_list = []
    for h in data.get("output1", []):
        rec_h = {
            "account_no": PAPER_ACNT,
            "stock_code": h.get("pdno", ""),
            "stock_name": h.get("prdt_name", ""),
            "quantity": int(h.get("hldg_qty", "0")),
            "avg_buy_price": float(h.get("pchs_avg_pric", "0")),
            "buy_amount": float(h.get("pchs_amt", "0")),
            "current_price": float(h.get("prpr", "0")),
            "eval_amount": float(h.get("evlu_amt", "0")),
            "eval_profit": float(h.get("evlu_pfls_amt", "0")),
            "as_of_date": as_of_date
        }
        hold_list.append(rec_h)

    if hold_list:
        save_holdings(hold_list)
        print("✅ holdings 저장/업데이트 완료")
    else:
        print("⚠ 저장할 보유종목 데이터 없음")


# —————————————————————————————
# 실행
# —————————————————————————————
if __name__ == "__main__":
    fetch_and_save_balance()
