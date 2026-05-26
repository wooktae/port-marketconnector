import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

import psycopg
from psycopg.rows import dict_row

DB_CONN_STR = """
host=localhost
port=5433
dbname=interest_crawler
user=postgres
password=doflwhsk3768!
"""


def _json_default(obj):
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    return str(obj)


def to_jsonb(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=_json_default)


def get_conn():
    return psycopg.connect(DB_CONN_STR)


# ---------------------------------------------------------
# common helpers
# ---------------------------------------------------------
def _fetchone_dict(sql: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
    return dict(row) if row else None


def _fetchall_dict(sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
    return [dict(r) for r in rows]


def ensure_connector_account(
    account_no: str,
    account_product_code: str,
    broker_name: str = "koreainvestment",
    environment: str = "paper",
    account_alias: Optional[str] = None,
) -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO connector_account (
                    account_no, account_product_code, broker_name, environment, account_alias
                )
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (broker_name, environment, account_no, account_product_code)
                DO UPDATE SET
                    account_alias = COALESCE(EXCLUDED.account_alias, connector_account.account_alias),
                    updated_at = now()
                RETURNING id
                """,
                (account_no, account_product_code, broker_name, environment, account_alias),
            )
            row = cur.fetchone()
        conn.commit()
    return row[0]


def insert_api_call_log(
    account_id: Optional[int],
    api_category: str,
    api_name: str,
    http_method: str,
    endpoint: str,
    tr_id: Optional[str],
    request_params: Optional[Dict[str, Any]] = None,
    request_body: Optional[Dict[str, Any]] = None,
    response_status: Optional[int] = None,
    response_code: Optional[str] = None,
    response_message: Optional[str] = None,
    response_body: Optional[Dict[str, Any]] = None,
    is_success: Optional[bool] = None,
    latency_ms: Optional[int] = None,
):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO connector_api_call_log (
                    account_id, api_category, api_name, http_method, endpoint, tr_id,
                    request_params, request_body, response_status, response_code,
                    response_message, response_body, is_success, latency_ms
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s,
                    %s::jsonb, %s::jsonb, %s, %s,
                    %s, %s::jsonb, %s, %s
                )
                """,
                (
                    account_id,
                    api_category,
                    api_name,
                    http_method,
                    endpoint,
                    tr_id,
                    to_jsonb(request_params) if request_params is not None else None,
                    to_jsonb(request_body) if request_body is not None else None,
                    response_status,
                    response_code,
                    response_message,
                    to_jsonb(response_body) if response_body is not None else None,
                    is_success,
                    latency_ms,
                ),
            )
        conn.commit()


def upsert_connector_balance_snapshot(record: Dict[str, Any]):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO connector_balance_snapshot (
                    account_id, account_no, as_of_date, as_of_ts,
                    cash_balance, withdrawable_cash, orderable_cash,
                    nextday_exec_amt, prev_closing_amt, total_eval_amount,
                    eval_profit, buy_amount_today, sell_amount_today, fee_total_today,
                    raw_json, source_api, source_version
                )
                VALUES (
                    %(account_id)s, %(account_no)s, %(as_of_date)s, %(as_of_ts)s,
                    %(cash_balance)s, %(withdrawable_cash)s, %(orderable_cash)s,
                    %(nextday_exec_amt)s, %(prev_closing_amt)s, %(total_eval_amount)s,
                    %(eval_profit)s, %(buy_amount_today)s, %(sell_amount_today)s, %(fee_total_today)s,
                    %(raw_json)s::jsonb, %(source_api)s, %(source_version)s
                )
                ON CONFLICT (account_no, as_of_date)
                DO UPDATE SET
                    account_id         = EXCLUDED.account_id,
                    as_of_ts           = EXCLUDED.as_of_ts,
                    cash_balance       = EXCLUDED.cash_balance,
                    withdrawable_cash  = EXCLUDED.withdrawable_cash,
                    orderable_cash     = EXCLUDED.orderable_cash,
                    nextday_exec_amt   = EXCLUDED.nextday_exec_amt,
                    prev_closing_amt   = EXCLUDED.prev_closing_amt,
                    total_eval_amount  = EXCLUDED.total_eval_amount,
                    eval_profit        = EXCLUDED.eval_profit,
                    buy_amount_today   = EXCLUDED.buy_amount_today,
                    sell_amount_today  = EXCLUDED.sell_amount_today,
                    fee_total_today    = EXCLUDED.fee_total_today,
                    raw_json           = EXCLUDED.raw_json,
                    source_api         = EXCLUDED.source_api,
                    source_version     = EXCLUDED.source_version
                """,
                record,
            )
        conn.commit()


def upsert_connector_position_snapshots(records: List[Dict[str, Any]]):
    if not records:
        return

    with get_conn() as conn:
        with conn.cursor() as cur:
            for r in records:
                cur.execute(
                    """
                    INSERT INTO connector_position_snapshot (
                        account_id, account_no,
                        ticker_code, stock_name, market,
                        as_of_date, as_of_ts,
                        quantity, sellable_quantity,
                        avg_buy_price, buy_amount, current_price,
                        eval_amount, eval_profit, eval_profit_rate,
                        raw_json, source_api, source_version
                    )
                    VALUES (
                        %(account_id)s, %(account_no)s,
                        %(ticker_code)s, %(stock_name)s, %(market)s,
                        %(as_of_date)s, %(as_of_ts)s,
                        %(quantity)s, %(sellable_quantity)s,
                        %(avg_buy_price)s, %(buy_amount)s, %(current_price)s,
                        %(eval_amount)s, %(eval_profit)s, %(eval_profit_rate)s,
                        %(raw_json)s::jsonb, %(source_api)s, %(source_version)s
                    )
                    ON CONFLICT (account_no, ticker_code, as_of_date)
                    DO UPDATE SET
                        account_id        = EXCLUDED.account_id,
                        stock_name        = EXCLUDED.stock_name,
                        market            = EXCLUDED.market,
                        as_of_ts          = EXCLUDED.as_of_ts,
                        quantity          = EXCLUDED.quantity,
                        sellable_quantity = EXCLUDED.sellable_quantity,
                        avg_buy_price     = EXCLUDED.avg_buy_price,
                        buy_amount        = EXCLUDED.buy_amount,
                        current_price     = EXCLUDED.current_price,
                        eval_amount       = EXCLUDED.eval_amount,
                        eval_profit       = EXCLUDED.eval_profit,
                        eval_profit_rate  = EXCLUDED.eval_profit_rate,
                        raw_json          = EXCLUDED.raw_json,
                        source_api        = EXCLUDED.source_api,
                        source_version    = EXCLUDED.source_version
                    """,
                    r,
                )
        conn.commit()


def insert_connector_quote_realtime(record: Dict[str, Any]):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO connector_quote_realtime (
                    ticker_code, quote_ts, current_price, diff_price, diff_rate,
                    volume, open_price, high_price, low_price,
                    best_ask_price, best_bid_price, expected_match_price, expected_match_volume,
                    raw_json, source_api, source_version
                )
                VALUES (
                    %(ticker_code)s, %(quote_ts)s, %(current_price)s, %(diff_price)s, %(diff_rate)s,
                    %(volume)s, %(open_price)s, %(high_price)s, %(low_price)s,
                    %(best_ask_price)s, %(best_bid_price)s, %(expected_match_price)s, %(expected_match_volume)s,
                    %(raw_json)s::jsonb, %(source_api)s, %(source_version)s
                )
                """,
                record,
            )
        conn.commit()


