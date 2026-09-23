"""KIS period quote query and EOD quote save flow.

Calls the per-period daily-candle API and, depending on the option, upserts into the connector_quote_eod table.
Even when `--no-save` is used, external API calls and token handling can still occur.
"""

import argparse
import time
from datetime import datetime, timedelta

import pandas as pd
import requests

from config import APP_KEY, APP_SECRET, BASE_URL
from connector_db import insert_api_call_log, to_jsonb, upsert_connector_quote_eod
from token_manager import check_and_refresh_token, get_access_token

SOURCE_VERSION = "connector-quote-eod-1.0.0"
API_NAME = "inquire-daily-itemchartprice"
TR_ID = "FHKST03010100"
ENDPOINT = "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"


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


def get_closed_prices(
    stock_code: str,
    period_div: str = "D",
    start_date: str = "",
    end_date: str = "",
    save_db: bool = True,
):
    """Queries period quotes and saves EOD quotes to the DB depending on the request option."""
    print(f"\n📆 기간별 시세 조회: {stock_code}, 기간: {period_div}")

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
        "FID_INPUT_DATE_1": start_date,
        "FID_INPUT_DATE_2": end_date,
        "FID_PERIOD_DIV_CODE": period_div,
        "FID_ORG_ADJ_PRC": "1",
    }

    started = time.time()
    try:
        res = requests.get(url, headers=headers, params=params, timeout=20)
        latency_ms = int((time.time() - started) * 1000)
    except Exception as e:
        print("❌ 요청 에러:", e)
        return None

    new_tok = check_and_refresh_token(res.text)
    if new_tok:
        print("🔄 토큰 오류 -> 재발급 후 재요청")
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

    prices = data.get("output2")
    if isinstance(prices, list) and prices:
        print(f"✅ 기간별 시세 조회 성공 (총 {len(prices)}건)")

        if save_db:
            rows = []
            for p in prices:
                trade_date_raw = p.get("stck_bsop_date", "")
                trade_date = pd.to_datetime(trade_date_raw).date() if trade_date_raw else None

                rows.append(
                    {
                        "ticker_code": stock_code,
                        "trade_date": trade_date,
                        "open_price": _to_float(p.get("stck_oprc")),
                        "high_price": _to_float(p.get("stck_hgpr")),
                        "low_price": _to_float(p.get("stck_lwpr")),
                        "close_price": _to_float(p.get("stck_clpr")),
                        "volume": _to_int(p.get("acml_vol")),
                        "trading_value": _to_float(p.get("acml_tr_pbmn")),
                        "period_div": period_div,
                        "adjusted_price_yn": True,
                        "raw_json": to_jsonb(p),
                        "source_api": API_NAME,
                        "source_version": SOURCE_VERSION,
                    }
                )

            upsert_connector_quote_eod(rows)
            print(f"✅ connector_quote_eod 저장 완료 ({len(rows)}건)")

        return prices

    print("❌ 조회 실패:", data)
    return None


if __name__ == "__main__":
    today = datetime.now().strftime("%Y%m%d")
    default_start = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")

    parser = argparse.ArgumentParser(
        description="KIS 기간별 시세 조회 후 connector_quote_eod 저장"
    )
    parser.add_argument("--code", required=True, help="종목코드. 예: 005930")
    parser.add_argument("--period", default="D", choices=["D", "W", "M", "Y"], help="기간 구분")
    parser.add_argument("--start", default=default_start, help="조회 시작일 YYYYMMDD")
    parser.add_argument("--end", default=today, help="조회 종료일 YYYYMMDD")
    parser.add_argument("--no-save", action="store_true", help="DB 저장 없이 조회만 수행")

    args = parser.parse_args()

    daily = get_closed_prices(
        stock_code=args.code,
        period_div=args.period,
        start_date=args.start,
        end_date=args.end,
        save_db=not args.no_save,
    )

    if daily:
        print(f"\n✅ 조회 완료: {args.code}, count={len(daily)}")
        for item in daily[:5]:
            print(item)
