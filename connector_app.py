"""Flask-based connector API entrypoint.

Provides both the quote, balance, order, and order/fill query execution APIs and the View query API.
Depending on how a route is used, a broker API call, token renewal, or DB write can occur.
"""

import os
from flask import Flask, jsonify, request

from connector_balance import fetch_and_save_balance
from connector_buy import buy_stock
from connector_cancel import cancel_order
from connector_modify import modify_order
from connector_order_check import fetch_and_save_orders
from connector_quote_closed import get_closed_prices
from connector_quote_realtime import get_stock_price
from connector_sell import sell_stock
from connector_view_service import (
    get_view_account_summary,
    get_view_balance_latest,
    get_view_dashboard,
    get_view_eod_quotes,
    get_view_order_detail,
    get_view_order_events,
    get_view_orders,
    get_view_positions_latest,
    get_view_realtime_quote_latest,
    get_view_strategy_trades_recent,
)

app = Flask(__name__)

def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "y", "yes", "on")

# ---------------------------------------------------------
# execute APIs
# ---------------------------------------------------------
@app.route("/api/v1/quotes/realtime", methods=["GET"])
def quote_realtime():
    stock_code = request.args.get("code")
    if not stock_code:
        return jsonify({"error": "code 파라미터가 필요해"}), 400

    result = get_stock_price(stock_code, save_db=True)
    if not result:
        return jsonify({"error": "시세를 받아오지 못했어"}), 500

    return jsonify(result), 200


@app.route("/api/v1/quotes/eod", methods=["GET"])
def quote_eod():
    stock_code = request.args.get("code")
    start_date = request.args.get("start", "")
    end_date = request.args.get("end", "")
    period_div = request.args.get("period", "D")

    if not stock_code:
        return jsonify({"error": "code 파라미터가 필요해"}), 400

    result = get_closed_prices(
        stock_code=stock_code,
        period_div=period_div,
        start_date=start_date,
        end_date=end_date,
        save_db=True,
    )

    if not result:
        return jsonify({"error": "기간 시세를 받아오지 못했어"}), 500

    return jsonify(
        {
            "code": stock_code,
            "period": period_div,
            "start": start_date,
            "end": end_date,
            "count": len(result),
            "items": result,
        }
    ), 200


@app.route("/api/v1/accounts/balance", methods=["GET"])
def balance():
    result = fetch_and_save_balance()
    if not result:
        return jsonify({"error": "잔고를 받아오지 못했어"}), 500

    return jsonify(
        {
            "message": "잔고/보유종목 저장 완료",
            "balance": result["balance"],
            "positions_count": len(result["positions"]),
        }
    ), 200


@app.route("/api/v1/orders/buy", methods=["POST"])
def order_buy():
    body = request.get_json(silent=True) or {}

    stock_code = body.get("stock_code")
    qty = body.get("qty", 1)
    order_method = body.get("order_method", "MARKET")
    order_price = body.get("order_price")

    if not stock_code:
        return jsonify({"error": "stock_code가 필요해"}), 400

    try:
        result = buy_stock(
            stock_code=stock_code,
            qty=int(qty),
            order_method=order_method,
            order_price=order_price,
            stock_name=body.get("stock_name"),
            strategy_name=body.get("strategy_name"),
            strategy_version=body.get("strategy_version"),
            strategy_run_id=body.get("strategy_run_id"),
            strategy_signal_id=body.get("strategy_signal_id"),
            signal_date=body.get("signal_date"),
            signal_type=body.get("signal_type"),
            signal_score=body.get("signal_score"),
            signal_position_size=body.get("signal_position_size"),
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"매수 주문 예외: {e}"}), 500

    if not result:
        return jsonify({"error": "매수 주문 처리 실패"}), 500

    return jsonify(result), 200


