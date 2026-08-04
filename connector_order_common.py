"""매수/매도/취소/정정 주문 제출 공통 흐름.

주문 요청 DB 기록, 브로커 API 호출, 응답 반영, strategy mapping 저장을 담당한다.
호출 시 token 처리, 외부 주문 API 호출, DB 쓰기가 발생할 수 있다.
"""

import os
import time
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, NamedTuple, Optional, Tuple

import requests
from psycopg.rows import dict_row

from config import APP_KEY, APP_SECRET, BASE_URL, PAPER_ACNT, ACNT_PRDT_CD
from connector_db import (
    ensure_connector_account,
    get_conn,
    insert_api_call_log,
    insert_order_request,
    insert_signal_order_map,
    to_jsonb,
    update_order_request_after_response,
)
from token_manager import check_and_refresh_token, get_access_token


DEFAULT_ORDER_ENDPOINT = "/uapi/domestic-stock/v1/trading/order-cash"
DEFAULT_RVSE_CNCL_ENDPOINT = "/uapi/domestic-stock/v1/trading/order-rvsecncl"


class QuantityValidationError(ValueError):
    """주문 수량이 1 이상의 정수로 안전하게 정규화될 수 없을 때 발생한다."""


def normalize_order_qty(value: Any) -> int:
    """주문 수량을 1 이상의 양의 정수로 정규화한다.

    브로커 제출 경계에서 명백히 잘못된 수량을 차단하기 위한 순수 함수다.
    broker API 호출, DB 쓰기, token 처리 같은 부작용을 수행하지 않는다.

    허용:
        - 1 이상의 양의 정수(`int`)는 값 변경 없이 그대로 반환한다.
        - 정수와 정확히 동일한 `Decimal`·문자열은 정수로 변환해 허용한다.

    거부(`QuantityValidationError`):
        - 0, 음수.
        - 소수부가 있는 값(절사·반올림·축소하지 않는다).
        - `bool` 값(`bool`은 `int` 하위 타입이므로 정수 판정 이전에 먼저 거부한다).
        - 숫자로 해석할 수 없는 값.
    """
    # bool은 int 하위 타입이므로 정수 판정 이전에 먼저 거부한다.
    if type(value) is bool:
        raise QuantityValidationError(f"수량으로 bool 값은 허용하지 않아: {value!r}")

    if isinstance(value, int):
        qty = value
    elif isinstance(value, Decimal):
        if not value.is_finite():
            raise QuantityValidationError(f"수량이 유한한 값이 아니야: {value!r}")
        if value != value.to_integral_value():
            raise QuantityValidationError(f"수량에 소수부가 있어 정수로 정규화할 수 없어: {value!r}")
        qty = int(value)
    elif isinstance(value, float):
        # float는 정확한 정수 표현을 보장하지 못하므로 정수값일 때만 허용한다.
        if not value.is_integer():
            raise QuantityValidationError(f"수량에 소수부가 있어 정수로 정규화할 수 없어: {value!r}")
        qty = int(value)
    elif isinstance(value, str):
        text = value.strip()
        try:
            parsed = Decimal(text)
        except (InvalidOperation, ValueError):
            raise QuantityValidationError(f"수량을 숫자로 해석할 수 없어: {value!r}") from None
        if not parsed.is_finite():
            raise QuantityValidationError(f"수량이 유한한 값이 아니야: {value!r}")
        if parsed != parsed.to_integral_value():
            raise QuantityValidationError(f"수량에 소수부가 있어 정수로 정규화할 수 없어: {value!r}")
        qty = int(parsed)
    else:
        raise QuantityValidationError(f"수량을 숫자로 해석할 수 없어: {value!r}")

    if qty < 1:
        raise QuantityValidationError(f"수량은 1 이상이어야 해: {qty}")

    return qty


# Fatal_Max 비상 상한 환경변수. 전략 상한이 아니라 데이터 손상 방지용 비상 상한이다.
FATAL_MAX_ORDER_QTY_ENV = "STEP12_FATAL_MAX_ORDER_QTY"


class FatalMaxConfigError(Exception):
    """`STEP12_FATAL_MAX_ORDER_QTY` 구성값을 양의 정수 상한으로 해석할 수 없을 때 발생한다.

    미설정과 `0`(비활성)을 제외하고 음수·소수·비숫자처럼 양의 정수로 해석 불가한 값을
    조용히 무시하거나 검사를 비활성화하지 않고, 명시적 구성 오류로 신호하기 위한 예외다.
    `QuantityValidationError`와 별도 계열로 두어 수량 검증 오류와 구분한다.
    """


class FatalMaxExceededError(ValueError):
    """정규화된 주문 수량이 Fatal_Max 상한을 초과할 때 발생한다.

    상한 초과 수량을 상한으로 조정하지 않고 브로커 제출을 차단하기 위한 예외다.
    """


