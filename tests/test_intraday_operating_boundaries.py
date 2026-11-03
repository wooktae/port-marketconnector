"""Operational repair regressions with no broker, token or database writes."""
import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest

import connector_intraday_auto_run as auto
import connector_intraday_readonly as readonly
import connector_intraday_settings as settings


@pytest.mark.parametrize("day,minute,expected", [
    (3, "09:09:59", False), (3, "09:10:00", True), (3, "15:10:59", True),
    (3, "15:11:00", False), (3, "15:20:00", False), (3, "16:00:00", False),
    (7, "10:00:00", False), (8, "10:00:00", False),
])
def test_kst_window(day, minute, expected):
    instant = datetime.fromisoformat(f"2026-11-{day:02d}T{minute}+09:00")
    assert settings.intraday_window_open(instant.astimezone(timezone.utc)) is expected
    assert settings.intraday_window_open(instant) is expected


def test_naive_clock_is_rejected():
    assert not settings.intraday_window_open(datetime(2026, 11, 3, 10, tzinfo=timezone.utc).replace(tzinfo=None))


def test_root_trusts_only_named_application_owner_at_protected_default_path():
    path = MagicMock()
    path.__str__.return_value = settings.DEFAULT_PATH
    parent = Mock(lstat=Mock(return_value=SimpleNamespace(st_mode=0o40700, st_uid=1000)))
    path.parents = (parent,)
    with patch.object(settings, "os", SimpleNamespace(geteuid=lambda: 0)), \
            patch.dict(sys.modules, {"pwd": SimpleNamespace(getpwnam=lambda name: SimpleNamespace(pw_uid=1000))}):
        assert settings.trusted_owners(path) == {0, 1000}
        parent.lstat.return_value.st_mode = 0o40770
        with pytest.raises(ValueError, match="UNSAFE_INTRADAY_SETTINGS_DIRECTORY"):
            settings.trusted_owners(path)
        path.__str__.return_value = "/tmp/untrusted.json"
        assert settings.trusted_owners(path) == {0}


@pytest.mark.parametrize("mode,uid", [(0o120600, 0), (0o100622, 0), (0o100600, 999)])
def test_unsafe_settings_remain_blocked_and_clear_inherited_switch(mode, uid):
    env = {"INTRADAY_AUTO_SELL_ENABLED": "true"}
    with patch.object(settings, "os", SimpleNamespace(name="posix", geteuid=lambda: 0, environ=env)), \
            patch.object(Path, "exists", return_value=True), \
            patch.object(Path, "lstat", return_value=SimpleNamespace(st_mode=mode, st_uid=uid)), \
            pytest.raises(ValueError, match="UNSAFE_INTRADAY_SETTINGS_FILE"):
        settings.apply_settings("MOCK_SETTINGS")
    assert env["INTRADAY_AUTO_SELL_ENABLED"] == "false"


def broker(response=None):
    response = response or SimpleNamespace(status_code=200, headers={}, json=lambda: {"rt_cd": "0", "output1": []})
    get = Mock(return_value=response)
    sleeper = Mock()
    return readonly.BrokerReader(get, "MOCK_TOKEN", "https://openapivts.koreainvestment.com:29443",
                               "MOCK_KEY", "MOCK_SECRET", "TEST_ACCOUNT", "01", sleeper=sleeper), get


@pytest.mark.parametrize("response", [
    SimpleNamespace(status_code=500),
    SimpleNamespace(status_code=200, json=Mock(side_effect=ValueError)),
    SimpleNamespace(status_code=200, json=lambda: {"rt_cd": "1"}),
    SimpleNamespace(status_code=200, headers={}, json=lambda: {"rt_cd": "0", "output1": None}),
])
def test_read_only_http_json_and_broker_failures_do_not_retry(response):
    reader, get = broker(response)
    with patch.object(settings, "intraday_window_open", return_value=True), pytest.raises(readonly.ReadOnlyFailure):
        reader.holdings()
    assert get.call_count == 1


def test_read_only_get_is_paced_without_order_retry():
    reader, get = broker()
    with patch.object(settings, "intraday_window_open", return_value=True):
        assert reader.holdings() == {}
    reader.sleeper.assert_called_once_with(0.25)
    get.assert_called_once()


@pytest.mark.parametrize("value", [True, -1, "1.2", "NaN", "Infinity"])
def test_invalid_holding_quantity(value):
    with pytest.raises(readonly.ReadOnlyFailure):
        readonly.integer(value)


def test_missing_token_and_live_host_are_blocked():
    with pytest.raises(readonly.ReadOnlyFailure, match="EXISTING_TOKEN_REQUIRED"):
        readonly.BrokerReader(Mock(), None, "https://example.invalid", "KEY", "SECRET", "ACCOUNT", "01")
    with pytest.raises(readonly.ReadOnlyFailure, match="PAPER_BROKER_REQUIRED"):
        readonly.BrokerReader(Mock(), "MOCK", "https://openapi.koreainvestment.com", "KEY", "SECRET", "ACCOUNT", "01")


def test_delayed_reader_does_not_contact_broker():
    reader, get = broker()
    with patch.object(settings, "intraday_window_open", return_value=False), pytest.raises(readonly.ReadOnlyFailure):
        reader.holdings()
    get.assert_not_called()


