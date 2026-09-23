"""Query response assembly service for the Flask View API.

Serializes the query results of DB repository helpers for JSON responses and reshapes them into timeline/tree form.
It does not call the broker API but depends on DB queries.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from psycopg.rows import dict_row

from connector_db import (
    count_order_requests,
    get_conn,
    get_dashboard_summary,
    get_eod_quotes,
    get_latest_balance_snapshot,
    get_latest_position_snapshots,
    get_latest_realtime_quote,
    get_order_request_detail,
    list_order_requests,
)


def _serialize_value(value: Any):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _serialize_dict(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    return {k: _serialize_value(v) for k, v in row.items()}


def _serialize_list(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [_serialize_dict(r) for r in rows]


def get_view_balance_latest(account_no: Optional[str] = None) -> Optional[Dict[str, Any]]:
    row = get_latest_balance_snapshot(account_no=account_no)
    if not row:
        return None

    return {
        "account_id": row["account_id"],
        "account_no": row["account_no"],
        "as_of_date": _serialize_value(row["as_of_date"]),
        "as_of_ts": _serialize_value(row["as_of_ts"]),
        "cash_balance": _serialize_value(row["cash_balance"]),
        "withdrawable_cash": _serialize_value(row["withdrawable_cash"]),
        "orderable_cash": _serialize_value(row["orderable_cash"]),
        "nextday_exec_amt": _serialize_value(row["nextday_exec_amt"]),
        "prev_closing_amt": _serialize_value(row["prev_closing_amt"]),
        "total_eval_amount": _serialize_value(row["total_eval_amount"]),
        "eval_profit": _serialize_value(row["eval_profit"]),
        "buy_amount_today": _serialize_value(row["buy_amount_today"]),
        "sell_amount_today": _serialize_value(row["sell_amount_today"]),
        "fee_total_today": _serialize_value(row["fee_total_today"]),
        "source_api": row["source_api"],
        "source_version": row["source_version"],
    }


def get_view_positions_latest(account_no: Optional[str] = None) -> Optional[Dict[str, Any]]:
    balance = get_latest_balance_snapshot(account_no=account_no)
    if not balance:
        return None

    rows = get_latest_position_snapshots(account_no=balance["account_no"])

    return {
        "account_no": balance["account_no"],
        "as_of_date": _serialize_value(balance["as_of_date"]),
        "as_of_ts": _serialize_value(balance["as_of_ts"]),
        "count": len(rows),
        "items": [
            {
                "position_id": r["id"],
                "ticker_code": r["ticker_code"],
                "stock_name": r["stock_name"],
                "market": r["market"],
                "quantity": _serialize_value(r["quantity"]),
                "sellable_quantity": _serialize_value(r["sellable_quantity"]),
                "avg_buy_price": _serialize_value(r["avg_buy_price"]),
                "buy_amount": _serialize_value(r["buy_amount"]),
                "current_price": _serialize_value(r["current_price"]),
                "eval_amount": _serialize_value(r["eval_amount"]),
                "eval_profit": _serialize_value(r["eval_profit"]),
                "eval_profit_rate": _serialize_value(r["eval_profit_rate"]),
            }
            for r in rows
        ],
    }


def get_view_orders(
    status: Optional[str] = None,
    ticker: Optional[str] = None,
    request_type: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
    account_no: Optional[str] = None,
) -> Dict[str, Any]:
    total = count_order_requests(
        status=status,
        ticker=ticker,
        request_type=request_type,
        account_no=account_no,
    )
    rows = list_order_requests(
        status=status,
        ticker=ticker,
        request_type=request_type,
        limit=limit,
        offset=offset,
        account_no=account_no,
    )

    return {
        "count": total,
        "limit": limit,
        "offset": offset,
        "items": [
            {
                "order_request_id": r["id"],
                "account_no": r["account_no"],
                "ticker_code": r["ticker_code"],
                "stock_name": r["stock_name"],
                "request_type": r["request_type"],
                "order_method": r["order_method"],
                "order_price": _serialize_value(r["order_price"]),
                "order_qty": _serialize_value(r["order_qty"]),
                "requested_amount": _serialize_value(r["requested_amount"]),
                "request_status": r["request_status"],
                "broker_order_no": r["broker_order_no"],
                "broker_branch_code": r["broker_branch_code"],
                "strategy_name": r["strategy_name"],
                "strategy_version": r["strategy_version"],
                "strategy_run_id": r["strategy_run_id"],
                "strategy_signal_id": _serialize_value(r["strategy_signal_id"]),
                "signal_date": _serialize_value(r["signal_date"]),
                "signal_type": r["signal_type"],
                "signal_score": _serialize_value(r["signal_score"]),
                "signal_position_size": _serialize_value(r["signal_position_size"]),
                "requested_at": _serialize_value(r["requested_at"]),
                "accepted_at": _serialize_value(r["accepted_at"]),
                "last_event_at": _serialize_value(r["last_event_at"]),
            }
            for r in rows
        ],
    }

def _build_tree_order_request_ids(
    request_row: Dict[str, Any],
    children: List[Dict[str, Any]],
) -> List[int]:
    """
    Builds the list of order_request_id for the root order + child orders.

    Example:
    request 10
    children 11, 12
    => [10, 11, 12]
    """
    ids = []

    if request_row and request_row.get("id") is not None:
        ids.append(int(request_row["id"]))

    for child in children or []:
        child_id = child.get("id")
        if child_id is not None:
            ids.append(int(child_id))

    # Deduplicate + preserve order
    seen = set()
    unique_ids = []
    for order_id in ids:
        if order_id not in seen:
            seen.add(order_id)
            unique_ids.append(order_id)

    return unique_ids

def _fetch_order_tree_events(order_request_ids: List[int]) -> List[Dict[str, Any]]:
    """
    Queries the events of the root order + child orders at once.

    Used to build the full lifecycle timeline in the View Order Detail.
    """
    if not order_request_ids:
        return []

    sql = """
        SELECT
            e.id,
            e.id AS order_event_id,
            e.order_request_id,
            r.parent_order_request_id,
            r.account_no,
            r.request_type,
            r.request_status,
            e.broker_order_no,
            e.broker_branch_code,
            COALESCE(e.ticker_code, r.ticker_code) AS ticker_code,
            COALESCE(e.stock_name, r.stock_name) AS stock_name,
            e.event_type,
            e.side,
            e.order_type_name,
            e.order_qty,
            e.executed_qty,
            e.remaining_qty,
            e.avg_exec_price,
            e.total_exec_amount,
            e.cancel_flag,
            e.order_date,
            e.order_time,
            e.event_ts,
            e.event_key,
            e.source_api,
            e.source_version,
            e.created_at
        FROM connector_order_event e
        LEFT JOIN connector_order_request r
               ON r.id = e.order_request_id
        WHERE e.order_request_id = ANY(%s)
        ORDER BY
            COALESCE(e.event_ts, e.created_at) ASC,
            e.id ASC
    """

    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, (order_request_ids,))
            rows = cur.fetchall()

    return [dict(row) for row in rows]

def _fetch_order_tree_fills(order_request_ids: List[int]) -> List[Dict[str, Any]]:
    """
    Queries the fills of the root order + child orders at once.
    """
    if not order_request_ids:
        return []

    sql = """
        SELECT
            f.id,
            f.id AS fill_id,
            f.order_request_id,
            f.order_event_id,
            r.parent_order_request_id,
            r.account_no,
            r.request_type,
            r.request_status,
            f.broker_order_no,
            f.broker_branch_code,
            COALESCE(f.ticker_code, r.ticker_code) AS ticker_code,
            COALESCE(r.stock_name, '') AS stock_name,
            f.side,
            f.fill_seq,
            f.fill_qty,
            f.fill_price,
            f.fill_ts,
            f.fee_amount,
            f.tax_amount,
            f.created_at
        FROM connector_fill f
        LEFT JOIN connector_order_request r
               ON r.id = f.order_request_id
        WHERE f.order_request_id = ANY(%s)
        ORDER BY
            COALESCE(f.fill_ts, f.created_at) ASC,
            f.id ASC
    """

    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, (order_request_ids,))
            rows = cur.fetchall()

    return [dict(row) for row in rows]

def _build_order_detail_timeline(
    request_row: Dict[str, Any],
    children: List[Dict[str, Any]],
    events: List[Dict[str, Any]],
    fills: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Builds the unified timeline for the order detail screen.

    Includes:
    - REQUEST: original / modification / cancellation order requests
    - EVENT: broker-query-based order events
    - FILL: fill history
    """
    timeline = []

    # 1) root request
    if request_row:
        timeline.append(
            {
                "timeline_type": "REQUEST",
                "sort_ts": request_row.get("requested_at") or request_row.get("created_at"),
                "order_request_id": request_row.get("id"),
                "parent_order_request_id": request_row.get("parent_order_request_id"),
                "request_type": request_row.get("request_type"),
                "request_status": request_row.get("request_status"),
                "broker_order_no": request_row.get("broker_order_no"),
                "broker_branch_code": request_row.get("broker_branch_code"),
                "ticker_code": request_row.get("ticker_code"),
                "stock_name": request_row.get("stock_name"),
                "order_method": request_row.get("order_method"),
                "order_price": request_row.get("order_price"),
                "order_qty": request_row.get("order_qty"),
                "requested_amount": request_row.get("requested_amount"),
                "message": f"{request_row.get('request_type')} 요청 생성",
            }
        )

    # 2) child requests
    for child in children or []:
        timeline.append(
            {
                "timeline_type": "REQUEST",
                "sort_ts": child.get("requested_at") or child.get("created_at"),
                "order_request_id": child.get("id"),
                "parent_order_request_id": child.get("parent_order_request_id"),
                "request_type": child.get("request_type"),
                "request_status": child.get("request_status"),
                "broker_order_no": child.get("broker_order_no"),
                "broker_branch_code": child.get("broker_branch_code"),
                "ticker_code": child.get("ticker_code"),
                "stock_name": child.get("stock_name"),
                "order_method": child.get("order_method"),
                "order_price": child.get("order_price"),
                "order_qty": child.get("order_qty"),
                "requested_amount": child.get("requested_amount"),
                "depth": child.get("depth"),
                "message": f"{child.get('request_type')} 요청 생성",
            }
        )

    # 3) order events
    for event in events or []:
        timeline.append(
            {
                "timeline_type": "EVENT",
                "sort_ts": event.get("event_ts") or event.get("created_at"),
                "order_event_id": event.get("id"),
                "order_request_id": event.get("order_request_id"),
                "parent_order_request_id": event.get("parent_order_request_id"),
                "request_type": event.get("request_type"),
                "request_status": event.get("request_status"),
                "event_type": event.get("event_type"),
                "broker_order_no": event.get("broker_order_no"),
                "broker_branch_code": event.get("broker_branch_code"),
                "ticker_code": event.get("ticker_code"),
                "stock_name": event.get("stock_name"),
                "side": event.get("side"),
                "order_qty": event.get("order_qty"),
                "executed_qty": event.get("executed_qty"),
                "remaining_qty": event.get("remaining_qty"),
                "avg_exec_price": event.get("avg_exec_price"),
                "total_exec_amount": event.get("total_exec_amount"),
                "event_key": event.get("event_key"),
                "message": event.get("event_type"),
            }
        )

    # 4) fills
    for fill in fills or []:
        timeline.append(
            {
                "timeline_type": "FILL",
                "sort_ts": fill.get("fill_ts") or fill.get("created_at"),
                "fill_id": fill.get("id"),
                "order_request_id": fill.get("order_request_id"),
                "order_event_id": fill.get("order_event_id"),
                "parent_order_request_id": fill.get("parent_order_request_id"),
                "request_type": fill.get("request_type"),
                "request_status": fill.get("request_status"),
                "broker_order_no": fill.get("broker_order_no"),
                "broker_branch_code": fill.get("broker_branch_code"),
                "ticker_code": fill.get("ticker_code"),
                "stock_name": fill.get("stock_name"),
                "side": fill.get("side"),
                "fill_seq": fill.get("fill_seq"),
                "fill_qty": fill.get("fill_qty"),
                "fill_price": fill.get("fill_price"),
                "fee_amount": fill.get("fee_amount"),
                "tax_amount": fill.get("tax_amount"),
                "message": "체결",
            }
        )

    # Sort by sort_ts
    timeline.sort(
        key=lambda x: (
            x.get("sort_ts") is None,
            str(x.get("sort_ts")),
            x.get("order_request_id") or 0,
            x.get("order_event_id") or 0,
            x.get("fill_id") or 0,
        )
    )

    # JSON serialization
    return _serialize_list(timeline)