def resolve_fatal_max_order_qty() -> Optional[int]:
    """`STEP12_FATAL_MAX_ORDER_QTY` 환경변수를 Fatal_Max 상한으로 해석한다.

    이 함수는 호출 시점에만 환경변수를 읽는다. module import 시점에는 환경변수를
    읽지 않으므로 import-time 부작용이 없다. broker API 호출·DB 쓰기·token 처리도
    수행하지 않는다.

    반환:
        - 미설정(또는 빈 문자열)이면 검사 비활성으로 보고 `None`을 반환한다.
        - 값이 `0`이면 검사 비활성으로 보고 `None`을 반환한다.
        - 양의 정수이면 그 값을 상한으로 반환한다.

    예외(`FatalMaxConfigError`):
        - 미설정·`0`을 제외하고 양의 정수로 해석할 수 없는 값(음수·소수·비숫자).
    """
    raw = os.environ.get(FATAL_MAX_ORDER_QTY_ENV)
    if raw is None:
        return None

    text = raw.strip()
    if text == "":
        # 빈 문자열은 미설정과 동일하게 검사 비활성으로 취급한다.
        return None

    try:
        parsed = Decimal(text)
    except (InvalidOperation, ValueError):
        raise FatalMaxConfigError(
            f"{FATAL_MAX_ORDER_QTY_ENV} 값을 양의 정수로 해석할 수 없어: {raw!r}"
        ) from None

    if not parsed.is_finite():
        raise FatalMaxConfigError(
            f"{FATAL_MAX_ORDER_QTY_ENV} 값이 유한한 정수가 아니야: {raw!r}"
        )
    if parsed != parsed.to_integral_value():
        raise FatalMaxConfigError(
            f"{FATAL_MAX_ORDER_QTY_ENV} 값에 소수부가 있어 정수 상한으로 쓸 수 없어: {raw!r}"
        )

    limit = int(parsed)
    if limit == 0:
        # 0은 검사 비활성을 의미한다.
        return None
    if limit < 0:
        raise FatalMaxConfigError(
            f"{FATAL_MAX_ORDER_QTY_ENV} 값은 음수일 수 없어: {raw!r}"
        )

    return limit


def check_fatal_max_order_qty(qty: int, limit: Optional[int]) -> int:
    """정규화된 수량이 Fatal_Max 상한 이하인지 검사한다.

    상한을 초과해도 수량을 상한으로 조정·축소하지 않고 `FatalMaxExceededError`를
    발생시킨다. 상한 이하이거나 상한이 비활성(`None`)이면 `qty`를 값 변경 없이
    그대로 반환한다. broker API 호출·DB 쓰기 같은 부작용은 수행하지 않는다.

    Args:
        qty: `normalize_order_qty`로 정규화된 1 이상의 양의 정수 수량.
        limit: `resolve_fatal_max_order_qty` 결과. `None`이면 검사 비활성.
    """
    if limit is None:
        return qty
    if qty > limit:
        raise FatalMaxExceededError(
            f"수량 {qty}가 Fatal_Max 상한 {limit}을 초과했어(상한으로 조정하지 않고 차단)"
        )
    return qty


class CancelModifyPayloadError(ValueError):
    """CANCEL/MODIFY 수량 분기가 KIS payload 계약을 위반할 때 발생한다.

    전량/부분 취소와 정정의 수량 계약(0·음수·소수·활성 수량 초과·양의 정수가 아닌 정정 수량)을
    브로커 호출 이전에 차단하기 위한 예외다. `QuantityValidationError`와 별도 계열로 두어
    취소·정정 payload 계약 위반을 다른 수량 검증 오류와 구분한다.
    """


def resolve_cancel_modify_quantity(
    action_type: str,
    qty: Any = None,
    active_order_qty: Any = None,
) -> Tuple[str, str]:
    """CANCEL/MODIFY 요청의 수량 분기를 계산하는 순수 함수.

    `submit_rvsecncl_order()`의 인라인 수량 분기를 부작용 없는 순수 함수로 분리한 것이다.
    broker API 호출, DB 쓰기, token 처리 같은 부작용을 수행하지 않으며, 동일 입력에 대해
    항상 동일한 payload 또는 동일한 오류를 결정적으로 반환한다.

    Args:
        action_type: `"CANCEL"` 또는 `"MODIFY"`(대소문자 무관). 그 외 값은 오류다.
        qty: 요청 수량. CANCEL에서 `None`이면 전량 취소, 지정되면 부분 취소 수량이다.
            MODIFY에서 `None`이면 활성 주문 수량을 그대로 사용한다.
        active_order_qty: Active_Order(정정/취소 대상 활성 주문)의 기존 수량.

    Returns:
        `(ord_qty, qty_all_ord_yn)` 문자열 튜플. KIS payload의 `ORD_QTY`와
        `QTY_ALL_ORD_YN`에 그대로 대입할 수 있는 형태다.

        - CANCEL, qty 생략(None) → `("0", "Y")`(전량 취소).
        - CANCEL, 1 이상 active_order_qty 이하 정수 → `(str(qty), "N")`(부분 취소).
        - MODIFY, 1 이상 양의 정수 → `(str(qty), "N")`.
        - MODIFY, qty 생략(None) → `(str(active_order_qty), "N")`.

    Raises:
        CancelModifyPayloadError:
            - action_type이 CANCEL/MODIFY가 아닐 때.
            - CANCEL 부분 취소 수량이 0·음수·소수이거나 active_order_qty를 초과할 때.
            - MODIFY 정정 수량(또는 생략 시 active_order_qty)이 양의 정수가 아닐 때.
    """
    normalized_action = str(action_type).upper()
    if normalized_action not in ("CANCEL", "MODIFY"):
        raise CancelModifyPayloadError(
            f"resolve_cancel_modify_quantity는 CANCEL 또는 MODIFY만 지원해: {action_type!r}"
        )

    def _positive_int(value: Any, label: str) -> int:
        # 수량 정규화·검증(C1)과 동일한 의미(0·음수·소수·bool·비숫자 거부)를 재사용하되,
        # 취소·정정 payload 계약 위반은 CancelModifyPayloadError로 신호한다.
        try:
            return normalize_order_qty(value)
        except QuantityValidationError as exc:
            raise CancelModifyPayloadError(f"{label}이(가) 1 이상의 정수가 아니야: {value!r}") from exc

    if normalized_action == "CANCEL":
        if qty is None:
            # 전량 취소: ORD_QTY=0, QTY_ALL_ORD_YN=Y
            return "0", "Y"
        cancel_qty = _positive_int(qty, "부분 취소 수량")
        active_qty = _positive_int(active_order_qty, "활성 주문 수량")
        if cancel_qty > active_qty:
            raise CancelModifyPayloadError(
                f"부분 취소 수량 {cancel_qty}가 활성 주문 수량 {active_qty}를 초과했어"
            )
        # 부분 취소: ORD_QTY=취소 수량, QTY_ALL_ORD_YN=N
        return str(cancel_qty), "N"

    # MODIFY: 수량 생략 시 활성 주문 수량을 그대로 사용한다.
    if qty is None:
        modify_qty = _positive_int(active_order_qty, "활성 주문 수량")
    else:
        modify_qty = _positive_int(qty, "정정 수량")
    return str(modify_qty), "N"


