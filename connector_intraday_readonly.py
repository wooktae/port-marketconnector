"""OFF-mode intraday verification: broker GET and read-only SQL, never persistence.

Uses an existing token only. No token issuance, API-call logging, order submission,
snapshot save, check insert, fill task or notification is reachable from this path.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
import json


class ReadOnlyFailure(RuntimeError):
    pass


def emit(stage, **fields):
    print(json.dumps({"component": "intraday_readonly", "stage": stage, **fields}, default=str))


def integer(value):
    if isinstance(value, bool):
        raise ReadOnlyFailure("INVALID_KIS_QUANTITY")
    number = Decimal(str(value))
    if not number.is_finite() or number < 0 or number != int(number):
        raise ReadOnlyFailure("INVALID_KIS_QUANTITY")
    return int(number)


class BrokerReader:
    def __init__(self, get, token, base_url, key, secret, account, product):
        if not token:
            raise ReadOnlyFailure("EXISTING_TOKEN_REQUIRED")
        from urllib.parse import urlparse
        if urlparse(base_url).hostname != "openapivts.koreainvestment.com":
            raise ReadOnlyFailure("PAPER_BROKER_REQUIRED")
        self.get, self.base_url = get, base_url
        self.headers = {"authorization": "Bearer " + token, "appkey": key,
                        "appsecret": secret, "custtype": "P", "Content-Type": "application/json"}
        self.account, self.product = account, product

    def query(self, endpoint, tr_id, params):
        from connector_intraday_settings import intraday_window_open
        if not intraday_window_open():
            raise ReadOnlyFailure("OUTSIDE_EXECUTION_WINDOW")
        try:
            headers = {**self.headers, "tr_id": tr_id}
            if params.get("CTX_AREA_FK100") or params.get("CTX_AREA_NK100"):
                headers["tr_cont"] = "N"
            response = self.get(self.base_url + endpoint, headers=headers,
                                params=params, timeout=20)
            if response.status_code != 200:
                raise ReadOnlyFailure("KIS_HTTP_ERROR")
            data = response.json()
        except ReadOnlyFailure:
            raise
        except Exception:
            raise ReadOnlyFailure("KIS_TRANSPORT_OR_JSON_ERROR") from None
        if not isinstance(data, dict) or data.get("rt_cd") != "0":
            raise ReadOnlyFailure("KIS_QUERY_REJECTED")
        return data, response.headers.get("tr_cont", "")

    def holdings(self):
        params = {"CANO": self.account, "ACNT_PRDT_CD": self.product, "AFHR_FLPR_YN": "N",
                  "OFL_YN": "", "INQR_DVSN": "02", "UNPR_DVSN": "01", "FUND_STTL_ICLD_YN": "N",
                  "FNCG_AMT_AUTO_RDPT_YN": "N", "PRCS_DVSN": "00", "CTX_AREA_FK100": "", "CTX_AREA_NK100": ""}
        holdings, seen = {}, set()
        for _ in range(10):
            data, continuation = self.query("/uapi/domestic-stock/v1/trading/inquire-balance", "VTTC8434R", params)
            if not isinstance(data.get("output1"), list):
                raise ReadOnlyFailure("INVALID_KIS_HOLDINGS")
            for row in data["output1"]:
                if not isinstance(row, dict) or not row.get("pdno") or row["pdno"] in holdings:
                    raise ReadOnlyFailure("INVALID_OR_DUPLICATE_KIS_HOLDING")
                holdings[row["pdno"]] = {"quantity": integer(row.get("hldg_qty")),
                                        "sellable_quantity": integer(row.get("ord_psbl_qty"))}
            if continuation not in ("F", "M"):
                return holdings
            cursor = (data.get("ctx_area_fk100"), data.get("ctx_area_nk100"))
            if not any(cursor) or cursor in seen:
                raise ReadOnlyFailure("INVALID_KIS_PAGINATION")
            seen.add(cursor)
            params.update(CTX_AREA_FK100=cursor[0] or "", CTX_AREA_NK100=cursor[1] or "")
        raise ReadOnlyFailure("KIS_PAGINATION_LIMIT")

    def price(self, ticker):
        data, _ = self.query("/uapi/domestic-stock/v1/quotations/inquire-price", "FHKST01010100",
                             {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker})
        output = data.get("output")
        if not isinstance(output, dict):
            raise ReadOnlyFailure("INVALID_KIS_PRICE")
        try:
            price = Decimal(str(output["stck_prpr"]))
            if not price.is_finite() or price <= 0:
                raise ValueError
        except Exception:
            raise ReadOnlyFailure("INVALID_KIS_PRICE") from None
        return price

    def history(self, request):
        # Never fall back to an unscoped account history to infer non-acceptance.
        if not request.get("broker_order_no") or not request.get("requested_at"):
            return None
        day = request["requested_at"].strftime("%Y%m%d")
        data, continuation = self.query("/uapi/domestic-stock/v1/trading/inquire-daily-ccld", "VTTC8001R", {
            "CANO": self.account, "ACNT_PRDT_CD": self.product,
            "INQR_STRT_DT": day, "INQR_END_DT": day, "SLL_BUY_DVSN_CD": "01", "INQR_DVSN": "00",
            "PDNO": request["ticker_code"], "CCLD_DVSN": "00",
            "ORD_GNO_BRNO": request.get("broker_branch_code") or "", "ODNO": request["broker_order_no"],
            "INQR_DVSN_3": "00", "INQR_DVSN_1": "", "INQR_DVSN_2": "",
            "CTX_AREA_FK100": "", "CTX_AREA_NK100": ""})
        if not isinstance(data.get("output1"), list) or continuation in ("F", "M"):
            raise ReadOnlyFailure("INCOMPLETE_KIS_HISTORY")
        matches = [r for r in data["output1"] if isinstance(r, dict)
                   and r.get("odno") == request["broker_order_no"] and r.get("pdno") == request["ticker_code"]]
        return {"matchedRows": len(matches), "hasFills": any(integer(r.get("tot_ccld_qty", "0")) > 0 for r in matches)}


@contextmanager
def read_only_connection():
    import psycopg
    from psycopg.rows import dict_row
    from db_config import get_db_config
    config = get_db_config()
    config.update(options="-c default_transaction_read_only=on -c statement_timeout=10000", connect_timeout=10)
    with psycopg.connect(**config, row_factory=dict_row) as conn:
        try:
            yield conn
        finally:
            conn.rollback()


def verify(account, broker, connect=read_only_connection, *, in_window=None):
    from connector_intraday_settings import intraday_window_open
    from connector_intraday_auto_run import Store
    from connector_intraday_position_evaluate import evaluate_position
    in_window = in_window or intraday_window_open
    if not in_window():
        emit("OUTSIDE_EXECUTION_WINDOW")
        return 0
    holdings = broker.holdings()
    emit("KIS_HOLDINGS_OK", holdingCount=len(holdings))
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("select current_setting('transaction_read_only') as value")
            if cur.fetchone()["value"] != "on":
                raise ReadOnlyFailure("READ_ONLY_TRANSACTION_REQUIRED")
            cur.execute("""select p.* from execution.strategy_position_state p
                join connector.connector_account a on a.id=p.account_id and a.account_no=p.account_no
                    and a.environment='paper' and a.broker_name='koreainvestment'
                where p.account_no=%s and p.position_status='OPEN' order by p.id""", (account,))
            positions = list(cur.fetchall())
            cur.execute("select count(*) as count from execution.strategy_position_state where account_no=%s and position_status='OPEN'", (account,))
            if cur.fetchone()["count"] != len(positions):
                raise ReadOnlyFailure("POSITION_ACCOUNT_LINK_MISMATCH")
            emit("DB_ENTRY_PRICES_OK", openPositionCount=len(positions))
            cur.execute("""select r.*, o.id as execution_id from execution.strategy_execution_order o
                left join connector.connector_order_request r on r.id=o.connector_order_request_id
                    and r.account_id=o.account_id and r.ticker_code=o.ticker_code and r.request_type='SELL'
                where o.account_no=%s and o.action_type='SELL' and o.execution_status='FAILED'""", (account,))
            failed = list(cur.fetchall())
            for request in failed:
                if not in_window():
                    emit("OUTSIDE_EXECUTION_WINDOW")
                    return 0
                proof = Store._confirmed_unaccepted(cur, request)
                history = broker.history(request)
                emit("FAILED_SELL_EVIDENCE", executionOrderId=request["execution_id"],
                     explicitRejectionWithoutLocalFills=proof, brokerHistory=history,
                     reconciliationRequired=not proof)
            store = Store(None, None)
            for position in positions:
                if not in_window():
                    emit("OUTSIDE_EXECUTION_WINDOW")
                    return 0
                price = broker.price(position["ticker_code"])
                held = holdings.get(position["ticker_code"])
                now = datetime.now(timezone.utc)
                snapshot = ({**held, "current_price": price, "as_of_ts": now} if held else None)
                cur.execute("""select id from execution.strategy_execution_order
                    where source_position_state_id=%s and source_type='INTRADAY_STOP_SELL'
                      and action_type='SELL' and execution_status in
                      ('READY','REQUESTED','SUBMITTING','SUBMITTED','ACCEPTED','PARTIAL_FILLED') limit 1""", (position["id"],))
                duplicate = cur.fetchone()
                result = evaluate_position(position, snapshot, now_ts=now,
                    hard_stop_rate=Decimal('-0.10'), max_snapshot_age_minutes=2,
                    existing_order_lookup=lambda _: duplicate)
                blocked = store._has_conflicts(cur, {**position, "id": -1})
                emit("POSITION_EVALUATED", positionId=position["id"], ticker=position["ticker_code"],
                     entryPrice=position["entry_price"], currentPrice=price,
                     strategyQty=position["remaining_qty"], holdingQty=held["quantity"] if held else 0,
                     sellableQty=held["sellable_quantity"] if held else 0,
                     pnlRate=result["pnl_rate"], shouldStop=result["should_stop"],
                     activeSellBlocked=blocked, warnings=result["warnings"], killSwitch="OFF")
    emit("READ_ONLY_SUCCESS", killSwitch="OFF", dbWrites=0, brokerOrders=0)
    return 0


def run_read_only(account):
    try:
        import os
        import requests
        from config import APP_KEY, APP_SECRET, BASE_URL, PAPER_ACNT, ACNT_PRDT_CD
        from token_manager import load_token
        if (account != PAPER_ACNT or os.environ.get("INTRADAY_AUTO_SELL_ENABLED") != "false"
                or os.environ.get("PORT_ENVIRONMENT") != "paper"
                or os.environ.get("PORT_DB_TARGET") != "aws-paper"):
            raise ReadOnlyFailure("READ_ONLY_ENVIRONMENT_MISMATCH")
        broker = BrokerReader(requests.get, load_token(), BASE_URL, APP_KEY, APP_SECRET, account, ACNT_PRDT_CD)
        return verify(account, broker)
    except Exception as exc:
        # Exception messages from HTTP/DB libraries can include credentials.
        emit("READ_ONLY_FAILED", errorType=type(exc).__name__,
             reason=str(exc) if type(exc) is ReadOnlyFailure else "QUERY_OR_VALIDATION_FAILED")
        return 1
