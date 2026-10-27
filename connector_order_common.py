"""Common flow for submitting buy/sell/cancel/modify orders.

Responsible for recording order requests in the DB, calling the broker API, reflecting responses, and saving strategy mapping.
When called, token handling, external order API calls and DB writes can occur.
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
from connector_locale import t


DEFAULT_ORDER_ENDPOINT = "/uapi/domestic-stock/v1/trading/order-cash"
DEFAULT_RVSE_CNCL_ENDPOINT = "/uapi/domestic-stock/v1/trading/order-rvsecncl"


class QuantityValidationError(ValueError):
    """Raised when the order quantity cannot be safely normalized to an integer of 1 or greater."""


def normalize_order_qty(value: Any) -> int:
    """Normalizes the order quantity to a positive integer of 1 or greater.

    A pure function to block clearly invalid quantities at the broker submission boundary.
    It performs no side effects such as broker API calls, DB writes or token handling.

    Allowed:
        - A positive integer (`int`) of 1 or greater is returned unchanged.
        - A `Decimal` or string exactly equal to an integer is converted to an integer and allowed.

    Rejected (`QuantityValidationError`):
        - 0, negative numbers.
        - Values with a fractional part (not truncated, rounded or reduced).
        - `bool` values (since `bool` is a subtype of `int`, reject it before the integer check).
        - Values that cannot be interpreted as numbers.
    """
    # Since bool is a subtype of int, reject it before the integer check.
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
        # float does not guarantee exact integer representation, so allow it only when it is an integer value.
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


# Fatal_Max emergency cap environment variable. Not a strategy cap but an emergency cap to prevent data corruption.
FATAL_MAX_ORDER_QTY_ENV = "STEP12_FATAL_MAX_ORDER_QTY"


class FatalMaxConfigError(Exception):
    """Raised when the `STEP12_FATAL_MAX_ORDER_QTY` configuration value cannot be interpreted as a positive integer cap.

    Excluding unset and `0` (disabled), this exception signals an explicit configuration error rather than silently
    ignoring or disabling the check for values that cannot be interpreted as a positive integer, such as negative,
    fractional or non-numeric values. It is kept as a separate family from `QuantityValidationError` to distinguish
    it from quantity validation errors.
    """


class FatalMaxExceededError(ValueError):
    """Raised when the normalized order quantity exceeds the Fatal_Max cap.

    An exception to block broker submission rather than adjusting an over-cap quantity down to the cap.
    """


def resolve_fatal_max_order_qty() -> Optional[int]:
    """Interprets the `STEP12_FATAL_MAX_ORDER_QTY` environment variable as the Fatal_Max cap.

    This function reads the environment variable only at call time. It does not read the environment
    variable at module import time, so there is no import-time side effect. It also performs no
    broker API calls, DB writes or token handling.

    Returns:
        - If unset (or an empty string), treats the check as disabled and returns `None`.
        - If the value is `0`, treats the check as disabled and returns `None`.
        - If a positive integer, returns that value as the cap.

    Raises (`FatalMaxConfigError`):
        - Excluding unset and `0`, a value that cannot be interpreted as a positive integer (negative, fractional, non-numeric).
    """
    raw = os.environ.get(FATAL_MAX_ORDER_QTY_ENV)
    if raw is None:
        return None

    text = raw.strip()
    if text == "":
        # An empty string is treated the same as unset: the check is disabled.
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
        # 0 means the check is disabled.
        return None
    if limit < 0:
        raise FatalMaxConfigError(
            f"{FATAL_MAX_ORDER_QTY_ENV} 값은 음수일 수 없어: {raw!r}"
        )

    return limit


def check_fatal_max_order_qty(qty: int, limit: Optional[int]) -> int:
    """Checks whether the normalized quantity is at or below the Fatal_Max cap.

    Even when the cap is exceeded, it raises `FatalMaxExceededError` rather than adjusting or reducing
    the quantity to the cap. If at or below the cap, or if the cap is disabled (`None`), it returns `qty`
    unchanged. It performs no side effects such as broker API calls or DB writes.

    Args:
        qty: A positive integer quantity of 1 or greater, normalized by `normalize_order_qty`.
        limit: The result of `resolve_fatal_max_order_qty`. If `None`, the check is disabled.
    """
    if limit is None:
        return qty
    if qty > limit:
        raise FatalMaxExceededError(
            f"수량 {qty}가 Fatal_Max 상한 {limit}을 초과했어(상한으로 조정하지 않고 차단)"
        )
    return qty


class CancelModifyPayloadError(ValueError):
    """Raised when the CANCEL/MODIFY quantity branch violates the KIS payload contract.

    An exception to block, before the broker call, the quantity contract for full/partial cancellation and
    modification (0, negative, fractional, exceeding the active quantity, or a modification quantity that is
    not a positive integer). It is kept as a separate family from `QuantityValidationError` to distinguish
    cancel/modify payload-contract violations from other quantity validation errors.
    """


def resolve_cancel_modify_quantity(
    action_type: str,
    qty: Any = None,
    active_order_qty: Any = None,
) -> Tuple[str, str]:
    """Pure function that computes the quantity branch of a CANCEL/MODIFY request.

    This separates the inline quantity branch of `submit_rvsecncl_order()` into a side-effect-free pure function.
    It performs no side effects such as broker API calls, DB writes or token handling, and deterministically
    returns the same payload or the same error for the same input.

    Args:
        action_type: `"CANCEL"` or `"MODIFY"` (case-insensitive). Any other value is an error.
        qty: The requested quantity. In CANCEL, `None` means full cancellation; if specified, it is the partial cancellation quantity.
            In MODIFY, `None` uses the active order quantity as is.
        active_order_qty: The existing quantity of the Active_Order (the active order targeted for modification/cancellation).

    Returns:
        An `(ord_qty, qty_all_ord_yn)` string tuple. In a form that can be assigned directly to the KIS payload's
        `ORD_QTY` and `QTY_ALL_ORD_YN`.

        - CANCEL, qty omitted (None) → `("0", "Y")` (full cancellation).
        - CANCEL, an integer between 1 and active_order_qty → `(str(qty), "N")` (partial cancellation).
        - MODIFY, a positive integer of 1 or greater → `(str(qty), "N")`.
        - MODIFY, qty omitted (None) → `(str(active_order_qty), "N")`.

    Raises:
        CancelModifyPayloadError:
            - When action_type is not CANCEL/MODIFY.
            - When the CANCEL partial cancellation quantity is 0, negative, fractional, or exceeds active_order_qty.
            - When the MODIFY modification quantity (or active_order_qty when omitted) is not a positive integer.
    """
    normalized_action = str(action_type).upper()
    if normalized_action not in ("CANCEL", "MODIFY"):
        raise CancelModifyPayloadError(
            f"resolve_cancel_modify_quantity는 CANCEL 또는 MODIFY만 지원해: {action_type!r}"
        )

    def _positive_int(value: Any, label: str) -> int:
        # Reuse the same meaning as quantity normalization/validation (C1) (reject 0, negative, fractional, bool, non-numeric),
        # but signal cancel/modify payload-contract violations with CancelModifyPayloadError.
        try:
            return normalize_order_qty(value)
        except QuantityValidationError as exc:
            raise CancelModifyPayloadError(f"{label}이(가) 1 이상의 정수가 아니야: {value!r}") from exc

    if normalized_action == "CANCEL":
        if qty is None:
            # Full cancellation: ORD_QTY=0, QTY_ALL_ORD_YN=Y
            return "0", "Y"
        cancel_qty = _positive_int(qty, "부분 취소 수량")
        active_qty = _positive_int(active_order_qty, "활성 주문 수량")
        if cancel_qty > active_qty:
            raise CancelModifyPayloadError(
                f"부분 취소 수량 {cancel_qty}가 활성 주문 수량 {active_qty}를 초과했어"
            )
        # Partial cancellation: ORD_QTY=cancellation quantity, QTY_ALL_ORD_YN=N
        return str(cancel_qty), "N"

    # MODIFY: when the quantity is omitted, use the active order quantity as is.
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
    Finds the 'current active order' targeted for modification/cancellation.

    Even if the user passes the original order id,
    if there is a successful MODIFY order derived from that original order,
    the last ACCEPTED MODIFY order is used as the actual modification/cancellation target.

    Example:
    id=5 BUY     broker_order_no=0000029249
    id=6 MODIFY  broker_order_no=0000029474

    Even when cancel_order(5) is called, 0000029474 is sent to the actual API.
    """
    original = load_order_request_for_action(order_request_id)
    if not original:
        return None

    # Even when a MODIFY row is passed directly,
    # that MODIFY may have a subsequent MODIFY, so the same logic is applied.
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
        print(t("❌ No token", "❌ 토큰 없음"))
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
            print(t("❌ API request error:", "❌ API 요청 에러:"), e)
            return None, None

    res, latency_ms = _send(token)
    if res is None:
        return None, None, None

    new_tok = check_and_refresh_token(res.text)
    if new_tok:
        print(t("🔄 Token error detected → reissue + retry", "🔄 토큰 오류 감지 → 재발급 + 재요청"))
        res, latency_ms = _send(new_tok)
        if res is None:
            return None, None, None

    try:
        data = res.json()
    except Exception as e:
        print(t("❌ JSON parsing failed:", "❌ JSON 파싱 실패:"), e)
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
    """Records a cash buy/sell order in the DB and then submits it to the broker order API."""
    request_type = request_type.upper()
    if request_type not in ("BUY", "SELL"):
        raise ValueError("submit_cash_order는 BUY 또는 SELL만 지원해")

    if qty <= 0:
        raise ValueError("qty는 1 이상이어야 해")

    ord_dvsn, ord_unpr, normalized_method = normalize_order_method(order_method, order_price)

    print(t(f"\n📌 {request_type} order start: {stock_code} {qty} shares ({normalized_method})", f"\n📌 {request_type} 주문 시작: {stock_code} {qty}주 ({normalized_method})"))

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
        print(t(f"✅ {request_type} order succeeded", f"✅ {request_type} 주문 성공"))
    else:
        print(t(f"❌ {request_type} order failed:", f"❌ {request_type} 주문 실패:"), data.get("msg1", data))

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