def test_unknown_failed_order_does_not_infer_rejection_from_empty_history():
    reader, get = broker()
    assert reader.history({"broker_order_no": None}) is None
    get.assert_not_called()


def test_off_main_never_enters_writing_cycle(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["auto", "--account-no", "TEST_ACCOUNT"])
    monkeypatch.setattr(settings, "intraday_window_open", lambda: True)
    def off_settings():
        monkeypatch.setenv("INTRADAY_AUTO_SELL_ENABLED", "false")
        return {"enabled": False}
    monkeypatch.setattr(settings, "apply_settings", off_settings)
    run = Mock(return_value=0)
    monkeypatch.setattr(readonly, "run_read_only", run)
    with patch.object(auto, "run_cycle", side_effect=AssertionError("write path forbidden")):
        assert auto.main() == 0
    run.assert_called_once_with("TEST_ACCOUNT")


def test_late_ec2_main_skips_settings_api_and_db(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["auto", "--account-no", "TEST_ACCOUNT"])
    monkeypatch.setattr(settings, "intraday_window_open", lambda: False)
    with patch.object(settings, "apply_settings", side_effect=AssertionError("settings unreachable")):
        assert auto.main() == 0


def test_cycle_stops_before_evaluation_when_refresh_crosses_cutoff():
    store = Mock()
    store.cycle.return_value.__enter__ = Mock(return_value=True)
    store.cycle.return_value.__exit__ = Mock(return_value=False)
    refresh, evaluate, sell = Mock(return_value=0), Mock(), Mock()
    assert auto.run_cycle(store, "TEST_ACCOUNT", refresh, evaluate, sell, object(),
                          in_window=Mock(side_effect=[True, True, False])) == 0
    refresh.assert_called_once()
    evaluate.assert_not_called()
    sell.assert_not_called()


def test_future_and_naive_snapshots_cannot_trigger_stop():
    from connector_intraday_position_evaluate import evaluate_position
    now = datetime.now(timezone.utc)
    position = {"id": 1, "account_id": 2, "entry_price": 100, "remaining_qty": 2}
    for timestamp in (now + timedelta(hours=9), now.replace(tzinfo=None), now - timedelta(minutes=3)):
        result = evaluate_position(position, {"quantity": 2, "sellable_quantity": 2, "current_price": 89, "as_of_ts": timestamp},
                                   now_ts=now, hard_stop_rate=Decimal('-0.10'), max_snapshot_age_minutes=2,
                                   existing_order_lookup=lambda _: None)
        assert not result["should_stop"]


def test_dispatcher_delayed_delivery_does_not_send_ssm():
    source = Path(__file__).resolve().parents[1] / "deployment/intraday_dispatcher.py"
    ssm = Mock()
    with patch.dict(sys.modules, {"boto3": SimpleNamespace(client=lambda name: ssm)}):
        spec = importlib.util.spec_from_file_location("dispatcher_boundary_test", source)
        dispatcher = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(dispatcher)
    with patch.object(dispatcher, "_validated_dispatch", return_value=("TEST_ACCOUNT", "MOCK_INSTANCE", "MOCK_SCRIPT", "900")), \
            patch.object(dispatcher, "intraday_window_open", return_value=False):
        assert dispatcher.lambda_handler({}, None)["skipReason"] == "OUTSIDE_EXECUTION_WINDOW"
    ssm.send_command.assert_not_called()


def test_off_cycle_reads_kis_and_db_evaluates_threshold_without_writes(capsys):
    conn, cur = MagicMock(), MagicMock()
    conn.__enter__.return_value = conn
    conn.cursor.return_value.__enter__.return_value = cur
    position = {"id": 1, "account_id": 2, "account_no": "TEST_ACCOUNT", "ticker_code": "TEST_STOCK",
                "entry_price": 100, "remaining_qty": 2}
    cur.fetchone.side_effect = [{"value": "on"}, {"count": 1}, None]
    cur.fetchall.side_effect = [[position], []]
    reader = Mock()
    reader.holdings.return_value = {"TEST_STOCK": {"quantity": 2, "sellable_quantity": 2}}
    reader.price.return_value = Decimal(90)
    with patch.object(auto.Store, "_has_conflicts", return_value=False):
        assert readonly.verify("TEST_ACCOUNT", reader, connect=lambda: conn, in_window=lambda: True) == 0
    for call in cur.execute.call_args_list:
        assert call.args[0].lstrip().lower().startswith("select")
    conn.commit.assert_not_called()
    text = capsys.readouterr().out
    assert '"shouldStop": true' in text
    assert '"dbWrites": 0' in text
    assert "TEST_ACCOUNT" not in text


def test_off_cycle_rejects_writable_db_connection():
    conn, cur = MagicMock(), MagicMock()
    conn.__enter__.return_value = conn
    conn.cursor.return_value.__enter__.return_value = cur
    cur.fetchone.return_value = {"value": "off"}
    reader = Mock(holdings=Mock(return_value={}))
    with pytest.raises(readonly.ReadOnlyFailure, match="READ_ONLY_TRANSACTION_REQUIRED"):
        readonly.verify("TEST_ACCOUNT", reader, connect=lambda: conn, in_window=lambda: True)
    assert cur.execute.call_count == 1
