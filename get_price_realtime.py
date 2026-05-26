import requests
from token_manager import get_access_token, check_and_refresh_token
from config import APP_KEY, APP_SECRET, BASE_URL

# —————————————————————————————
# 실시간 시세 조회 함수
# —————————————————————————————
def get_stock_price(stock_code: str):
    """
    국내 주식 실시간 시세 조회
    :param stock_code: 종목 코드 (예: "005930")
    :return: 시세 딕셔너리 또는 None
    """
    print(f"\n📊 실시간 시세 조회 시작: {stock_code}")

    # 1) 엑세스 토큰 가져오기
    token = get_access_token()
    if not token:
        print("❌ 토큰 없음")
        return None

    # 2) REST 요청
    url = f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price"
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Authorization": f"Bearer {token}",
        "appKey": APP_KEY,
        "appSecret": APP_SECRET,
        # 시세조회용 TR_ID
        "tr_id": "FHKST01010100",
        "custtype": "P"
    }
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",  # 전체 시장
        "FID_INPUT_ISCD": stock_code
    }

    # 3) 첫 번째 요청
    try:
        res = requests.get(url, headers=headers, params=params)
    except Exception as e:
        print("❌ 시세 요청 에러:", e)
        return None

    # 4) 토큰 오류 자동 감지 & 재발급 + 재요청
    new_tok = check_and_refresh_token(res.text)
    if new_tok:
        print("🔄 토큰 오류 감지 → 재발급 + 재요청")
        headers["Authorization"] = f"Bearer {new_tok}"
        res = requests.get(url, headers=headers, params=params)

    # 5) JSON 파싱
    try:
        data = res.json()
    except Exception as e:
        print("❌ JSON 파싱 실패:", e)
        return None

    # 6) 응답 상태 체크
    if data.get("rt_cd") == "0":
        quote = data.get("output", {})

        # 핵심 값 리턴
        return {
            "current_price": quote.get("stck_prpr"),   # 현재가
            "diff": quote.get("prdy_vrss"),            # 전일대비
            "volume": quote.get("acml_vol"),           # 거래량
            "open": quote.get("stck_oprc"),            # 시가
            "high": quote.get("stck_hgpr"),            # 고가
            "low": quote.get("stck_lwpr")              # 저가
        }
    else:
        print("❌ 조회 실패:", data)
        return None


# —————————————————————————————
# 테스트용 main
# —————————————————————————————
if __name__ == "__main__":
    stock = "005930"  # 삼성전자
    result = get_stock_price(stock)

    if result:
        print("👉 현재가:", result.get("current_price"))
        print("👉 전일대비:", result.get("diff"))
        print("👉 거래량:", result.get("volume"))
        print("👉 시가:", result.get("open"))
        print("👉 고가:", result.get("high"))
        print("👉 저가:", result.get("low"))
    else:
        print("❌ 시세를 못 받아왔어")