# Terminal set for local state monotonicity protection on the submit_rvsecncl_order() path.
# Must be kept identical to the SQL IN list of the conditional UPDATE below.
TERMINAL_REQUEST_STATUSES: Tuple[str, ...] = ("FILLED", "CANCELED", "REJECTED", "FAILED")


class RequestStatusTransition(NamedTuple):
    """The decision result of `resolve_request_status_transition`.

    Attributes:
        allowed: Whether to treat the update as a success. True for a non-Terminal update or a same-Terminal
            re-application (no-op); False for a blocked Terminal regression.
        result_status: The result status after the decision. When allowed, the requested status (for a same-Terminal
            re-application, that Terminal status); when blocked, the current Terminal status is kept unchanged.
    """

    allowed: bool
    result_status: str


def resolve_request_status_transition(
    current_status: Optional[str],
    requested_status: str,
) -> RequestStatusTransition:
    """Pure function that decides the local order request status transition on the `submit_rvsecncl_order()` path.

    Blocks regression from a Terminal status (`FILLED`, `CANCELED`, `REJECTED`, `FAILED`) to another status.
    It performs no side effects such as broker API calls, DB access or token handling, and deterministically
    returns the same result for the same input.

    Args:
        current_status: The current `request_status`. `None` is treated as non-Terminal.
        requested_status: The requested update status.

    Returns:
        RequestStatusTransition:
            - Current status is non-Terminal → `allowed=True`, `result_status=requested_status`.
            - Current status is Terminal and the requested status is the same → `allowed=True` (no-op),
              `result_status=current_status`.
            - Current status is Terminal and the requested status differs → `allowed=False` (blocked),
              `result_status=current_status`.
    """
    if current_status in TERMINAL_REQUEST_STATUSES:
        if requested_status == current_status:
            # Same-Terminal re-application: allowed without error (no-op); result keeps that Terminal status.
            return RequestStatusTransition(allowed=True, result_status=current_status)
        # Block Terminal regression: not a success; keep the current Terminal status unchanged.
        return RequestStatusTransition(allowed=False, result_status=current_status)
    # Non-Terminal: allow the update; result is the requested status.
    return RequestStatusTransition(allowed=True, result_status=requested_status)