@app.route("/api/v1/orders/sell", methods=["POST"])
def order_sell():
    body = request.get_json(silent=True) or {}

    stock_code = body.get("stock_code")
    qty = body.get("qty", 1)
    order_method = body.get("order_method", "MARKET")
    order_price = body.get("order_price")

    if not stock_code:
        return jsonify({"error": "stock_code가 필요해"}), 400

    try:
        result = sell_stock(
            stock_code=stock_code,
            qty=int(qty),
            order_method=order_method,
            order_price=order_price,
            stock_name=body.get("stock_name"),
            parent_order_request_id=body.get("parent_order_request_id"),
            strategy_name=body.get("strategy_name"),
            strategy_version=body.get("strategy_version"),
            strategy_run_id=body.get("strategy_run_id"),
            strategy_signal_id=body.get("strategy_signal_id"),
            signal_date=body.get("signal_date"),
            signal_type=body.get("signal_type"),
            signal_score=body.get("signal_score"),
            signal_position_size=body.get("signal_position_size"),
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"매도 주문 예외: {e}"}), 500

    if not result:
        return jsonify({"error": "매도 주문 처리 실패"}), 500

    return jsonify(result), 200


@app.route("/api/v1/orders/cancel", methods=["POST"])
def order_cancel():
    body = request.get_json(silent=True) or {}

    original_order_request_id = body.get("original_order_request_id")
    qty = body.get("qty")
    reason = body.get("reason")

    if original_order_request_id is None:
        return jsonify({"error": "original_order_request_id가 필요해"}), 400

    try:
        result = cancel_order(
            original_order_request_id=int(original_order_request_id),
            qty=int(qty) if qty is not None else None,
            reason=reason,
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"취소 주문 예외: {e}"}), 500

    if not result:
        return jsonify({"error": "취소 주문 처리 실패"}), 500

    return jsonify(result), 200


@app.route("/api/v1/orders/modify", methods=["POST"])
def order_modify():
    body = request.get_json(silent=True) or {}

    original_order_request_id = body.get("original_order_request_id")
    qty = body.get("qty")
    order_method = body.get("order_method", "LIMIT")
    order_price = body.get("order_price")
    reason = body.get("reason")

    if original_order_request_id is None:
        return jsonify({"error": "original_order_request_id가 필요해"}), 400

    try:
        result = modify_order(
            original_order_request_id=int(original_order_request_id),
            qty=int(qty) if qty is not None else None,
            order_method=order_method,
            order_price=order_price,
            reason=reason,
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"정정 주문 예외: {e}"}), 500

    if not result:
        return jsonify({"error": "정정 주문 처리 실패"}), 500

    return jsonify(result), 200


@app.route("/api/v1/orders/history", methods=["GET"])
def order_history():
    start_date = request.args.get("start")
    end_date = request.args.get("end")
    stock_code = request.args.get("stock_code")
    order_no = request.args.get("order_no")
    branch_code = request.args.get("branch_code")
    try_broad = request.args.get("try_broad", "true").lower() in ("true", "1", "y", "yes")

    if not start_date or not end_date:
        return jsonify({"error": "start, end 파라미터가 필요해"}), 400

    result = fetch_and_save_orders(
        start_date=start_date,
        end_date=end_date,
        stock_code=stock_code,
        order_no=order_no,
        branch_code=branch_code,
        try_broad_search_if_empty=try_broad,
    )
    if not result:
        return jsonify({"error": "주문/체결 조회 실패"}), 500

    return jsonify(
        {
            "message": "주문/체결 저장 완료",
            "raw_result": result,
        }
    ), 200


# ---------------------------------------------------------
# view APIs
# ---------------------------------------------------------
@app.route("/api/v1/view/account-summary", methods=["GET"])
def view_account_summary():
    account_no = request.args.get("account_no")

    result = get_view_account_summary(account_no=account_no)
    if not result:
        return jsonify({"error": "계좌 요약 데이터가 없어"}), 404

    return jsonify(result), 200

@app.route("/api/v1/view/dashboard", methods=["GET"])
def view_dashboard():
    account_no = request.args.get("account_no")
    recent_limit = request.args.get("recent_limit", "5")

    try:
        recent_limit_int = max(1, min(int(recent_limit), 50))
    except ValueError:
        return jsonify({"error": "recent_limit은 정수여야 해"}), 400

    result = get_view_dashboard(account_no=account_no, recent_limit=recent_limit_int)
    if not result:
        return jsonify({"error": "대시보드 데이터가 없어"}), 404

    return jsonify(result), 200


@app.route("/api/v1/view/balance/latest", methods=["GET"])
def view_balance_latest():
    account_no = request.args.get("account_no")

    result = get_view_balance_latest(account_no=account_no)
    if not result:
        return jsonify({"error": "최신 잔고 데이터가 없어"}), 404

    return jsonify(result), 200


