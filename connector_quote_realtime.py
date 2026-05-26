import json
import time
from datetime import datetime
import argparse

import requests

from config import APP_KEY, APP_SECRET, BASE_URL
from connector_db import insert_api_call_log, insert_connector_quote_realtime, to_jsonb
from token_manager import check_and_refresh_token, get_access_token

SOURCE_VERSION = "connector-quote-realtime-1.0.0"
API_NAME = "inquire-price"
TR_ID = "FHKST01010100"
ENDPOINT = "/uapi/domestic-stock/v1/quotations/inquire-price"


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


def get_stock_price(stock_code: str, save_db: bool = True):
    print(f"\n📊 실시간 시세 조회 시작: {stock_code}")

    token = get_access_token()
    if not token:
        print("❌ 토큰 없음")
        return None

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
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": stock_code,
    }

    started = time.time()
    try:
        res = requests.get(url, headers=headers, params=params, timeout=20)
        latency_ms = int((time.time() - started) * 1000)
    except Exception as e:
        print("❌ 시세 요청 에러:", e)
        return None

    new_tok = check_and_refresh_token(res.text)
    if new_tok:
        print("🔄 토큰 오류 감지 → 재발급 + 재요청")
        headers["Authorization"] = f"Bearer {new_tok}"
        res = requests.get(url, headers=headers, params=params, timeout=20)

    try:
        data = res.json()
    except Exception as e:
        print("❌ JSON 파싱 실패:", e)
        return None

    insert_api_call_log(
        account_id=None,
        api_category="QUOTE",
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

    if data.get("rt_cd") == "0":
        quote = data.get("output", {}) or {}

        result = {
            "ticker_code": stock_code,
            "quote_ts": datetime.now(),
            "current_price": _to_float(quote.get("stck_prpr")),
            "diff_price": _to_float(quote.get("prdy_vrss")),
            "diff_rate": _to_float(quote.get("prdy_ctrt")),
            "volume": _to_int(quote.get("acml_vol")),
            "open_price": _to_float(quote.get("stck_oprc")),
            "high_price": _to_float(quote.get("stck_hgpr")),
            "low_price": _to_float(quote.get("stck_lwpr")),
            "best_ask_price": _to_float(quote.get("askp1")),
            "best_bid_price": _to_float(quote.get("bidp1")),
            "expected_match_price": _to_float(quote.get("sstn_avls")),
            "expected_match_volume": _to_int(quote.get("sstn_vol")),
            "raw_json": to_jsonb(data),
            "source_api": API_NAME,
            "source_version": SOURCE_VERSION,
        }

        if save_db:
            insert_connector_quote_realtime(result)

        return {
            "code": stock_code,
            "current_price": result["current_price"],
            "diff": result["diff_price"],
            "diff_rate": result["diff_rate"],
            "volume": result["volume"],
            "open": result["open_price"],
            "high": result["high_price"],
            "low": result["low_price"],
            "best_ask": result["best_ask_price"],
            "best_bid": result["best_bid_price"],
            "raw": data,
        }

    print("❌ 조회 실패:", data)
    return None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="KIS 실시간 시세 조회 후 connector_quote_realtime 저장"
    )
    parser.add_argument("--code", required=True, help="종목코드. 예: 005930")
    parser.add_argument("--no-save", action="store_true", help="DB 저장 없이 조회만 수행")

    args = parser.parse_args()

    result = get_stock_price(args.code, save_db=not args.no_save)

    if result:
        print("👉 현재가:", result.get("current_price"))
        print("👉 전일대비:", result.get("diff"))
        print("👉 거래량:", result.get("volume"))
        print("👉 시가:", result.get("open"))
        print("👉 고가:", result.get("high"))
        print("👉 저가:", result.get("low"))