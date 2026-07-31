from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

EXPECTED_ENTRYPOINTS = {
    "connector_app.py",
    "connector_balance.py",
    "connector_buy.py",
    "connector_cancel.py",
    "connector_intraday_position_evaluate.py",
    "connector_intraday_snapshot_refresh.py",
    "connector_modify.py",
    "connector_order_check.py",
    "connector_quote_closed.py",
    "connector_quote_realtime.py",
    "connector_sell.py",
    "connector_strategy_order_execute.py",
    "token_manager.py",
}

ORDER_ENTRYPOINTS = {
    "connector_buy.py",
    "connector_sell.py",
    "connector_cancel.py",
    "connector_modify.py",
    "connector_strategy_order_execute.py",
}


def parse_source(path: Path) -> ast.Module:
    return ast.parse(
        path.read_text(encoding="utf-8-sig"),
        filename=str(path),
    )


def has_main_guard(tree: ast.Module) -> bool:
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue

        test = node.test

        if not isinstance(test, ast.Compare):
            continue

        if not isinstance(test.left, ast.Name):
            continue

        if test.left.id != "__name__":
            continue

        if len(test.ops) != 1 or not isinstance(test.ops[0], ast.Eq):
            continue

        if len(test.comparators) != 1:
            continue

        comparator = test.comparators[0]

        if (
            isinstance(comparator, ast.Constant)
            and comparator.value == "__main__"
        ):
            return True

    return False


def test_expected_entrypoints_exist() -> None:
    missing = sorted(
        name
        for name in EXPECTED_ENTRYPOINTS
        if not (ROOT / name).is_file()
    )

    assert not missing, f"Missing entrypoints: {missing}"


def test_all_root_python_files_parse() -> None:
    failures: list[str] = []

    for path in sorted(ROOT.glob("*.py")):
        try:
            parse_source(path)
        except SyntaxError as exc:
            failures.append(
                f"{path.name}:{exc.lineno}:{exc.msg}"
            )

    assert not failures, (
        "Python syntax failures: " + ", ".join(failures)
    )


def test_order_entrypoints_keep_main_guard() -> None:
    missing = sorted(
        name
        for name in ORDER_ENTRYPOINTS
        if not has_main_guard(parse_source(ROOT / name))
    )

    assert not missing, (
        "Order entrypoints without __main__ guard: "
        + ", ".join(missing)
    )


def test_access_token_is_ignored() -> None:
    gitignore = (ROOT / ".gitignore").read_text(
        encoding="utf-8-sig"
    )

    assert "access_token.txt" in gitignore
    assert "token*.txt" in gitignore


def test_daily_wrapper_keeps_paper_guards() -> None:
    wrapper = ROOT / "scripts" / "run_connector_balance_daily.sh"

    assert wrapper.is_file()

    content = wrapper.read_text(encoding="utf-8-sig")

    assert 'PORT_ENVIRONMENT="paper"' in content
    assert 'PORT_DB_TARGET="aws-paper"' in content