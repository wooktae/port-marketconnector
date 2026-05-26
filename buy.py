import requests
from token_manager import get_access_token, force_issue_new_token, delete_token_file, issue_new_token, check_and_refresh_token
from config import APP_KEY, APP_SECRET, BASE_URL, PAPER_ACNT, ACNT_PRDT_CD

# —————————————————————————————
# 매수 주문 함수
# —————————————————————————————
def buy_stock(stock_code: str, qty: int = 1):
    print(f"\n📌 주문 시작: {stock_code} {qty}주")

    # 1) 토큰 얻기
    token = get_access_token()
    if not token:
        print("❌ 토큰이 없어서 주문 불가")
        return

    # 주문 요청 함수 정의
    def request_order(tok):
        url = f"{BASE_URL}/uapi/domestic-stock/v1/trading/order-cash"
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Bearer {tok}",
            "appKey": APP_KEY,          # 대소문자 주의
            "appSecret": APP_SECRET,    # 대소문자 주의
            "tr_id": "VTTC0802U",
            "custtype": "P"
        }
        body = {
            "CANO": PAPER_ACNT,
            "ACNT_PRDT_CD": ACNT_PRDT_CD,
            "PDNO": stock_code,
            "ORD_DVSN": "01",     # 01: 시장가
            "ORD_QTY": str(qty),
            "ORD_UNPR": "0"       # 시장가면 0
        }
        try:
            return requests.post(url, headers=headers, json=body)
        except Exception as e:
            print("❌ 주문 요청 에러:", e)
            return None

    # 2) 첫 요청
    res = request_order(token)
    if res is None:
        return

    # 3) 토큰 오류 감지 및 자동 재발급
    new_tok = check_and_refresh_token(res.text)
    if new_tok:
        print("🔄 토큰 오류 감지 → 재발급된 토큰으로 재요청")
        token = new_tok
        res = request_order(token)
        if res is None:
            return

    # 4) 결과 처리
    print("📌 주문 응답 Status:", res.status_code)
    try:
        data = res.json()
    except Exception as e:
        print("❌ JSON 파싱 실패:", e)
        return

    # rt_cd가 "0"이면 정상 주문
    if data.get("rt_cd") == "0":
        print(f"✅ {stock_code} {qty}주 매수 주문 성공!")
        out = data.get("output", {})
        print("주문번호:", out.get("ODNO", "없음"))
    else:
        print("❌ 주문 실패:", data.get("msg1", data))


# —————————————————————————————
# main
# —————————————————————————————
if __name__ == "__main__":
    buy_stock("000660", 3)   # 예: 현대차 주식 3주 매수