def upsert_connector_quote_eod(records: List[Dict[str, Any]]):
    if not records:
        return

    with get_conn() as conn:
        with conn.cursor() as cur:
            for r in records:
                cur.execute(
                    """
                    INSERT INTO connector_quote_eod (
                        ticker_code, trade_date,
                        open_price, high_price, low_price, close_price,
                        volume, trading_value, period_div, adjusted_price_yn,
                        raw_json, source_api, source_version
                    )
                    VALUES (
                        %(ticker_code)s, %(trade_date)s,
                        %(open_price)s, %(high_price)s, %(low_price)s, %(close_price)s,
                        %(volume)s, %(trading_value)s, %(period_div)s, %(adjusted_price_yn)s,
                        %(raw_json)s::jsonb, %(source_api)s, %(source_version)s
                    )
                    ON CONFLICT (ticker_code, trade_date, period_div)
                    DO UPDATE SET
                        open_price        = EXCLUDED.open_price,
                        high_price        = EXCLUDED.high_price,
                        low_price         = EXCLUDED.low_price,
                        close_price       = EXCLUDED.close_price,
                        volume            = EXCLUDED.volume,
                        trading_value     = EXCLUDED.trading_value,
                        adjusted_price_yn = EXCLUDED.adjusted_price_yn,
                        raw_json          = EXCLUDED.raw_json,
                        source_api        = EXCLUDED.source_api,
                        source_version    = EXCLUDED.source_version
                    """,
                    r,
                )
        conn.commit()


