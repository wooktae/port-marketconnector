import argparse
from typing import Optional

from connector_order_common import submit_rvsecncl_order

SOURCE_VERSION = "connector-order-cancel-1.0.0"
API_NAME = "order-cancel"

# 중요:
# 아래 2개는 네 KIS 문서 기준으로 확인 필요
TR_ID = "VTTC0803U"
RVSE_CNCL_DVSN_CD = "02"   # 일반적으로 취소 코드로 많이 쓰는 값. 환경 문서 확인 필요


def cancel_order(
    original_order_request_id: int,
    qty: Optional[int] = None,
    reason: Optional[str] = None,
):
    return submit_rvsecncl_order(
        action_type="CANCEL",
        api_name=API_NAME,
        tr_id=TR_ID,
        rvse_cncl_dvsn_cd=RVSE_CNCL_DVSN_CD,
        original_order_request_id=original_order_request_id,
        qty=qty,
        order_method=None,
        order_price=None,
        reason=reason,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="KIS 주문 취소")
    parser.add_argument("--order-request-id", type=int, required=True, help="원 주문 request id")
    parser.add_argument("--qty", type=int, default=None, help="취소 수량. 생략 시 전량")
    parser.add_argument("--reason", default=None, help="취소 사유")
    parser.add_argument("--yes", action="store_true", help="실제 취소 실행 확인")

    args = parser.parse_args()

    if not args.yes:
        print("❌ 실제 취소 주문 차단: 실행하려면 --yes를 붙여줘")
        raise SystemExit(1)

    result = cancel_order(
        original_order_request_id=args.order_request_id,
        qty=args.qty,
        reason=args.reason,
    )
    print(result)