"""Offline tests: settings, ID-scoped history, ECS fill handoff, no real I/O."""
import ast
from datetime import datetime, timezone
import importlib.util
import json
import sys
import types
from pathlib import Path
import unittest
from unittest.mock import MagicMock, Mock, patch
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


settings = load("connector_intraday_settings")
reconcile = load("connector_intraday_reconcile")
auto = load("connector_intraday_auto_run")


class PrepareTests(unittest.TestCase):
    def setUp(self):
        for target in ("socket.socket", "socket.create_connection", "urllib.request.urlopen", "subprocess.Popen"):
            guard = patch(target, side_effect=AssertionError("External access forbidden"))
            blocked = guard.start()
            self.addCleanup(guard.stop)
            self.addCleanup(blocked.assert_not_called)

    def config(self):
        return dict(cluster="MOCK_CLUSTER", taskDefinition="mock-execution:10", container="strategy-execution",
                    subnets=["subnet-abc"], securityGroups=["sg-abc"], assignPublicIp="DISABLED")

    def row(self):
        return dict(id=10, account_id=2, ticker_code="TEST_STOCK", connector_order_request_id=7,
                    broker_order_no="MOCK_ORDER", broker_branch_code="MOCK_BRANCH",
                    requested_at=datetime.now(timezone.utc), connector_status="ACCEPTED")

    def test_missing_file_overrides_inherited_true_with_off(self):
        with patch.dict(settings.os.environ, {"INTRADAY_AUTO_SELL_ENABLED": "true"}), \
                patch.object(Path, "exists", return_value=False):
            result = settings.apply_settings("MOCK_SETTINGS")
            self.assertFalse(result["enabled"])
            self.assertEqual(settings.os.environ["INTRADAY_AUTO_SELL_ENABLED"], "false")

    def test_explicit_json_boolean_and_invalid_string(self):
        metadata = Mock(st_mode=0o100600, st_uid=0)
        for value in (True, False, "true"):
            with self.subTest(value=value), patch.dict(settings.os.environ, {}), \
                    patch.object(Path, "exists", return_value=True), \
                    patch.object(Path, "lstat", return_value=metadata), \
                    patch.object(Path, "read_text", return_value=json.dumps({"enabled": value, "fillSync": {}})):
                if isinstance(value, str):
                    with self.assertRaises(ValueError):
                        settings.apply_settings("MOCK_SETTINGS")
                else:
                    settings.apply_settings("MOCK_SETTINGS")
                    self.assertEqual(auto.enabled(), value)

    def test_deployment_default_off_and_manifest(self):
        config = json.loads((ROOT / "deployment/intraday-auto.default.json").read_text())
        self.assertIs(config["enabled"], False)
        manifest = (ROOT / ".devops/bundle/include.txt").read_text()
        for name in ("connector_intraday_auto_run.py", "connector_intraday_settings.py",
                     "connector_intraday_reconcile.py", "deployment/intraday-auto.default.json"):
            self.assertIn(name, manifest)
            self.assertTrue((ROOT / name).is_file())
        with self.assertRaises(ValueError):
            reconcile.validate_fill_config(config["fillSync"])

    def test_scoped_history_then_fill_handoff(self):
        store = Mock(tracked=Mock(return_value=[self.row()]))
        query, sync = Mock(return_value={"rt_cd": "0"}), Mock()
        ids = reconcile.reconcile(store, "TEST_ACCOUNT", "RUN", self.config(), query, sync=sync)
        self.assertEqual(ids, [10])
        self.assertFalse(query.call_args.kwargs["try_broad_search_if_empty"])
        self.assertEqual(query.call_args.kwargs["expected_order_request_id"], 7)
        self.assertEqual(query.call_args.kwargs["order_no"], "MOCK_ORDER")
        sync.assert_called_once_with(self.config(), 2, [10], "RUN")

    def test_stored_filled_request_skips_requery_but_syncs(self):
        row = self.row()
        row["connector_status"] = "FILLED"
        query, sync = Mock(), Mock()
        reconcile.reconcile(Mock(tracked=Mock(return_value=[row])), "TEST_ACCOUNT", "RUN",
                            self.config(), query, sync=sync)
        query.assert_not_called()
        sync.assert_called_once()

    def test_query_failure_or_missing_branch_never_starts_fill_task(self):
        for branch, result in ((None, {"rt_cd": "0"}), ("MOCK_BRANCH", None),
                               ("MOCK_BRANCH", {"rt_cd": "1"})):
            row = self.row()
            row["broker_branch_code"] = branch
            query, sync = Mock(return_value=result), Mock()
            with self.subTest(branch=branch, result=result), self.assertRaises((ValueError, RuntimeError)):
                reconcile.reconcile(Mock(tracked=Mock(return_value=[row])), "TEST_ACCOUNT", "RUN",
                                    self.config(), query, sync=sync)
            sync.assert_not_called()

    def test_empty_tracking_never_calls_aws_or_kis(self):
        query, sync = Mock(), Mock()
        self.assertEqual(reconcile.reconcile(Mock(tracked=Mock(return_value=[])), "TEST_ACCOUNT", "RUN",
                                            {}, query, sync=sync), [])
        query.assert_not_called()
        sync.assert_not_called()

    def test_ecs_command_exact_ids_and_old_image_guard(self):
        call = Mock(side_effect=[{"tasks": [{"taskArn": "MOCK_TASK"}]},
                                 {"tasks": [{"lastStatus": "STOPPED", "containers": [
                                     {"name": "strategy-execution", "exitCode": 0}]}]}])
        reconcile.sync_ecs(self.config(), 2, [10, 11], "RUN", call=call, sleep=Mock())
        request = call.call_args_list[0].args[0]
        command = json.loads(request[request.index("--overrides") + 1])["containerOverrides"][0]["command"]
        self.assertEqual(command[:2], ["python", "-c"])
        self.assertIn("intraday-id-scope-v1", command[2])
        self.assertEqual(command[3:], ["--intraday-account-id", "2", "--intraday-order-id", "10",
                                        "--intraday-order-id", "11"])
        self.assertNotIn("TEST_ACCOUNT", str(request))
        legacy = types.ModuleType("execution_sync_sell_fill")
        legacy.main = Mock()
        with patch.dict(sys.modules, {"execution_sync_sell_fill": legacy}), self.assertRaises(RuntimeError):
            exec(compile(command[2], "<bootstrap>", "exec", optimize=2), {})
        legacy.main.assert_not_called()

    def test_ecs_failure_exit_or_timeout_is_not_relaunched(self):
        for state in ({"tasks": [{"lastStatus": "STOPPED", "containers": [
                          {"name": "strategy-execution", "exitCode": 1}]}]},
                      {"tasks": [{"lastStatus": "RUNNING"}]}):
            def respond(args):
                return {"tasks": [{"taskArn": "MOCK_TASK"}]} if args[1] == "run-task" else state
            call = Mock(side_effect=respond)
            with self.assertRaises(RuntimeError):
                reconcile.sync_ecs(self.config(), 2, [10], "RUN", call=call, sleep=Mock())
            self.assertEqual(sum(c.args[0][1] == "run-task" for c in call.call_args_list), 1)

    def test_ecs_preflight_passes_no_order_ids(self):
        call = Mock(side_effect=[{"tasks": [{"taskArn": "MOCK_TASK"}]},
                                 {"tasks": [{"lastStatus": "STOPPED", "containers": [
                                     {"name": "strategy-execution", "exitCode": 0}]}]}])
        reconcile.sync_ecs(self.config(), None, [], "RUN", preflight=True, call=call, sleep=Mock())
        args = call.call_args_list[0].args[0]
        command = json.loads(args[args.index("--overrides") + 1])["containerOverrides"][0]["command"]
        self.assertEqual(command[3:], ["--intraday-preflight"])

    def test_fill_observer_failure_blocks_new_submission(self):
        from contextlib import contextmanager
        @contextmanager
        def cycle(account):
            yield True
        store, refresh, evaluate, sell = Mock(), Mock(), Mock(), Mock()
        store.cycle = cycle
        status = auto.run_cycle(store, "TEST_ACCOUNT", refresh, evaluate, sell, object(),
                                observe=Mock(side_effect=RuntimeError("synthetic error")))
        self.assertEqual(status, 1)
        refresh.assert_not_called()
        sell.assert_not_called()

    def history_function(self):
        path = ROOT / "connector_order_check.py"
        fn = next(n for n in ast.parse(path.read_text(encoding="utf-8-sig")).body
                  if isinstance(n, ast.FunctionDef) and n.name == "fetch_and_save_orders")
        cur = MagicMock()
        cur.__enter__.return_value = cur
        cur.fetchone.return_value = (7,)
        conn = MagicMock()
        conn.__enter__.return_value = conn
        conn.cursor.return_value = cur
        ns = dict(Optional=Optional, PAPER_ACNT="TEST_ACCOUNT", ACNT_PRDT_CD="MOCK",
                  ensure_connector_account=Mock(return_value=2), get_conn=Mock(return_value=conn),
                  _build_params=Mock(return_value={}), _call_order_history=Mock(), print=Mock(),
                  json=json, t=lambda a, b: "MOCK", _process_detail_orders=Mock(),
                  _process_summary_fallback=Mock(), _to_int=int, _to_float=float)
        exec(compile(ast.Module(body=[fn], type_ignores=[]), str(path), "exec"), ns)
        return ns

    def test_history_scoped_rejects_other_response_rows_before_writes(self):
        ns = self.history_function()
        ns["_call_order_history"].return_value = ({"rt_cd": "0", "output1": [
            {"pdno": "OTHER", "odno": "MOCK_ORDER", "ord_gno_brno": "MOCK_BRANCH"}]}, None, None)
        with self.assertRaises(ValueError):
            ns["fetch_and_save_orders"]("20261102", "20261102", "TEST_STOCK", "MOCK_ORDER", "MOCK_BRANCH",
                                         False, expected_order_request_id=7)
        ns["_process_detail_orders"].assert_not_called()
        ns["_process_summary_fallback"].assert_not_called()

    def test_history_scoped_forwards_expected_id_and_default_daily_unchanged(self):
        for scoped in (False, True):
            ns = self.history_function()
            ns["_call_order_history"].return_value = ({"rt_cd": "0", "output1": [
                {"pdno": "TEST_STOCK", "odno": "MOCK_ORDER", "ord_gno_brno": "MOCK_BRANCH"}]}, None, None)
            kwargs = {"expected_order_request_id": 7} if scoped else {}
            ns["fetch_and_save_orders"]("20261102", "20261102", "TEST_STOCK", "MOCK_ORDER", "MOCK_BRANCH",
                                         not scoped, **kwargs)
            self.assertEqual(ns["_process_detail_orders"].call_args.kwargs,
                             {"expected_request_id": 7} if scoped else {})

    def test_detail_and_summary_helpers_reject_wrong_request_before_writes(self):
        path = ROOT / "connector_order_check.py"
        names = {"_process_detail_orders", "_process_summary_fallback"}
        functions = [n for n in ast.parse(path.read_text(encoding="utf-8-sig")).body
                     if isinstance(n, ast.FunctionDef) and n.name in names]
        ns = dict(Optional=Optional, _parse_order_datetime=Mock(), _find_order_request_id=Mock(return_value=99),
                  _to_int=int, _to_float=float, print=Mock(), t=lambda a, b: "MOCK",
                  _count_summary_fallback_candidates=Mock(return_value=[(99,)]),
                  _find_latest_order_context=Mock(return_value={"order_request_id": 99}),
                  save_trade_orders_legacy=Mock(), insert_order_event=Mock(), upsert_fill=Mock(),
                  update_order_request_after_response=Mock())
        exec(compile(ast.Module(body=functions, type_ignores=[]), str(path), "exec"), ns)
        with self.assertRaises(ValueError):
            ns["_process_detail_orders"](2, [{"odno": "MOCK", "ord_gno_brno": "MOCK"}], expected_request_id=7)
        with self.assertRaises(ValueError):
            ns["_process_summary_fallback"](2, {"output2": {"tot_ord_qty": "2"}},
                                           "TEST_STOCK", "MOCK", "MOCK", expected_request_id=7)
        ns["_count_summary_fallback_candidates"].return_value = [(7,)]
        with self.assertRaises(ValueError):
            ns["_process_summary_fallback"](2, {"output2": {"tot_ord_qty": "2"}},
                                           "TEST_STOCK", "MOCK", "MOCK", expected_request_id=7)
        for name in ("save_trade_orders_legacy", "insert_order_event", "upsert_fill", "update_order_request_after_response"):
            ns[name].assert_not_called()


if __name__ == "__main__":
    unittest.main()