def normalize_order_method(
    order_method: str,
    order_price: Optional[float] = None,
):
    method = (order_method or "MARKET").upper()

    if method == "MARKET":
        return "01", "0", "MARKET"

    if method == "LIMIT":
        if order_price is None:
            raise ValueError("LIMIT 주문은 order_price가 필요해")
        return "00", str(int(float(order_price))), "LIMIT"

    raise ValueError(f"지원하지 않는 order_method야: {order_method}")


def load_order_request_for_action(order_request_id: int) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT
                    id,
                    account_id,
                    account_no,
                    ticker_code,
                    stock_name,
                    request_type,
                    order_method,
                    order_price,
                    order_qty,
                    requested_amount,
                    parent_order_request_id,
                    strategy_name,
                    strategy_version,
                    strategy_run_id,
                    strategy_signal_id,
                    signal_date,
                    signal_type,
                    signal_score,
                    signal_position_size,
                    request_status,
                    broker_order_no,
                    broker_branch_code,
                    rejection_code,
                    rejection_message,
                    request_payload,
                    response_payload,
                    requested_at,
                    accepted_at,
                    last_event_at,
                    created_at,
                    updated_at
                FROM connector_order_request
                WHERE id = %s
                """,
                (order_request_id,),
            )
            row = cur.fetchone()

    return dict(row) if row else None


def load_latest_order_request_by_broker_order(
    broker_order_no: str,
    broker_branch_code: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    if not broker_order_no:
        return None

    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT
                    id,
                    account_id,
                    account_no,
                    ticker_code,
                    stock_name,
                    request_type,
                    order_method,
                    order_price,
                    order_qty,
                    requested_amount,
                    parent_order_request_id,
                    strategy_name,
                    strategy_version,
                    strategy_run_id,
                    strategy_signal_id,
                    signal_date,
                    signal_type,
                    signal_score,
                    signal_position_size,
                    request_status,
                    broker_order_no,
                    broker_branch_code,
                    rejection_code,
                    rejection_message,
                    request_payload,
                    response_payload,
                    requested_at,
                    accepted_at,
                    last_event_at,
                    created_at,
                    updated_at
                FROM connector_order_request
                WHERE broker_order_no = %s
                  AND (
                        broker_branch_code = %s
                        OR %s IS NULL
                        OR broker_branch_code IS NULL
                  )
                ORDER BY requested_at DESC, id DESC
                LIMIT 1
                """,
                (broker_order_no, broker_branch_code, broker_branch_code),
            )
            row = cur.fetchone()

    return dict(row) if row else None