def insert_order_request(record: Dict[str, Any]) -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO connector_order_request (
                    account_id, account_no,
                    ticker_code, stock_name,
                    request_type, order_method, order_price, order_qty, requested_amount,
                    parent_order_request_id,
                    strategy_name, strategy_version, strategy_run_id,
                    strategy_signal_id, signal_date, signal_type,
                    signal_score, signal_position_size,
                    request_status,
                    broker_order_no, broker_branch_code,
                    rejection_code, rejection_message,
                    request_payload, response_payload,
                    requested_at, accepted_at, last_event_at
                )
                VALUES (
                    %(account_id)s, %(account_no)s,
                    %(ticker_code)s, %(stock_name)s,
                    %(request_type)s, %(order_method)s, %(order_price)s, %(order_qty)s, %(requested_amount)s,
                    %(parent_order_request_id)s,
                    %(strategy_name)s, %(strategy_version)s, %(strategy_run_id)s,
                    %(strategy_signal_id)s, %(signal_date)s, %(signal_type)s,
                    %(signal_score)s, %(signal_position_size)s,
                    %(request_status)s,
                    %(broker_order_no)s, %(broker_branch_code)s,
                    %(rejection_code)s, %(rejection_message)s,
                    %(request_payload)s::jsonb, %(response_payload)s::jsonb,
                    %(requested_at)s, %(accepted_at)s, %(last_event_at)s
                )
                RETURNING id
                """,
                record,
            )
            row = cur.fetchone()
        conn.commit()
    return row[0]


def update_order_request_after_response(order_request_id: int, record: Dict[str, Any]):
    payload = {"id": order_request_id, **record}
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE connector_order_request
                   SET request_status     = %(request_status)s,
                       broker_order_no    = %(broker_order_no)s,
                       broker_branch_code = %(broker_branch_code)s,
                       rejection_code     = %(rejection_code)s,
                       rejection_message  = %(rejection_message)s,
                       response_payload   = %(response_payload)s::jsonb,
                       accepted_at        = %(accepted_at)s,
                       last_event_at      = %(last_event_at)s,
                       updated_at         = now()
                 WHERE id = %(id)s
                """,
                payload,
            )
        conn.commit()