def update_order_request_status_if_not_terminal(
    order_request_id: int,
    request_status: str,
    message: Optional[str] = None,
) -> bool:
    """Updates the local order request status with a conditional UPDATE that blocks Terminal regression.

    Performs the same decision as `resolve_request_status_transition` in a single atomic conditional UPDATE.
    If the current status is Terminal and the requested status differs, it does not update (0 rows) and blocks;
    a same-Terminal re-application is allowed without error. This protection is defined only as local state
    protection on the `submit_rvsecncl_order()` path and does not claim to guarantee the monotonicity of the
    entire order synchronization outside the target file.

    The existing `update_order_request_status_only()` is kept as is for compatibility with other paths.

    Returns:
        Whether the update succeeded. Returns `True` if the update (or a same-Terminal no-op) reflected 1 or more rows;
        returns `False` as a block-failure indication if Terminal regression was blocked (0 rows).
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                # Keep the WHERE Terminal IN list identical to TERMINAL_REQUEST_STATUSES.
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
    Post-processes the original/active order status after a successful modification/cancellation order.

    MODIFY success:
      - If active_order_id is the original order, mark that original order as MODIFIED

    CANCEL success:
      - Mark active_order_id, i.e. the actual cancellation-target order, as CANCELED
    """
    action_type = action_type.upper()

    # The post-processing state transition is updated through the Terminal-regression-blocking guard.
    # The existing normal transitions non-Terminal → MODIFIED, non-Terminal → CANCELED are still allowed,
    # and an active order that is already Terminal (FILLED/CANCELED/REJECTED/FAILED) is not overwritten.
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
    """Submits a cancel/modify request based on the existing order context and corrects the parent order status."""
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
        # Cancellation uses the price/method of the current active order by default
        normalize_price = active_order.get("order_price")
        ord_dvsn, ord_unpr, normalized_method = normalize_order_method(original_method, normalize_price)
    else:
        effective_price = order_price if order_price is not None else active_order.get("order_price")
        ord_dvsn, ord_unpr, normalized_method = normalize_order_method(effective_method, effective_price)

    # KIS modify/cancel contract:
    # - Full cancellation: ORD_QTY=0, QTY_ALL_ORD_YN=Y
    # - Partial cancellation: ORD_QTY=cancellation quantity, QTY_ALL_ORD_YN=N
    # - MODIFY: specify the target quantity (use the active order quantity when omitted).
    #
    # The quantity branch is computed by the side-effect-free pure function resolve_cancel_modify_quantity.
    # For the no-payload-build / no-broker-call contract, it is called before building the payload or calling the broker.
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
        # A CANCEL request row itself is not a normal ACCEPTED; it is represented as
        # CANCEL_ACCEPTED, meaning "cancellation request acceptance success".
        #
        # active_order is the actual cancellation-target order, and
        # order_request_id is the CANCEL request row newly created this time.
        # ---------------------------------------------------------
        if action_type == "CANCEL":
            # The existing normal transition non-Terminal → CANCEL_ACCEPTED is kept,
            # but updated through the Terminal-regression-blocking guard.
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
        print(t(f"❌ {action_type} request failed:", f"❌ {action_type} 요청 실패:"), data.get("msg1", data))

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