def resolve_active_order_for_action(order_request_id: int) -> Optional[Dict[str, Any]]:
    """
    정정/취소 대상이 되는 '현재 활성 주문'을 찾는다.

    사용자가 원 주문 id를 넣어도,
    그 원 주문에서 파생된 성공 MODIFY 주문이 있으면
    가장 마지막 ACCEPTED MODIFY 주문을 실제 정정/취소 대상으로 사용한다.

    예:
    id=5 BUY     broker_order_no=0000029249
    id=6 MODIFY  broker_order_no=0000029474

    cancel_order(5)를 호출해도 실제 API에는 0000029474를 보낸다.
    """
    original = load_order_request_for_action(order_request_id)
    if not original:
        return None

    # 이미 직접 MODIFY row를 넘긴 경우에도,
    # 그 MODIFY의 후속 MODIFY가 있을 수 있으므로 같은 로직을 탄다.
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                WITH RECURSIVE order_chain AS (
                    SELECT
                        id,
                        account_id,
                        account_no,
                        ticker_code,
                        stock_name,
                        request_type,
                        order_method,
                        order_price,
                        order_qty,
                        requested_amount,
                        parent_order_request_id,
                        strategy_name,
                        strategy_version,
                        strategy_run_id,
                        strategy_signal_id,
                        signal_date,
                        signal_type,
                        signal_score,
                        signal_position_size,
                        request_status,
                        broker_order_no,
                        broker_branch_code,
                        rejection_code,
                        rejection_message,
                        request_payload,
                        response_payload,
                        requested_at,
                        accepted_at,
                        last_event_at,
                        created_at,
                        updated_at,
                        0 AS depth
                    FROM connector_order_request
                    WHERE id = %s

                    UNION ALL

                    SELECT
                        c.id,
                        c.account_id,
                        c.account_no,
                        c.ticker_code,
                        c.stock_name,
                        c.request_type,
                        c.order_method,
                        c.order_price,
                        c.order_qty,
                        c.requested_amount,
                        c.parent_order_request_id,
                        c.strategy_name,
                        c.strategy_version,
                        c.strategy_run_id,
                        c.strategy_signal_id,
                        c.signal_date,
                        c.signal_type,
                        c.signal_score,
                        c.signal_position_size,
                        c.request_status,
                        c.broker_order_no,
                        c.broker_branch_code,
                        c.rejection_code,
                        c.rejection_message,
                        c.request_payload,
                        c.response_payload,
                        c.requested_at,
                        c.accepted_at,
                        c.last_event_at,
                        c.created_at,
                        c.updated_at,
                        oc.depth + 1 AS depth
                    FROM connector_order_request c
                    JOIN order_chain oc
                      ON c.parent_order_request_id = oc.id
                    WHERE c.request_type = 'MODIFY'
                      AND c.request_status = 'ACCEPTED'
                      AND c.broker_order_no IS NOT NULL
                )
                SELECT *
                FROM order_chain
                WHERE request_status = 'ACCEPTED'
                  AND broker_order_no IS NOT NULL
                  AND request_type IN ('BUY', 'SELL', 'MODIFY')
                ORDER BY depth DESC, accepted_at DESC NULLS LAST, id DESC
                LIMIT 1
                """,
                (order_request_id,),
            )
            row = cur.fetchone()

    return dict(row) if row else original

def _create_order_request_record(
    *,
    account_id: int,
    account_no: str,
    stock_code: str,
    stock_name: Optional[str],
    request_type: str,
    order_method: str,
    order_price: Optional[float],
    qty: int,
    parent_order_request_id: Optional[int],
    strategy_name: Optional[str],
    strategy_version: Optional[str],
    strategy_run_id: Optional[str],
    strategy_signal_id: Optional[int],
    signal_date: Optional[str],
    signal_type: Optional[str],
    signal_score: Optional[float],
    signal_position_size: Optional[float],
    request_payload: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "account_id": account_id,
        "account_no": account_no,
        "ticker_code": stock_code,
        "stock_name": stock_name,
        "request_type": request_type,
        "order_method": order_method,
        "order_price": order_price,
        "order_qty": qty,
        "requested_amount": (qty * order_price) if order_price is not None else None,
        "parent_order_request_id": parent_order_request_id,
        "strategy_name": strategy_name,
        "strategy_version": strategy_version,
        "strategy_run_id": strategy_run_id,
        "strategy_signal_id": strategy_signal_id,
        "signal_date": signal_date,
        "signal_type": signal_type,
        "signal_score": signal_score,
        "signal_position_size": signal_position_size,
        "request_status": "PENDING",
        "broker_order_no": None,
        "broker_branch_code": None,
        "rejection_code": None,
        "rejection_message": None,
        "request_payload": to_jsonb(request_payload),
        "response_payload": None,
        "requested_at": datetime.now(),
        "accepted_at": None,
        "last_event_at": None,
    }


def _insert_signal_map_if_needed(
    *,
    strategy_signal_id: Optional[int],
    order_request_id: int,
    strategy_name: Optional[str],
    strategy_version: Optional[str],
    strategy_run_id: Optional[str],
    signal_date: Optional[str],
    stock_code: str,
    signal_type: Optional[str],
    qty: int,
    order_price: Optional[float],
    signal_position_size: Optional[float],
    source: str,
    order_method: str,
):
    if strategy_signal_id is None:
        return

    insert_signal_order_map(
        {
            "strategy_signal_id": strategy_signal_id,
            "order_request_id": order_request_id,
            "strategy_name": strategy_name or "strategy_ai",
            "strategy_version": strategy_version,
            "strategy_run_id": strategy_run_id,
            "signal_date": signal_date,
            "ticker_code": stock_code,
            "signal_type": signal_type,
            "target_qty": qty,
            "target_price": order_price,
            "target_weight": signal_position_size,
            "execution_decision": to_jsonb(
                {
                    "source": source,
                    "qty": qty,
                    "order_method": order_method,
                }
            ),
        }
    )


def _request_api(
    *,
    account_id: int,
    api_category: str,
    api_name: str,
    http_method: str,
    endpoint: str,
    tr_id: str,
    request_payload: Dict[str, Any],
):
    token = get_access_token()
    if not token:
        print("❌ 토큰 없음")
        return None, None, None

    url = f"{BASE_URL}{endpoint}"

    def _send(tok: str):
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Bearer {tok}",
            "appKey": APP_KEY,
            "appSecret": APP_SECRET,
            "tr_id": tr_id,
            "custtype": "P",
        }

        started = time.time()
        try:
            if http_method.upper() == "POST":
                res = requests.post(url, headers=headers, json=request_payload, timeout=20)
            else:
                res = requests.get(url, headers=headers, params=request_payload, timeout=20)

            latency_ms = int((time.time() - started) * 1000)
            return res, latency_ms
        except Exception as e:
            insert_api_call_log(
                account_id=account_id,
                api_category=api_category,
                api_name=api_name,
                http_method=http_method.upper(),
                endpoint=endpoint,
                tr_id=tr_id,
                request_body=request_payload if http_method.upper() == "POST" else None,
                request_params=request_payload if http_method.upper() == "GET" else None,
                response_message=str(e),
                is_success=False,
            )
            print("❌ API 요청 에러:", e)
            return None, None

    res, latency_ms = _send(token)
    if res is None:
        return None, None, None

    new_tok = check_and_refresh_token(res.text)
    if new_tok:
        print("🔄 토큰 오류 감지 → 재발급 + 재요청")
        res, latency_ms = _send(new_tok)
        if res is None:
            return None, None, None

    try:
        data = res.json()
    except Exception as e:
        print("❌ JSON 파싱 실패:", e)
        return res, {"rt_cd": "-1", "msg_cd": "JSON_PARSE_ERROR", "msg1": str(e)}, latency_ms

    insert_api_call_log(
        account_id=account_id,
        api_category=api_category,
        api_name=api_name,
        http_method=http_method.upper(),
        endpoint=endpoint,
        tr_id=tr_id,
        request_body=request_payload if http_method.upper() == "POST" else None,
        request_params=request_payload if http_method.upper() == "GET" else None,
        response_status=res.status_code,
        response_code=data.get("rt_cd"),
        response_message=data.get("msg1"),
        response_body=data,
        is_success=(data.get("rt_cd") == "0"),
        latency_ms=latency_ms,
    )
    return res, data, latency_ms


def _finalize_order_request_from_response(
    *,
    order_request_id: int,
    data: Dict[str, Any],
):
    out = data.get("output", {}) or {}
    broker_order_no = out.get("ODNO")
    broker_branch_code = out.get("KRX_FWDG_ORD_ORGNO")
    now_ts = datetime.now()

    if data.get("rt_cd") == "0":
        update_order_request_after_response(
            order_request_id,
            {
                "request_status": "ACCEPTED",
                "broker_order_no": broker_order_no,
                "broker_branch_code": broker_branch_code,
                "rejection_code": None,
                "rejection_message": None,
                "response_payload": to_jsonb(data),
                "accepted_at": now_ts,
                "last_event_at": now_ts,
            },
        )
        return True, broker_order_no, broker_branch_code

    update_order_request_after_response(
        order_request_id,
        {
            "request_status": "REJECTED",
            "broker_order_no": broker_order_no,
            "broker_branch_code": broker_branch_code,
            "rejection_code": data.get("msg_cd"),
            "rejection_message": data.get("msg1"),
            "response_payload": to_jsonb(data),
            "accepted_at": None,
            "last_event_at": now_ts,
        },
    )
    return False, broker_order_no, broker_branch_code


def submit_cash_order(
    *,
    request_type: str,
    api_name: str,
    tr_id: str,
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
    endpoint: str = DEFAULT_ORDER_ENDPOINT,
):
    """현금 매수/매도 주문을 DB에 기록한 뒤 브로커 주문 API로 제출한다."""
    request_type = request_type.upper()
    if request_type not in ("BUY", "SELL"):
        raise ValueError("submit_cash_order는 BUY 또는 SELL만 지원해")

    if qty <= 0:
        raise ValueError("qty는 1 이상이어야 해")

    ord_dvsn, ord_unpr, normalized_method = normalize_order_method(order_method, order_price)

    print(f"\n📌 {request_type} 주문 시작: {stock_code} {qty}주 ({normalized_method})")

    account_id = ensure_connector_account(
        account_no=PAPER_ACNT,
        account_product_code=ACNT_PRDT_CD,
        broker_name="koreainvestment",
        environment="paper",
        account_alias="main-paper",
    )

    request_payload = {
        "CANO": PAPER_ACNT,
        "ACNT_PRDT_CD": ACNT_PRDT_CD,
        "PDNO": stock_code,
        "ORD_DVSN": ord_dvsn,
        "ORD_QTY": str(qty),
        "ORD_UNPR": ord_unpr,
    }

    order_request_id = insert_order_request(
        _create_order_request_record(
            account_id=account_id,
            account_no=PAPER_ACNT,
            stock_code=stock_code,
            stock_name=stock_name,
            request_type=request_type,
            order_method=normalized_method,
            order_price=order_price,
            qty=qty,
            parent_order_request_id=parent_order_request_id,
            strategy_name=strategy_name,
            strategy_version=strategy_version,
            strategy_run_id=strategy_run_id,
            strategy_signal_id=strategy_signal_id,
            signal_date=signal_date,
            signal_type=signal_type or request_type,
            signal_score=signal_score,
            signal_position_size=signal_position_size,
            request_payload=request_payload,
        )
    )

    _insert_signal_map_if_needed(
        strategy_signal_id=strategy_signal_id,
        order_request_id=order_request_id,
        strategy_name=strategy_name,
        strategy_version=strategy_version,
        strategy_run_id=strategy_run_id,
        signal_date=signal_date,
        stock_code=stock_code,
        signal_type=signal_type or request_type,
        qty=qty,
        order_price=order_price,
        signal_position_size=signal_position_size,
        source=f"submit_cash_order:{request_type.lower()}",
        order_method=normalized_method,
    )

    res, data, _ = _request_api(
        account_id=account_id,
        api_category="ORDER",
        api_name=api_name,
        http_method="POST",
        endpoint=endpoint,
        tr_id=tr_id,
        request_payload=request_payload,
    )

    if res is None or data is None:
        update_order_request_after_response(
            order_request_id,
            {
                "request_status": "FAILED",
                "broker_order_no": None,
                "broker_branch_code": None,
                "rejection_code": None,
                "rejection_message": "HTTP request failed",
                "response_payload": to_jsonb({"error": "HTTP request failed"}),
                "accepted_at": None,
                "last_event_at": datetime.now(),
            },
        )
        return None

    ok, broker_order_no, broker_branch_code = _finalize_order_request_from_response(
        order_request_id=order_request_id,
        data=data,
    )

    if ok:
        print(f"✅ {request_type} 주문 성공")
    else:
        print(f"❌ {request_type} 주문 실패:", data.get("msg1", data))

    return {
        "order_request_id": order_request_id,
        "request_type": request_type,
        "broker_order_no": broker_order_no,
        "broker_branch_code": broker_branch_code,
        "response": data,
    }

def update_order_request_status_only(
    order_request_id: int,
    request_status: str,
    message: Optional[str] = None,
):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE connector_order_request
                   SET request_status = %s,
                       rejection_message = COALESCE(%s, rejection_message),
                       last_event_at = NOW(),
                       updated_at = NOW()
                 WHERE id = %s
                """,
                (request_status, message, order_request_id),
            )
        conn.commit()


