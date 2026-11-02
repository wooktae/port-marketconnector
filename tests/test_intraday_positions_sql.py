"""Offline regression tests for fetch_latest_positions SQL bindings.

Compile only the actual function AST, without importing application/config/token
modules. The only database entrypoint is an injected Mock connection/cursor.
"""

import ast
import unittest
from pathlib import Path
from typing import Any, Optional
from unittest.mock import MagicMock, Mock, patch

SOURCE = Path(__file__).resolve().parents[1] / "connector_intraday_position_evaluate.py"


class FetchLatestPositionsTests(unittest.TestCase):
    def setUp(self):
        # Block network access and external processes even if the function regresses.
        for target in (
            "socket.socket",
            "socket.create_connection",
            "urllib.request.urlopen",
            "subprocess.Popen",
        ):
            guard = patch(target, side_effect=AssertionError("External call forbidden"))
            blocked_call = guard.start()
            self.addCleanup(guard.stop)
            self.addCleanup(blocked_call.assert_not_called)

        self.cursor = MagicMock()
        self.cursor.__enter__.return_value = self.cursor
        self.cursor.fetchall.return_value = []
        self.conn = MagicMock()
        self.conn.__enter__.return_value = self.conn
        self.conn.cursor.return_value = self.cursor
        self.get_conn = Mock(return_value=self.conn)
        self.row_factory = object()

        tree = ast.parse(SOURCE.read_text(encoding="utf-8-sig"))
        function = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "fetch_latest_positions"
        )
        namespace = {
            "Any": Any, "Dict": dict, "List": list, "Optional": Optional,
            "get_conn": self.get_conn, "dict_row": self.row_factory,
        }
        isolated = ast.Module(body=[function], type_ignores=[])
        exec(compile(isolated, str(SOURCE), "exec"), namespace)  # noqa: S102 - Isolated test code with synthetic dependencies only.
        self.fetch = namespace["fetch_latest_positions"]

    def assert_query(self, expected_params, account_filtered):
        self.get_conn.assert_called_once_with()
        self.conn.cursor.assert_called_once_with(row_factory=self.row_factory)
        self.cursor.execute.assert_called_once()
        sql, params = self.cursor.execute.call_args.args
        self.assertIsInstance(params, tuple)
        self.assertEqual(params, expected_params)
        self.assertEqual(sql.count("%s"), len(expected_params))
        normalized = " ".join(sql.split()).lower()
        self.assertIn("with latest_date as ( select max(as_of_date) as as_of_date", normalized)
        self.assertIn("join latest_date ld on ld.as_of_date = cps.as_of_date", normalized)
        self.assertIn("where (%s::varchar is null or cps.account_no = %s)", normalized)
        self.assertIn("order by cps.ticker_code", normalized)
        cte = normalized.split(") select cps.*", 1)[0]
        self.assertEqual("where account_no = %s" in cte, account_filtered)
        return sql

    def test_account_has_three_ordered_parameters(self):
        self.fetch("TEST_ACCOUNT")
        self.assert_query(("TEST_ACCOUNT",) * 3, account_filtered=True)

    def test_no_account_keeps_two_null_parameters(self):
        self.fetch(None)
        self.assert_query((None, None), account_filtered=False)

    def test_empty_account_keeps_unfiltered_behavior(self):
        self.fetch("")
        self.assert_query((None, None), account_filtered=False)

    def test_account_is_bound_not_interpolated(self):
        account = "TEST' OR 'a'='a"
        self.fetch(account)
        sql = self.assert_query((account,) * 3, account_filtered=True)
        self.assertNotIn(account, sql)

    def test_empty_rows_return_empty_dictionary(self):
        result = self.fetch("TEST_ACCOUNT")
        self.assertEqual(result, {})
        self.assertIsInstance(result, dict)

    def test_rows_return_copied_dictionaries_keyed_by_ticker(self):
        rows = [
            {"ticker_code": "TEST_A", "quantity": 2},
            {"ticker_code": "TEST_B", "quantity": 3},
        ]
        self.cursor.fetchall.return_value = rows
        result = self.fetch("TEST_ACCOUNT")
        self.assertEqual(result, {row["ticker_code"]: row for row in rows})
        self.assertIsNot(result["TEST_A"], rows[0])
        self.cursor.fetchall.assert_called_once_with()

    def test_execute_exception_propagates(self):
        error = RuntimeError("synthetic SQL failure")
        self.cursor.execute.side_effect = error
        with self.assertRaises(RuntimeError) as caught:
            self.fetch("TEST_ACCOUNT")
        self.assertIs(caught.exception, error)
        self.cursor.fetchall.assert_not_called()

    def test_fetch_exception_propagates(self):
        error = RuntimeError("synthetic fetch failure")
        self.cursor.fetchall.side_effect = error
        with self.assertRaises(RuntimeError) as caught:
            self.fetch(None)
        self.assertIs(caught.exception, error)


if __name__ == "__main__":
    unittest.main()