def build_order_event_key(
    broker_order_no: Optional[str],
    broker_branch_code: Optional[str],
    event_type: Optional[str],
    ticker_code: Optional[str],
    order_qty: Optional[int],
    executed_qty: Optional[int],
    total_exec_amount: Optional[float],
    cancel_flag: Optional[str],
) -> str:
    return "|".join(
        [
            str(broker_order_no or ""),
            str(broker_branch_code or ""),
            str(event_type or ""),
            str(ticker_code or ""),
            str(order_qty if order_qty is not None else ""),
            str(executed_qty if executed_qty is not None else ""),
            f"{float(total_exec_amount):.2f}" if total_exec_amount is not None else "",
            str(cancel_flag or ""),
        ]
    )


def insert_order_event(record: Dict[str, Any]) -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO connector_order_event (
                    order_request_id, account_id, account_no,
                    broker_order_no, broker_branch_code,
                    ticker_code, stock_name,
                    event_type, side, order_type_name,
                    order_qty, executed_qty, remaining_qty,
                    avg_exec_price, total_exec_amount, cancel_flag,
                    order_date, order_time, event_ts,
                    event_key,
                    raw_json, source_api, source_version
                )
                VALUES (
                    %(order_request_id)s, %(account_id)s, %(account_no)s,
                    %(broker_order_no)s, %(broker_branch_code)s,
                    %(ticker_code)s, %(stock_name)s,
                    %(event_type)s, %(side)s, %(order_type_name)s,
                    %(order_qty)s, %(executed_qty)s, %(remaining_qty)s,
                    %(avg_exec_price)s, %(total_exec_amount)s, %(cancel_flag)s,
                    %(order_date)s, %(order_time)s, %(event_ts)s,
                    %(event_key)s,
                    %(raw_json)s::jsonb, %(source_api)s, %(source_version)s
                )
                ON CONFLICT (event_key) WHERE event_key IS NOT NULL
                DO UPDATE SET
                    order_request_id = EXCLUDED.order_request_id,
                    account_id       = EXCLUDED.account_id,
                    account_no       = EXCLUDED.account_no,
                    ticker_code      = EXCLUDED.ticker_code,
                    stock_name       = EXCLUDED.stock_name,
                    raw_json         = EXCLUDED.raw_json,
                    source_api       = EXCLUDED.source_api,
                    source_version   = EXCLUDED.source_version
                RETURNING id
                """,
                record,
            )
            row = cur.fetchone()
        conn.commit()
    return row[0]


def upsert_fill(record: Dict[str, Any]):
    if not record.get("side"):
        record["side"] = "UNKNOWN"

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO connector_fill (
                    order_request_id, order_event_id, account_id,
                    broker_order_no, broker_branch_code,
                    ticker_code, side, fill_seq,
                    fill_qty, fill_price, fill_ts,
                    fee_amount, tax_amount, raw_json
                )
                VALUES (
                    %(order_request_id)s, %(order_event_id)s, %(account_id)s,
                    %(broker_order_no)s, %(broker_branch_code)s,
                    %(ticker_code)s, %(side)s, %(fill_seq)s,
                    %(fill_qty)s, %(fill_price)s, %(fill_ts)s,
                    %(fee_amount)s, %(tax_amount)s, %(raw_json)s::jsonb
                )
                ON CONFLICT (broker_order_no, broker_branch_code, fill_seq)
                DO UPDATE SET
                    fill_qty   = EXCLUDED.fill_qty,
                    fill_price = EXCLUDED.fill_price,
                    fill_ts    = EXCLUDED.fill_ts,
                    fee_amount = EXCLUDED.fee_amount,
                    tax_amount = EXCLUDED.tax_amount,
                    raw_json   = EXCLUDED.raw_json
                """,
                record,
            )
        conn.commit()