# submit_rvsecncl_order() 경로 로컬 상태 단조성 보호의 Terminal 집합.
# 아래 조건부 UPDATE의 SQL IN 목록과 동일하게 유지해야 한다.
TERMINAL_REQUEST_STATUSES: Tuple[str, ...] = ("FILLED", "CANCELED", "REJECTED", "FAILED")


class RequestStatusTransition(NamedTuple):
    """`resolve_request_status_transition`의 판정 결과.

    Attributes:
        allowed: 갱신을 성공으로 취급할지 여부. 비-Terminal 갱신과 동일 Terminal 재적용
            (no-op)이면 True, Terminal 역행 차단이면 False다.
        result_status: 판정 후 결과 상태. 허용 시 요청 상태(동일 Terminal 재적용이면
            그 Terminal 상태), 차단 시 현재 Terminal 상태를 그대로 유지한다.
    """

    allowed: bool
    result_status: str


def resolve_request_status_transition(
    current_status: Optional[str],
    requested_status: str,
) -> RequestStatusTransition:
    """`submit_rvsecncl_order()` 경로의 로컬 주문 요청 상태 전이를 판정하는 순수 함수.

    Terminal 상태(`FILLED`, `CANCELED`, `REJECTED`, `FAILED`)에서 다른 상태로의 역행을
    차단한다. broker API 호출, DB 접근, token 처리 같은 부작용을 수행하지 않으며, 동일
    입력에 대해 항상 동일한 결과를 결정적으로 반환한다.

    Args:
        current_status: 현재 `request_status`. `None`은 비-Terminal로 취급한다.
        requested_status: 갱신 요청 상태.

    Returns:
        RequestStatusTransition:
            - 현재 상태가 비-Terminal → `allowed=True`, `result_status=requested_status`.
            - 현재 상태가 Terminal이고 요청 상태가 같음 → `allowed=True`(no-op),
              `result_status=current_status`.
            - 현재 상태가 Terminal이고 요청 상태가 다름 → `allowed=False`(차단),
              `result_status=current_status`.
    """
    if current_status in TERMINAL_REQUEST_STATUSES:
        if requested_status == current_status:
            # 동일 Terminal 재적용: 오류 없이 허용(no-op), 결과는 그 Terminal 상태 유지.
            return RequestStatusTransition(allowed=True, result_status=current_status)
        # Terminal 역행 차단: 성공 아님, 현재 Terminal 상태를 그대로 유지.
        return RequestStatusTransition(allowed=False, result_status=current_status)
    # 비-Terminal: 갱신 허용, 결과는 요청 상태.
    return RequestStatusTransition(allowed=True, result_status=requested_status)


