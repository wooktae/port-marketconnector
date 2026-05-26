import requests
from token_manager import get_access_token, check_and_refresh_token
from config import APP_KEY, APP_SECRET, BASE_URL

def get_closed_prices(
    stock_code: str,
    period_div: str = "D",
    start_date: str = "",
    end_date: str = ""
):
    print(f"\n📆 기간별 시세 조회: {stock_code}, 기간: {period_div}")

    token = get_access_token()
    if not token:
        print("❌ 토큰 없음")
        return None

    url = f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Authorization": f"Bearer {token}",
        "appKey": APP_KEY,
        "appSecret": APP_SECRET,
        "tr_id": "FHKST03010100",
        "custtype": "P"
    }
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": stock_code,
        "FID_INPUT_DATE_1": start_date,
        "FID_INPUT_DATE_2": end_date,
        "FID_PERIOD_DIV_CODE": period_div,
        "FID_ORG_ADJ_PRC": "1",   # 원가 기준
    }

    try:
        res = requests.get(url, headers=headers, params=params)
    except Exception as e:
        print("❌ 요청 에러:", e)
        return None

    new_tok = check_and_refresh_token(res.text)
    if new_tok:
        print("🔄 토큰 오류 -> 재발급 후 재요청")
        headers["Authorization"] = f"Bearer {new_tok}"
        res = requests.get(url, headers=headers, params=params)

    try:
        data = res.json()
    except Exception as e:
        print("❌ JSON 파싱 실패:", e)
        return None

    # 실제 데이터는 output2에 있음
    prices = data.get("output2")
    if isinstance(prices, list) and prices:
        print(f"✅ 기간별 시세 조회 성공 (총 {len(prices)}건)")
        return prices

    print("❌ 조회 실패:", data)
    return None


if __name__ == "__main__":
    # 예: 20260101 ~ 20260131 사이 일봉 데이터
    daily = get_closed_prices("005930", "D", "20260101", "20260131")
    if daily:
        for item in daily[:5]:
            print(item)
