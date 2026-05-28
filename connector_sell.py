"""KIS 국내 주식 현금 매도 주문 wrapper.

공통 주문 제출 모듈에 매도 TR_ID와 주문 입력값을 전달한다.
CLI 실행 시 브로커 주문 제출과 DB 주문 요청 저장이 발생할 수 있다.
"""

import argparse
from typing import Optional

from connector_order_common import submit_cash_order

SOURCE_VERSION = "connector-order-sell-1.0.0"
API_NAME = "order-cash-sell"

# 중요:
# 네 KIS 모의투자/실전 문서 기준으로 다를 수 있어.
# 현금 매도 TR_ID가 다르면 아래 값만 바꾸면 돼.
TR_ID = "VTTC0801U"


def sell_stock(
    stock_code: str,
    qty: int = 1,
    order_method: str = "MARKET",
    order_price: Optional[float] = None,
    stock_name: Optional[str] = None,
    parent_order_request_id: Optional[int] = None,
    strategy_name: Optional[str] = None,
    strategy_version: Optional[str] = None,
    strategy_run_id: Optional[str] = None,
    strategy_signal_id: Optional[int] = None,
    signal_date: Optional[str] = None,
    signal_type: Optional[str] = None,
    signal_score: Optional[float] = None,
    signal_position_size: Optional[float] = None,
):
    """매도 주문 요청을 공통 주문 제출 흐름으로 위임한다."""
    return submit_cash_order(
        request_type="SELL",
        api_name=API_NAME,
        tr_id=TR_ID,
        stock_code=stock_code,
        qty=qty,
        order_method=order_method,
        order_price=order_price,
        stock_name=stock_name,
        parent_order_request_id=parent_order_request_id,
        strategy_name=strategy_name,
        strategy_version=strategy_version,
        strategy_run_id=strategy_run_id,
        strategy_signal_id=strategy_signal_id,
        signal_date=signal_date,
        signal_type=signal_type or "SELL",
        signal_score=signal_score,
        signal_position_size=signal_position_size,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="KIS 현금 매도 주문")
    parser.add_argument("--code", required=True, help="종목코드. 예: 005930")
    parser.add_argument("--qty", type=int, required=True, help="주문 수량")
    parser.add_argument("--method", default="MARKET", choices=["MARKET", "LIMIT"], help="주문 방식")
    parser.add_argument("--price", type=float, default=None, help="지정가 주문 가격")
    parser.add_argument("--name", default=None, help="종목명")
    parser.add_argument("--parent-id", type=int, default=None, help="연결할 원 주문 request id")
    parser.add_argument("--yes", action="store_true", help="실제 주문 실행 확인")

    args = parser.parse_args()

    if not args.yes:
        print("❌ 실제 매도 주문 차단: 실행하려면 --yes를 붙여줘")
        raise SystemExit(1)

    result = sell_stock(
        stock_code=args.code,
        qty=args.qty,
        order_method=args.method,
        order_price=args.price,
        stock_name=args.name,
        parent_order_request_id=args.parent_id,
    )
    print(result)