def insert_signal_order_map(record: Dict[str, Any]):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO connector_signal_order_map (
                    strategy_signal_id, order_request_id,
                    strategy_name, strategy_version, strategy_run_id,
                    signal_date, ticker_code, signal_type,
                    target_qty, target_price, target_weight,
                    execution_decision
                )
                VALUES (
                    %(strategy_signal_id)s, %(order_request_id)s,
                    %(strategy_name)s, %(strategy_version)s, %(strategy_run_id)s,
                    %(signal_date)s, %(ticker_code)s, %(signal_type)s,
                    %(target_qty)s, %(target_price)s, %(target_weight)s,
                    %(execution_decision)s::jsonb
                )
                ON CONFLICT (order_request_id)
                DO NOTHING
                """,
                record,
            )
        conn.commit()


# ---------------------------------------------------------
# legacy dual-write helpers
# ---------------------------------------------------------
def save_balance_summary_legacy(record: Dict[str, Any]):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO balance_summary (
                    account_no,
                    cash_balance, nextday_exec_amt,
                    prev_closing_amt, total_eval_amount,
                    eval_profit, buy_amount_today,
                    sell_amount_today, fee_total_today,
                    as_of_date
                )
                VALUES (
                    %(account_no)s,
                    %(cash_balance)s, %(nextday_exec_amt)s,
                    %(prev_closing_amt)s, %(total_eval_amount)s,
                    %(eval_profit)s, %(buy_amount_today)s,
                    %(sell_amount_today)s, %(fee_total_today)s,
                    %(as_of_date)s
                )
                ON CONFLICT (account_no, as_of_date)
                DO UPDATE SET
                    cash_balance      = EXCLUDED.cash_balance,
                    nextday_exec_amt  = EXCLUDED.nextday_exec_amt,
                    prev_closing_amt  = EXCLUDED.prev_closing_amt,
                    total_eval_amount = EXCLUDED.total_eval_amount,
                    eval_profit       = EXCLUDED.eval_profit,
                    buy_amount_today  = EXCLUDED.buy_amount_today,
                    sell_amount_today = EXCLUDED.sell_amount_today,
                    fee_total_today   = EXCLUDED.fee_total_today
                """,
                record,
            )
        conn.commit()


def save_holdings_legacy(records: List[Dict[str, Any]]):
    if not records:
        return

    with get_conn() as conn:
        with conn.cursor() as cur:
            for r in records:
                cur.execute(
                    """
                    INSERT INTO holdings (
                        account_no, stock_code, stock_name,
                        quantity, avg_buy_price, buy_amount,
                        current_price, eval_amount, eval_profit,
                        as_of_date
                    )
                    VALUES (
                        %(account_no)s, %(stock_code)s, %(stock_name)s,
                        %(quantity)s, %(avg_buy_price)s, %(buy_amount)s,
                        %(current_price)s, %(eval_amount)s, %(eval_profit)s,
                        %(as_of_date)s
                    )
                    ON CONFLICT (account_no, stock_code, as_of_date)
                    DO UPDATE SET
                        quantity      = EXCLUDED.quantity,
                        avg_buy_price = EXCLUDED.avg_buy_price,
                        buy_amount    = EXCLUDED.buy_amount,
                        current_price = EXCLUDED.current_price,
                        eval_amount   = EXCLUDED.eval_amount,
                        eval_profit   = EXCLUDED.eval_profit
                    """,
                    r,
                )
        conn.commit()


def save_trade_orders_legacy(records: List[Dict[str, Any]]):
    if not records:
        return

    with get_conn() as conn:
        with conn.cursor() as cur:
            for r in records:
                cur.execute(
                    """
                    INSERT INTO trade_orders (
                        account_no, order_no, branch_code,
                        stock_code, stock_name, order_type,
                        side, order_qty, executed_qty,
                        avg_exec_price, total_exec_amount,
                        order_time, cancel_flag
                    )
                    VALUES (
                        %(account_no)s, %(order_no)s, %(branch_code)s,
                        %(stock_code)s, %(stock_name)s, %(order_type)s,
                        %(side)s, %(order_qty)s, %(executed_qty)s,
                        %(avg_exec_price)s, %(total_exec_amount)s,
                        %(order_time)s, %(cancel_flag)s
                    )
                    ON CONFLICT (order_no, branch_code)
                    DO UPDATE SET
                        executed_qty      = EXCLUDED.executed_qty,
                        avg_exec_price    = EXCLUDED.avg_exec_price,
                        total_exec_amount = EXCLUDED.total_exec_amount,
                        cancel_flag       = EXCLUDED.cancel_flag,
                        order_time        = EXCLUDED.order_time
                    """,
                    r,
                )
        conn.commit()