def _get_tree_final_status(
    request_row: Dict[str, Any],
    children: List[Dict[str, Any]],
) -> Optional[str]:
    if children:
        sorted_children = sorted(
            children,
            key=lambda x: (
                x.get("depth") or 0,
                x.get("id") or 0,
            )
        )
        return sorted_children[-1].get("request_status")

    if request_row:
        return request_row.get("request_status")

    return None

def _get_tree_final_request_type(
    request_row: Dict[str, Any],
    children: List[Dict[str, Any]],
) -> Optional[str]:
    if children:
        sorted_children = sorted(
            children,
            key=lambda x: (
                x.get("depth") or 0,
                x.get("id") or 0,
            )
        )
        return sorted_children[-1].get("request_type")

    if request_row:
        return request_row.get("request_type")

    return None

def _build_timeline_summary(timeline: List[Dict[str, Any]]) -> str:
    parts = []

    for item in timeline:
        timeline_type = item.get("timeline_type")

        if timeline_type == "REQUEST":
            request_type = item.get("request_type")
            status = item.get("request_status")
            order_id = item.get("order_request_id")
            parts.append(f"{request_type}#{order_id}({status})")

        elif timeline_type == "EVENT":
            event_type = item.get("event_type")
            order_id = item.get("order_request_id")
            parts.append(f"{event_type}#{order_id}")

        elif timeline_type == "FILL":
            fill_id = item.get("fill_id")
            qty = item.get("fill_qty")
            price = item.get("fill_price")
            parts.append(f"FILL#{fill_id}({qty}@{price})")

    return " -> ".join(parts)