def update_order_request_status_if_not_terminal(
    order_request_id: int,
    request_status: str,
    message: Optional[str] = None,
) -> bool:
    """Terminal 역행을 차단하는 조건부 UPDATE로 로컬 주문 요청 상태를 갱신한다.

    `resolve_request_status_transition`과 동일한 판정을 단일 원자적 조건부 UPDATE로
    수행한다. 현재 상태가 Terminal이고 요청 상태가 다르면 갱신하지 않고(0행) 차단하며,
    동일 Terminal 재적용은 오류 없이 허용한다. 이 보호는 `submit_rvsecncl_order()`
    경로의 로컬 상태 보호로만 정의하며, 대상 파일 밖 전체 주문 동기화의 단조성을
    보장한다고 주장하지 않는다.

    기존 `update_order_request_status_only()`는 다른 경로 호환을 위해 그대로 유지한다.

    Returns:
        갱신 성공 여부. 갱신(또는 동일 Terminal no-op)으로 1행 이상 반영되면 `True`,
        Terminal 역행 차단(0행)이면 차단 실패 지시로 `False`를 반환한다.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                # WHERE의 Terminal IN 목록은 TERMINAL_REQUEST_STATUSES와 동일하게 유지한다.
                """
                UPDATE connector_order_request
                   SET request_status = %s,
                       rejection_message = COALESCE(%s, rejection_message),
                       last_event_at = NOW(),
                       updated_at = NOW()
                 WHERE id = %s
                   AND (
                        request_status = %s
                        OR request_status NOT IN ('FILLED', 'CANCELED', 'REJECTED', 'FAILED')
                   )
                """,
                (request_status, message, order_request_id, request_status),
            )
            updated_rows = cur.rowcount
        conn.commit()
    return updated_rows >= 1


def apply_rvsecncl_parent_status_after_success(
    *,
    action_type: str,
    active_order_id: int,
    root_original_id: int,
):
    """
    정정/취소 주문 성공 후 원 주문/활성 주문 상태를 후처리한다.

    MODIFY 성공:
      - active_order_id가 원 주문이면 해당 원 주문을 MODIFIED 처리

    CANCEL 성공:
      - active_order_id, 즉 실제 취소 대상 주문을 CANCELED 처리
    """
    action_type = action_type.upper()

    # 후처리 상태 전이는 Terminal 역행 차단 가드를 통해 갱신한다.
    # 비-Terminal → MODIFIED, 비-Terminal → CANCELED 기존 정상 전이는 그대로 허용되고,
    # 이미 Terminal(FILLED/CANCELED/REJECTED/FAILED)인 활성 주문은 덮어쓰지 않는다.
    if action_type == "MODIFY":
        update_order_request_status_if_not_terminal(
            active_order_id,
            "MODIFIED",
            "modified by subsequent MODIFY request",
        )

    elif action_type == "CANCEL":
        update_order_request_status_if_not_terminal(
            active_order_id,
            "CANCELED",
            "canceled by subsequent CANCEL request",
        )



def submit_rvsecncl_order(
    *,
    action_type: str,
    api_name: str,
    tr_id: str,
    rvse_cncl_dvsn_cd: str,
    original_order_request_id: int,
    qty: Optional[int] = None,
    order_method: Optional[str] = None,
    order_price: Optional[float] = None,
    reason: Optional[str] = None,
    endpoint: str = DEFAULT_RVSE_CNCL_ENDPOINT,
):
    """기존 주문 context를 기준으로 취소/정정 요청을 제출하고 부모 주문 상태를 보정한다."""
    action_type = action_type.upper()
    if action_type not in ("CANCEL", "MODIFY"):
        raise ValueError("submit_rvsecncl_order는 CANCEL 또는 MODIFY만 지원해")

    root_original = load_order_request_for_action(original_order_request_id)
    if not root_original:
        raise ValueError("원주문을 찾을 수 없어")

    active_order = resolve_active_order_for_action(original_order_request_id)
    if not active_order:
        raise ValueError("정정/취소 대상 주문을 찾을 수 없어")

    if active_order["request_status"] in ("FILLED", "CANCELED", "REJECTED", "FAILED"):
        raise ValueError(f"이미 종료된 주문이라 {action_type} 할 수 없어")

    if not active_order.get("broker_order_no"):
        raise ValueError("정정/취소 대상 주문의 broker_order_no가 없어서 정정/취소를 할 수 없어")

    original_method = (active_order.get("order_method") or "MARKET").upper()
    effective_method = (order_method or original_method).upper()

    if action_type == "CANCEL":
        # 취소는 현재 활성 주문의 가격/방식을 기본 사용
        normalize_price = active_order.get("order_price")
        ord_dvsn, ord_unpr, normalized_method = normalize_order_method(original_method, normalize_price)
    else:
        effective_price = order_price if order_price is not None else active_order.get("order_price")
        ord_dvsn, ord_unpr, normalized_method = normalize_order_method(effective_method, effective_price)

    # KIS 정정·취소 계약:
    # - 전량 취소: ORD_QTY=0, QTY_ALL_ORD_YN=Y
    # - 부분 취소: ORD_QTY=취소 수량, QTY_ALL_ORD_YN=N
    # - MODIFY: 대상 수량을 명시(생략 시 활성 주문 수량 사용).
    #
    # 수량 분기는 부작용 없는 순수 함수 resolve_cancel_modify_quantity로 계산한다.
    # payload 미구성·브로커 미호출 계약을 위해 payload 구성·브로커 호출 이전에 먼저 호출한다.
    ord_qty_str, qty_all_order_yn = resolve_cancel_modify_quantity(
        action_type, qty, active_order.get("order_qty")
    )
    request_qty = int(ord_qty_str)

    request_payload = {
        "CANO": PAPER_ACNT,
        "ACNT_PRDT_CD": ACNT_PRDT_CD,
        "KRX_FWDG_ORD_ORGNO": active_order.get("broker_branch_code") or "",
        "ORGN_ODNO": active_order.get("broker_order_no"),
        "ORD_DVSN": ord_dvsn,
        "RVSE_CNCL_DVSN_CD": rvse_cncl_dvsn_cd,
        "ORD_QTY": str(request_qty),
        "ORD_UNPR": ord_unpr,
        "QTY_ALL_ORD_YN": qty_all_order_yn,
    }

    account_id = ensure_connector_account(
        account_no=PAPER_ACNT,
        account_product_code=ACNT_PRDT_CD,
        broker_name="koreainvestment",
        environment="paper",
        account_alias="main-paper",
    )

    order_request_id = insert_order_request(
        _create_order_request_record(
            account_id=account_id,
            account_no=PAPER_ACNT,
            stock_code=active_order["ticker_code"],
            stock_name=active_order.get("stock_name"),
            request_type=action_type,
            order_method=normalized_method,
            order_price=order_price if action_type == "MODIFY" else active_order.get("order_price"),
            qty=request_qty,
            parent_order_request_id=active_order["id"],
            strategy_name=active_order.get("strategy_name"),
            strategy_version=active_order.get("strategy_version"),
            strategy_run_id=active_order.get("strategy_run_id"),
            strategy_signal_id=active_order.get("strategy_signal_id"),
            signal_date=active_order.get("signal_date"),
            signal_type=action_type,
            signal_score=active_order.get("signal_score"),
            signal_position_size=active_order.get("signal_position_size"),
            request_payload={
                **request_payload,
                "_reason": reason,
                "_requested_original_order_request_id": original_order_request_id,
                "_resolved_active_order_request_id": active_order["id"],
                "_resolved_active_broker_order_no": active_order.get("broker_order_no"),
            },
        )
    )

    _insert_signal_map_if_needed(
        strategy_signal_id=active_order.get("strategy_signal_id"),
        order_request_id=order_request_id,
        strategy_name=active_order.get("strategy_name"),
        strategy_version=active_order.get("strategy_version"),
        strategy_run_id=active_order.get("strategy_run_id"),
        signal_date=active_order.get("signal_date"),
        stock_code=active_order["ticker_code"],
        signal_type=action_type,
        qty=request_qty,
        order_price=order_price if action_type == "MODIFY" else active_order.get("order_price"),
        signal_position_size=active_order.get("signal_position_size"),
        source=f"submit_rvsecncl_order:{action_type.lower()}",
        order_method=normalized_method,
    )

    res, data, _ = _request_api(
        account_id=account_id,
        api_category="ORDER",
        api_name=api_name,
        http_method="POST",
        endpoint=endpoint,
        tr_id=tr_id,
        request_payload=request_payload,
    )

    if res is None or data is None:
        update_order_request_after_response(
            order_request_id,
            {
                "request_status": "FAILED",
                "broker_order_no": None,
                "broker_branch_code": None,
                "rejection_code": None,
                "rejection_message": "HTTP request failed",
                "response_payload": to_jsonb({"error": "HTTP request failed"}),
                "accepted_at": None,
                "last_event_at": datetime.now(),
            },
        )
        return None

    ok, broker_order_no, broker_branch_code = _finalize_order_request_from_response(
    order_request_id=order_request_id,
    data=data,
    )

    if ok:
        # ---------------------------------------------------------
        # CANCEL 요청 row 자체는 일반 ACCEPTED가 아니라
        # "취소 요청 접수 성공"을 의미하는 CANCEL_ACCEPTED로 표현한다.
        #
        # active_order는 실제 취소 대상 주문이고,
        # order_request_id는 이번에 새로 생성된 CANCEL 요청 row다.
        # ---------------------------------------------------------
        if action_type == "CANCEL":
            # 비-Terminal → CANCEL_ACCEPTED 기존 정상 전이는 그대로 유지하되,
            # Terminal 역행 차단 가드를 통해 갱신한다.
            update_order_request_status_if_not_terminal(
                order_request_id,
                "CANCEL_ACCEPTED",
                "cancel request accepted by broker",
            )

        apply_rvsecncl_parent_status_after_success(
            action_type=action_type,
            active_order_id=active_order["id"],
            root_original_id=root_original["id"],
        )

        print(
            f"✅ {action_type} 요청 성공 "
            f"(requested_original={original_order_request_id}, active={active_order['id']})"
        )
    else:
        print(f"❌ {action_type} 요청 실패:", data.get("msg1", data))

    return {
        "order_request_id": order_request_id,
        "parent_order_request_id": active_order["id"],
        "requested_original_order_request_id": original_order_request_id,
        "resolved_active_order_request_id": active_order["id"],
        "resolved_active_broker_order_no": active_order.get("broker_order_no"),
        "request_type": action_type,
        "broker_order_no": broker_order_no,
        "broker_branch_code": broker_branch_code,
        "response": data,
    }
