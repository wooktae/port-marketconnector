"""KIS 국내 주식 주문 취소 wrapper.

원 주문 요청 ID를 기준으로 broker 주문 context를 찾아 취소 요청을 제출한다.
CLI 실행 시 외부 주문 취소 API 호출과 DB 상태 갱신이 발생할 수 있다.

수량 계약:
- 수량 생략(--qty 미지정)은 전량 취소로 처리한다(ORD_QTY="0", QTY_ALL_ORD_YN="Y").
- 수량 지정은 해당 수량만 부분 취소로 처리한다(QTY_ALL_ORD_YN="N").
- 실제 수량 분기와 payload 계약은 submit_rvsecncl_order()가 호출하는
  resolve_cancel_modify_quantity() 순수 함수가 담당한다.

전량 취소는 현재 TR_ID와 취소 구분 코드 RVSE_CNCL_DVSN_CD로 Paper 환경에서
동작 확인된 값이다. 실 환경(live) 검증 주장으로 확장하지 않는다.
"""

import argparse
from typing import Optional

from connector_order_common import submit_rvsecncl_order
from connector_locale import t

SOURCE_VERSION = "connector-order-cancel-1.0.0"
API_NAME = "order-cancel"

# Full-cancellation payload contract:
#   TR_ID and RVSE_CNCL_DVSN_CD are values whose full-cancellation behavior was
#   confirmed in the Paper environment. Do not extend this into a live-environment
#   validation claim; re-confirm against the KIS documentation before live use.
TR_ID = "VTTC0803U"
RVSE_CNCL_DVSN_CD = "02"   # Cancellation division code. Based on Paper-environment behavior confirmation


def cancel_order(
    original_order_request_id: int,
    qty: Optional[int] = None,
    reason: Optional[str] = None,
):
    """원 주문 요청 ID 기준으로 취소 주문을 제출한다.

    qty를 생략하면 전량 취소, qty를 지정하면 부분 취소로 처리한다.
    """
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
    parser = argparse.ArgumentParser(
        description="KIS 주문 취소 (수량 생략 시 전량 취소, 수량 지정 시 부분 취소)"
    )
    parser.add_argument("--order-request-id", type=int, required=True, help="원 주문 request id")
    parser.add_argument(
        "--qty",
        type=int,
        default=None,
        help="취소 수량. 생략 시 전량 취소, 지정 시 부분 취소",
    )
    parser.add_argument("--reason", default=None, help="취소 사유")
    parser.add_argument("--yes", action="store_true", help="실제 취소 실행 확인")

    args = parser.parse_args()

    if not args.yes:
        print(t("❌ Real cancel order blocked: add --yes to execute", "❌ 실제 취소 주문 차단: 실행하려면 --yes를 붙여줘"))
        raise SystemExit(1)

    result = cancel_order(
        original_order_request_id=args.order_request_id,
        qty=args.qty,
        reason=args.reason,
    )
    print(result)