# ---------------------------------------------------------
# view query helpers
# ---------------------------------------------------------
def get_latest_balance_snapshot(account_no: Optional[str] = None) -> Optional[Dict[str, Any]]:
    if account_no:
        return _fetchone_dict(
            """
            SELECT
                id,
                account_id,
                account_no,
                as_of_date,
                as_of_ts,
                cash_balance,
                withdrawable_cash,
                orderable_cash,
                nextday_exec_amt,
                prev_closing_amt,
                total_eval_amount,
                eval_profit,
                buy_amount_today,
                sell_amount_today,
                fee_total_today,
                source_api,
                source_version,
                created_at
            FROM connector_balance_snapshot
            WHERE account_no = %s
            ORDER BY as_of_date DESC, as_of_ts DESC, id DESC
            LIMIT 1
            """,
            (account_no,),
        )

    return _fetchone_dict(
        """
        SELECT
            id,
            account_id,
            account_no,
            as_of_date,
            as_of_ts,
            cash_balance,
            withdrawable_cash,
            orderable_cash,
            nextday_exec_amt,
            prev_closing_amt,
            total_eval_amount,
            eval_profit,
            buy_amount_today,
            sell_amount_today,
            fee_total_today,
            source_api,
            source_version,
            created_at
        FROM connector_balance_snapshot
        ORDER BY as_of_date DESC, as_of_ts DESC, id DESC
        LIMIT 1
        """
    )


def get_latest_position_snapshots(account_no: Optional[str] = None) -> List[Dict[str, Any]]:
    latest_balance = get_latest_balance_snapshot(account_no=account_no)
    if not latest_balance:
        return []

    latest_account_no = latest_balance["account_no"]
    latest_as_of_date = latest_balance["as_of_date"]

    return _fetchall_dict(
        """
        SELECT
            id,
            account_id,
            account_no,
            ticker_code,
            stock_name,
            market,
            as_of_date,
            as_of_ts,
            quantity,
            sellable_quantity,
            avg_buy_price,
            buy_amount,
            current_price,
            eval_amount,
            eval_profit,
            eval_profit_rate,
            source_api,
            source_version,
            created_at
        FROM connector_position_snapshot
        WHERE account_no = %s
          AND as_of_date = %s
        ORDER BY eval_amount DESC NULLS LAST, ticker_code ASC
        """,
        (latest_account_no, latest_as_of_date),
    )


def list_order_requests(
    status: Optional[str] = None,
    ticker: Optional[str] = None,
    request_type: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
    account_no: Optional[str] = None,
) -> List[Dict[str, Any]]:
    where_clauses = ["1=1"]
    params: List[Any] = []

    if account_no:
        where_clauses.append("account_no = %s")
        params.append(account_no)

    if status:
        where_clauses.append("request_status = %s")
        params.append(status)

    if ticker:
        where_clauses.append("ticker_code = %s")
        params.append(ticker)

    if request_type:
        where_clauses.append("request_type = %s")
        params.append(request_type)

    params.extend([limit, offset])

    sql = f"""
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
            requested_at,
            accepted_at,
            last_event_at,
            created_at,
            updated_at
        FROM connector_order_request
        WHERE {" AND ".join(where_clauses)}
        ORDER BY requested_at DESC, id DESC
        LIMIT %s OFFSET %s
    """
    return _fetchall_dict(sql, tuple(params))


