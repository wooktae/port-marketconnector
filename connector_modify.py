"""KIS 국내 주식 주문 정정 wrapper.

원 주문 요청 ID를 기준으로 broker 주문 context를 찾아 정정 요청을 제출한다.
CLI 실행 시 외부 주문 정정 API 호출과 DB 상태 갱신이 발생할 수 있다.
"""

import argparse
from typing import Optional

from connector_order_common import submit_rvsecncl_order

SOURCE_VERSION = "connector-order-modify-1.0.0"
API_NAME = "order-modify"

# 중요:
# 아래 2개는 네 KIS 문서 기준으로 확인 필요
TR_ID = "VTTC0803U"
RVSE_CNCL_DVSN_CD = "01"   # 일반적으로 정정 코드로 많이 쓰는 값. 환경 문서 확인 필요


def modify_order(
    original_order_request_id: int,
    qty: Optional[int] = None,
    order_method: str = "LIMIT",
    order_price: Optional[float] = None,
    reason: Optional[str] = None,
):
    """원 주문 요청 ID 기준으로 정정 주문을 제출한다."""
    return submit_rvsecncl_order(
        action_type="MODIFY",
        api_name=API_NAME,
        tr_id=TR_ID,
        rvse_cncl_dvsn_cd=RVSE_CNCL_DVSN_CD,
        original_order_request_id=original_order_request_id,
        qty=qty,
        order_method=order_method,
        order_price=order_price,
        reason=reason,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="KIS 주문 정정")
    parser.add_argument("--order-request-id", type=int, required=True, help="원 주문 request id")
    parser.add_argument("--qty", type=int, default=None, help="정정 수량. 생략 시 기존 수량")
    parser.add_argument("--method", default="LIMIT", choices=["MARKET", "LIMIT"], help="정정 주문 방식")
    parser.add_argument("--price", type=float, default=None, help="정정 가격")
    parser.add_argument("--reason", default=None, help="정정 사유")
    parser.add_argument("--yes", action="store_true", help="실제 정정 실행 확인")

    args = parser.parse_args()

    if not args.yes:
        print("❌ 실제 정정 주문 차단: 실행하려면 --yes를 붙여줘")
        raise SystemExit(1)

    result = modify_order(
        original_order_request_id=args.order_request_id,
        qty=args.qty,
        order_method=args.method,
        order_price=args.price,
        reason=args.reason,
    )
    print(result)