def get_view_order_detail(order_request_id: int) -> Optional[Dict[str, Any]]:
    """Assembles the parent/child order, event and fill information of a single order request into a detailed timeline."""
    detail = get_order_request_detail(order_request_id)
    if not detail:
        return None

    request_row = detail["request"]
    children = detail.get("children", [])

    # All ids of the root order + child orders
    tree_order_request_ids = _build_tree_order_request_ids(
        request_row=request_row,
        children=children,
    )

    # Instead of the existing detail["events"], detail["fills"],
    # re-query the full events/fills of root + children
    events = _fetch_order_tree_events(tree_order_request_ids)
    fills = _fetch_order_tree_fills(tree_order_request_ids)

    timeline = _build_order_detail_timeline(
        request_row=request_row,
        children=children,
        events=events,
        fills=fills,
    )

    return {
        "request": {
            "order_request_id": request_row["id"],
            "account_id": _serialize_value(request_row["account_id"]),
            "account_no": request_row["account_no"],
            "ticker_code": request_row["ticker_code"],
            "stock_name": request_row["stock_name"],
            "request_type": request_row["request_type"],
            "order_method": request_row["order_method"],
            "order_price": _serialize_value(request_row["order_price"]),
            "order_qty": _serialize_value(request_row["order_qty"]),
            "requested_amount": _serialize_value(request_row["requested_amount"]),
            "parent_order_request_id": _serialize_value(request_row["parent_order_request_id"]),
            "strategy_name": request_row["strategy_name"],
            "strategy_version": request_row["strategy_version"],
            "strategy_run_id": request_row["strategy_run_id"],
            "strategy_signal_id": _serialize_value(request_row["strategy_signal_id"]),
            "signal_date": _serialize_value(request_row["signal_date"]),
            "signal_type": request_row["signal_type"],
            "signal_score": _serialize_value(request_row["signal_score"]),
            "signal_position_size": _serialize_value(request_row["signal_position_size"]),
            "request_status": request_row["request_status"],
            "broker_order_no": request_row["broker_order_no"],
            "broker_branch_code": request_row["broker_branch_code"],
            "rejection_code": request_row["rejection_code"],
            "rejection_message": request_row["rejection_message"],
            "requested_at": _serialize_value(request_row["requested_at"]),
            "accepted_at": _serialize_value(request_row["accepted_at"]),
            "last_event_at": _serialize_value(request_row["last_event_at"]),
            "created_at": _serialize_value(request_row["created_at"]),
            "updated_at": _serialize_value(request_row["updated_at"]),
            "request_payload": _serialize_value(request_row["request_payload"]),
            "response_payload": _serialize_value(request_row["response_payload"]),
        },
        "events": [
            {
                "order_event_id": e["id"],
                "order_request_id": e["order_request_id"],
                "parent_order_request_id": _serialize_value(e["parent_order_request_id"]),
                "account_no": e["account_no"],
                "request_type": e["request_type"],
                "request_status": e["request_status"],
                "broker_order_no": e["broker_order_no"],
                "broker_branch_code": e["broker_branch_code"],
                "ticker_code": e["ticker_code"],
                "stock_name": e["stock_name"],
                "event_type": e["event_type"],
                "side": e["side"],
                "order_type_name": e["order_type_name"],
                "order_qty": _serialize_value(e["order_qty"]),
                "executed_qty": _serialize_value(e["executed_qty"]),
                "remaining_qty": _serialize_value(e["remaining_qty"]),
                "avg_exec_price": _serialize_value(e["avg_exec_price"]),
                "total_exec_amount": _serialize_value(e["total_exec_amount"]),
                "cancel_flag": e["cancel_flag"],
                "order_date": _serialize_value(e["order_date"]),
                "order_time": e["order_time"],
                "event_ts": _serialize_value(e["event_ts"]),
                "event_key": e["event_key"],
                "source_api": e["source_api"],
                "source_version": e["source_version"],
                "created_at": _serialize_value(e["created_at"]),
            }
            for e in events
        ],
        "fills": [
            {
                "fill_id": f["id"],
                "order_request_id": f["order_request_id"],
                "order_event_id": f["order_event_id"],
                "parent_order_request_id": _serialize_value(f["parent_order_request_id"]),
                "account_no": f["account_no"],
                "request_type": f["request_type"],
                "request_status": f["request_status"],
                "broker_order_no": f["broker_order_no"],
                "broker_branch_code": f["broker_branch_code"],
                "ticker_code": f["ticker_code"],
                "stock_name": f["stock_name"],
                "side": f["side"],
                "fill_seq": _serialize_value(f["fill_seq"]),
                "fill_qty": _serialize_value(f["fill_qty"]),
                "fill_price": _serialize_value(f["fill_price"]),
                "fill_ts": _serialize_value(f["fill_ts"]),
                "fee_amount": _serialize_value(f["fee_amount"]),
                "tax_amount": _serialize_value(f["tax_amount"]),
                "created_at": _serialize_value(f["created_at"]),
            }
            for f in fills
        ],
        "children": [
            {
                "order_request_id": c["id"],
                "depth": _serialize_value(c.get("depth")),
                "account_id": _serialize_value(c["account_id"]),
                "account_no": c["account_no"],
                "ticker_code": c["ticker_code"],
                "stock_name": c["stock_name"],
                "request_type": c["request_type"],
                "order_method": c["order_method"],
                "order_price": _serialize_value(c["order_price"]),
                "order_qty": _serialize_value(c["order_qty"]),
                "requested_amount": _serialize_value(c["requested_amount"]),
                "parent_order_request_id": _serialize_value(c["parent_order_request_id"]),
                "strategy_name": c["strategy_name"],
                "strategy_version": c["strategy_version"],
                "strategy_run_id": c["strategy_run_id"],
                "strategy_signal_id": _serialize_value(c["strategy_signal_id"]),
                "signal_date": _serialize_value(c["signal_date"]),
                "signal_type": c["signal_type"],
                "signal_score": _serialize_value(c["signal_score"]),
                "signal_position_size": _serialize_value(c["signal_position_size"]),
                "request_status": c["request_status"],
                "broker_order_no": c["broker_order_no"],
                "broker_branch_code": c["broker_branch_code"],
                "rejection_code": c["rejection_code"],
                "rejection_message": c["rejection_message"],
                "requested_at": _serialize_value(c["requested_at"]),
                "accepted_at": _serialize_value(c["accepted_at"]),
                "last_event_at": _serialize_value(c["last_event_at"]),
                "created_at": _serialize_value(c["created_at"]),
                "updated_at": _serialize_value(c["updated_at"]),
            }
            for c in children
        ],
        "tree_order_request_ids": tree_order_request_ids,
        "timeline": timeline,
        "tree_final_status": _get_tree_final_status(request_row, children),
        "tree_final_request_type": _get_tree_final_request_type(request_row, children),
        "timeline_summary": _build_timeline_summary(timeline),
    }