@app.route("/api/v1/view/positions/latest", methods=["GET"])
def view_positions_latest():
    account_no = request.args.get("account_no")

    result = get_view_positions_latest(account_no=account_no)
    if not result:
        return jsonify({"error": "최신 보유종목 데이터가 없어"}), 404

    return jsonify(result), 200


@app.route("/api/v1/view/orders", methods=["GET"])
def view_orders():
    status = request.args.get("status")
    ticker = request.args.get("ticker")
    request_type = request.args.get("request_type")
    account_no = request.args.get("account_no")
    limit = request.args.get("limit", "20")
    offset = request.args.get("offset", "0")

    try:
        limit_int = max(1, min(int(limit), 200))
        offset_int = max(0, int(offset))
    except ValueError:
        return jsonify({"error": "limit, offset은 정수여야 해"}), 400

    result = get_view_orders(
        status=status,
        ticker=ticker,
        request_type=request_type,
        limit=limit_int,
        offset=offset_int,
        account_no=account_no,
    )
    return jsonify(result), 200


@app.route("/api/v1/view/orders/<int:order_request_id>", methods=["GET"])
def view_order_detail(order_request_id: int):
    result = get_view_order_detail(order_request_id)
    if not result:
        return jsonify({"error": "해당 주문이 없어"}), 404

    return jsonify(result), 200

@app.route("/api/v1/view/order-events", methods=["GET"])
def view_order_events():
    account_no = request.args.get("account_no")
    ticker = request.args.get("ticker")
    event_type = request.args.get("event_type")
    limit = request.args.get("limit", "100")
    offset = request.args.get("offset", "0")

    try:
        limit_int = max(1, min(int(limit), 500))
        offset_int = max(0, int(offset))
    except ValueError:
        return jsonify({"error": "limit, offset은 정수여야 해"}), 400

    result = get_view_order_events(
        account_no=account_no,
        ticker=ticker,
        event_type=event_type,
        limit=limit_int,
        offset=offset_int,
    )

    return jsonify(result), 200


@app.route("/api/v1/view/quotes/realtime/latest", methods=["GET"])
def view_realtime_quote_latest():
    stock_code = request.args.get("code")
    if not stock_code:
        return jsonify({"error": "code 파라미터가 필요해"}), 400

    result = get_view_realtime_quote_latest(stock_code)
    if not result:
        return jsonify({"error": "최신 실시간 시세 데이터가 없어"}), 404

    return jsonify(result), 200


@app.route("/api/v1/view/quotes/eod", methods=["GET"])
def view_eod_quotes():
    stock_code = request.args.get("code")
    period_div = request.args.get("period", "D")
    limit = request.args.get("limit", "60")

    if not stock_code:
        return jsonify({"error": "code 파라미터가 필요해"}), 400

    try:
        limit_int = max(1, min(int(limit), 1000))
    except ValueError:
        return jsonify({"error": "limit은 정수여야 해"}), 400

    result = get_view_eod_quotes(
        ticker_code=stock_code,
        limit=limit_int,
        period_div=period_div,
    )
    return jsonify(result), 200

@app.route("/api/v1/view/strategy/trades/recent", methods=["GET"])
def view_strategy_trades_recent():
    strategy_name = request.args.get("strategy_name")
    ticker = request.args.get("ticker")
    limit = request.args.get("limit", "100")
    offset = request.args.get("offset", "0")

    try:
        limit_int = max(1, min(int(limit), 500))
        offset_int = max(0, int(offset))
    except ValueError:
        return jsonify({"error": "limit, offset은 정수여야 해"}), 400

    result = get_view_strategy_trades_recent(
        strategy_name=strategy_name,
        ticker=ticker,
        limit=limit_int,
        offset=offset_int,
    )

    return jsonify(result), 200


# Alias kept for backward compatibility
@app.route("/api/v1/price", methods=["GET"])
def legacy_price():
    return quote_realtime()


if __name__ == "__main__":
    host = os.getenv("CONNECTOR_HOST", "127.0.0.1")
    port = int(os.getenv("CONNECTOR_PORT", "5000"))
    debug = _env_bool("CONNECTOR_DEBUG", False)

    app.run(host=host, port=port, debug=debug)
