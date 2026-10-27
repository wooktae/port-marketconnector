"""KIS domestic stock cash sell order wrapper.

Passes the sell TR_ID and order input values to the common order submission module.
CLI execution can trigger broker order submission and DB order-request saving.
"""

import argparse
from typing import Optional

from connector_order_common import submit_cash_order
from connector_locale import t

SOURCE_VERSION = "connector-order-sell-1.0.0"
API_NAME = "order-cash-sell"

# Important:
# This can differ based on your KIS paper/live trading documentation.
# If the cash sell TR_ID differs, change only the value below.
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
    """Delegates the sell order request to the common order submission flow."""
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
        print(t("❌ Real sell order blocked: add --yes to execute", "❌ 실제 매도 주문 차단: 실행하려면 --yes를 붙여줘"))
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