def count_order_requests(
    status: Optional[str] = None,
    ticker: Optional[str] = None,
    request_type: Optional[str] = None,
    account_no: Optional[str] = None,
) -> int:
    where_clauses = ["1=1"]
    params: List[Any] = []

    if account_no:
        where_clauses.append("account_no = %s")
        params.append(account_no)

    if status:
        where_clauses.append("request_status = %s")
        params.append(status)

    if ticker:
        where_clauses.append("ticker_code = %s")
        params.append(ticker)

    if request_type:
        where_clauses.append("request_type = %s")
        params.append(request_type)

    row = _fetchone_dict(
        f"""
        SELECT COUNT(*) AS cnt
        FROM connector_order_request
        WHERE {" AND ".join(where_clauses)}
        """,
        tuple(params),
    )
    return int(row["cnt"]) if row else 0


def get_order_request_by_id(order_request_id: int) -> Optional[Dict[str, Any]]:
    return _fetchone_dict(
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


def get_order_request_id_by_broker_order(
    broker_order_no: Optional[str],
    broker_branch_code: Optional[str] = None,
) -> Optional[int]:
    if not broker_order_no:
        return None

    row = _fetchone_dict(
        """
        SELECT id
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
    return int(row["id"]) if row else None


def get_latest_order_context(
    stock_code: Optional[str] = None,
    order_no: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    if order_no:
        row = _fetchone_dict(
            """
            SELECT
                id AS order_request_id,
                broker_order_no,
                broker_branch_code,
                ticker_code,
                order_qty,
                request_type
            FROM connector_order_request
            WHERE broker_order_no = %s
            ORDER BY requested_at DESC, id DESC
            LIMIT 1
            """,
            (order_no,),
        )
        if row:
            return row

    if stock_code:
        row = _fetchone_dict(
            """
            SELECT
                id AS order_request_id,
                broker_order_no,
                broker_branch_code,
                ticker_code,
                order_qty
            FROM connector_order_request
            WHERE ticker_code = %s
            ORDER BY requested_at DESC, id DESC
            LIMIT 1
            """,
            (stock_code,),
        )
        if row:
            return row

    return None


def list_child_order_requests(parent_order_request_id: int) -> List[Dict[str, Any]]:
    """
    특정 원 주문에서 파생된 모든 하위 주문을 재귀적으로 조회한다.

    예:
    BUY 10
      └─ MODIFY 11
           └─ CANCEL 12

    list_child_order_requests(10) 호출 시:
    - 11 depth=1
    - 12 depth=2
    를 모두 반환한다.

    주의:
    - 자기 자신(root)은 제외한다.
    - View에서 lifecycle 표시용으로 depth를 같이 내려준다.
    """
    return _fetchall_dict(
        """
        WITH RECURSIVE order_tree AS (
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
                requested_at,
                accepted_at,
                last_event_at,
                created_at,
                updated_at,
                1 AS depth,
                ARRAY[id] AS path
            FROM connector_order_request
            WHERE parent_order_request_id = %s

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
                c.requested_at,
                c.accepted_at,
                c.last_event_at,
                c.created_at,
                c.updated_at,
                ot.depth + 1 AS depth,
                ot.path || c.id AS path
            FROM connector_order_request c
            JOIN order_tree ot
              ON c.parent_order_request_id = ot.id
            WHERE NOT c.id = ANY(ot.path)
        )
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
            requested_at,
            accepted_at,
            last_event_at,
            created_at,
            updated_at,
            depth
        FROM order_tree
        ORDER BY path ASC
        """,
        (parent_order_request_id,),
    )

def delete_connector_position_snapshots(
    account_no: str,
    as_of_date,
) -> int:
    """
    특정 계좌/일자의 보유종목 스냅샷을 모두 삭제한다.

    사용 목적:
    - 잔고 API output1 기준으로 당일 보유종목 snapshot을 replace 처리하기 위함
    - output1이 빈 배열이면 기존 stale position row를 제거해서 View에 과거 보유종목이 남지 않게 함
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM connector_position_snapshot
                WHERE account_no = %s
                  AND as_of_date = %s
                """,
                (account_no, as_of_date),
            )
            deleted_count = cur.rowcount
        conn.commit()

    return deleted_count

def get_order_events_by_order_request_id(order_request_id: int) -> List[Dict[str, Any]]:
    return _fetchall_dict(
        """
        SELECT
            id,
            order_request_id,
            account_id,
            account_no,
            broker_order_no,
            broker_branch_code,
            ticker_code,
            stock_name,
            event_type,
            side,
            order_type_name,
            order_qty,
            executed_qty,
            remaining_qty,
            avg_exec_price,
            total_exec_amount,
            cancel_flag,
            order_date,
            order_time,
            event_ts,
            event_key,
            source_api,
            source_version,
            created_at
        FROM connector_order_event
        WHERE order_request_id = %s
        ORDER BY event_ts ASC NULLS LAST, id ASC
        """,
        (order_request_id,),
    )


def get_fills_by_order_request_id(order_request_id: int) -> List[Dict[str, Any]]:
    return _fetchall_dict(
        """
        SELECT
            id,
            order_request_id,
            order_event_id,
            account_id,
            broker_order_no,
            broker_branch_code,
            ticker_code,
            side,
            fill_seq,
            fill_qty,
            fill_price,
            fill_ts,
            fee_amount,
            tax_amount,
            created_at
        FROM connector_fill
        WHERE order_request_id = %s
        ORDER BY fill_ts ASC NULLS LAST, fill_seq ASC NULLS LAST, id ASC
        """,
        (order_request_id,),
    )


def get_order_request_detail(order_request_id: int) -> Optional[Dict[str, Any]]:
    request_row = get_order_request_by_id(order_request_id)
    if not request_row:
        return None

    events = get_order_events_by_order_request_id(order_request_id)
    fills = get_fills_by_order_request_id(order_request_id)
    children = list_child_order_requests(order_request_id)

    return {
        "request": request_row,
        "events": events,
        "fills": fills,
        "children": children,
    }


def get_dashboard_summary(account_no: Optional[str] = None, recent_limit: int = 5) -> Optional[Dict[str, Any]]:
    balance = get_latest_balance_snapshot(account_no=account_no)
    if not balance:
        return None

    positions = get_latest_position_snapshots(account_no=balance["account_no"])
    recent_orders = list_order_requests(
        account_no=balance["account_no"],
        limit=recent_limit,
        offset=0,
    )

    return {
        "balance": balance,
        "positions_count": len(positions),
        "recent_orders": recent_orders,
    }


def get_latest_realtime_quote(ticker_code: str) -> Optional[Dict[str, Any]]:
    return _fetchone_dict(
        """
        SELECT
            id,
            ticker_code,
            quote_ts,
            current_price,
            diff_price,
            diff_rate,
            volume,
            open_price,
            high_price,
            low_price,
            best_ask_price,
            best_bid_price,
            expected_match_price,
            expected_match_volume,
            source_api,
            source_version,
            created_at
        FROM connector_quote_realtime
        WHERE ticker_code = %s
        ORDER BY quote_ts DESC, id DESC
        LIMIT 1
        """,
        (ticker_code,),
    )


def get_eod_quotes(
    ticker_code: str,
    limit: int = 60,
    period_div: str = "D",
) -> List[Dict[str, Any]]:
    return _fetchall_dict(
        """
        SELECT
            id,
            ticker_code,
            trade_date,
            open_price,
            high_price,
            low_price,
            close_price,
            volume,
            trading_value,
            period_div,
            adjusted_price_yn,
            source_api,
            source_version,
            created_at
        FROM connector_quote_eod
        WHERE ticker_code = %s
          AND period_div = %s
        ORDER BY trade_date DESC, id DESC
        LIMIT %s
        """,
        (ticker_code, period_div, limit),
    )