def get_view_account_summary(account_no: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    For the account summary at the top of the View Dashboard.

    Basis:
    - Based on the latest as_of_date of connector_position_snapshot
    - If account_no is given, only that account
    - If account_no is absent, return the full latest summary per account
    """
    where_sql = ""
    params = []

    if account_no:
        where_sql = "WHERE account_no = %s"
        params.append(account_no)

    sql = f"""
        WITH latest_date AS (
            SELECT
                account_no,
                MAX(as_of_date) AS as_of_date
            FROM connector_position_snapshot
            {where_sql}
            GROUP BY account_no
        ),
        latest_positions AS (
            SELECT p.*
            FROM connector_position_snapshot p
            JOIN latest_date l
              ON l.account_no = p.account_no
             AND l.as_of_date = p.as_of_date
        )
        SELECT
            account_no,
            MAX(as_of_date) AS as_of_date,
            MAX(as_of_ts) AS as_of_ts,
            COUNT(*) AS position_count,
            COALESCE(SUM(quantity), 0) AS total_quantity,
            COALESCE(SUM(buy_amount), 0) AS total_buy_amount,
            COALESCE(SUM(eval_amount), 0) AS total_eval_amount,
            COALESCE(SUM(eval_profit), 0) AS total_eval_profit,
            CASE
                WHEN COALESCE(SUM(buy_amount), 0) = 0 THEN NULL
                ELSE ROUND(
                    COALESCE(SUM(eval_profit), 0)
                    / NULLIF(SUM(buy_amount), 0)
                    * 100,
                    2
                )
            END AS total_eval_profit_rate
        FROM latest_positions
        GROUP BY account_no
        ORDER BY account_no
    """

    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

    items = _serialize_list([dict(r) for r in rows])

    if account_no and not items:
        return None

    return {
        "account_no": account_no,
        "count": len(items),
        "items": items,
    }

def get_view_order_events(
    account_no: Optional[str] = None,
    ticker: Optional[str] = None,
    event_type: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> Dict[str, Any]:
    """
    For the View order event list.

    Purpose:
    - Check how an order request actually changed into events
    - Check acceptance/fill/cancellation/modification events
    - Check for duplicate event_key
    """
    where = ["1 = 1"]
    params = []

    if account_no:
        where.append("r.account_no = %s")
        params.append(account_no)

    if ticker:
        where.append("e.ticker_code = %s")
        params.append(ticker)

    if event_type:
        where.append("e.event_type = %s")
        params.append(event_type)

    sql = f"""
        SELECT
            e.id AS order_event_id,
            e.order_request_id,
            r.parent_order_request_id,
            r.account_no,
            r.request_type,
            r.request_status,
            e.broker_order_no,
            e.broker_branch_code,
            COALESCE(e.ticker_code, r.ticker_code) AS ticker_code,
            COALESCE(e.stock_name, r.stock_name) AS stock_name,
            e.event_type,
            e.side,
            e.order_type_name,
            e.order_qty,
            e.executed_qty,
            e.remaining_qty,
            e.avg_exec_price,
            e.total_exec_amount,
            e.cancel_flag,
            e.order_date,
            e.order_time,
            e.event_ts,
            e.event_key,
            e.source_api,
            e.source_version,
            e.created_at
        FROM connector_order_event e
        LEFT JOIN connector_order_request r
               ON r.id = e.order_request_id
        WHERE {' AND '.join(where)}
        ORDER BY e.id DESC
        LIMIT %s OFFSET %s
    """

    params.extend([limit, offset])

    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

    items = _serialize_list([dict(r) for r in rows])

    return {
        "account_no": account_no,
        "ticker": ticker,
        "event_type": event_type,
        "limit": limit,
        "offset": offset,
        "count": len(items),
        "items": items,
    }

def get_view_strategy_trades_recent(
    strategy_name: Optional[str] = None,
    ticker: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> Dict[str, Any]:
    """
    For the View strategy trade log list.

    Purpose:
    - Check recent trades from Strategy Research/Backtest
    - Check the buy/sell rationale buy_info/sell_info
    - Basis for later linking Strategy results with Connector orders in the View
    """
    where = ["1 = 1"]
    params = []

    if strategy_name:
        where.append("strategy_name = %s")
        params.append(strategy_name)

    if ticker:
        where.append("ticker_code = %s")
        params.append(ticker)

    sql = f"""
        SELECT
            id,
            strategy_name,
            strategy_version,
            ticker_code,
            company_name,
            buy_date,
            sell_date,
            return_pct,
            holding_period,
            buy_info,
            sell_info,
            created_at
        FROM strategy_trade_log
        WHERE {' AND '.join(where)}
        ORDER BY id DESC
        LIMIT %s OFFSET %s
    """

    params.extend([limit, offset])

    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

    items = _serialize_list([dict(r) for r in rows])

    return {
        "strategy_name": strategy_name,
        "ticker": ticker,
        "limit": limit,
        "offset": offset,
        "count": len(items),
        "items": items,
    }

def get_view_dashboard(account_no: Optional[str] = None, recent_limit: int = 5) -> Optional[Dict[str, Any]]:
    summary = get_dashboard_summary(account_no=account_no, recent_limit=recent_limit)
    if not summary:
        return None

    balance = summary["balance"]
    recent_orders = summary["recent_orders"]

    return {
        "account_no": balance["account_no"],
        "as_of_date": _serialize_value(balance["as_of_date"]),
        "as_of_ts": _serialize_value(balance["as_of_ts"]),
        "cash_balance": _serialize_value(balance["cash_balance"]),
        "withdrawable_cash": _serialize_value(balance["withdrawable_cash"]),
        "orderable_cash": _serialize_value(balance["orderable_cash"]),
        "total_eval_amount": _serialize_value(balance["total_eval_amount"]),
        "eval_profit": _serialize_value(balance["eval_profit"]),
        "positions_count": summary["positions_count"],
        "recent_orders": [
            {
                "order_request_id": r["id"],
                "ticker_code": r["ticker_code"],
                "stock_name": r["stock_name"],
                "request_type": r["request_type"],
                "order_method": r["order_method"],
                "order_qty": _serialize_value(r["order_qty"]),
                "request_status": r["request_status"],
                "broker_order_no": r["broker_order_no"],
                "broker_branch_code": r["broker_branch_code"],
                "requested_at": _serialize_value(r["requested_at"]),
                "last_event_at": _serialize_value(r["last_event_at"]),
            }
            for r in recent_orders
        ],
    }


def get_view_realtime_quote_latest(ticker_code: str) -> Optional[Dict[str, Any]]:
    row = get_latest_realtime_quote(ticker_code)
    if not row:
        return None

    return _serialize_dict(row)


def get_view_eod_quotes(
    ticker_code: str,
    limit: int = 60,
    period_div: str = "D",
) -> Dict[str, Any]:
    rows = get_eod_quotes(ticker_code=ticker_code, limit=limit, period_div=period_div)

    # For screen charts it is usually more convenient to return oldest-first
    rows = list(reversed(rows))

    return {
        "ticker_code": ticker_code,
        "period_div": period_div,
        "count": len(rows),
        "items": _serialize_list(rows),
    }
