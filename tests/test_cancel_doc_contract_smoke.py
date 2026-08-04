"""Smoke test: connector_cancel.py 문서 계약 정적 확인 (C8).

이 테스트는 broker/DB/token side effect 없이 `connector_cancel.py` 소스만 정적으로
읽어 docstring과 CLI help 텍스트 계약을 검증한다. 모듈을 import하지 않으므로
`from connector_order_common import submit_rvsecncl_order` 같은 import 경로도 실행되지
않으며, argparse 파서를 실제로 구성하거나 CLI entrypoint를 실행하지 않는다.

검증 대상 계약(Requirements 4.10, 4.11):
- 수량 생략(--qty 미지정)은 전량 취소, 수량 지정은 부분 취소임을 문서가 반영한다.
- 전량 취소 TR ID / 취소 구분 코드는 Paper 환경 동작 확인 수준으로만 기술하고,
  실 환경(live) 검증 주장으로 확장하지 않는다.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CANCEL_PATH = ROOT / "connector_cancel.py"


def _parse_source() -> ast.Module:
    return ast.parse(
        CANCEL_PATH.read_text(encoding="utf-8-sig"),
        filename=str(CANCEL_PATH),
    )


def _module_docstring(tree: ast.Module) -> str:
    doc = ast.get_docstring(tree)
    assert doc is not None, "connector_cancel.py 모듈 docstring이 없다"
    return doc


def _function_docstring(tree: ast.Module, name: str) -> str:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            doc = ast.get_docstring(node)
            assert doc is not None, f"{name}() docstring이 없다"
            return doc

    raise AssertionError(f"{name}() 정의를 찾지 못했다")


def _argparse_description(tree: ast.Module) -> str:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        func = node.func
        is_parser_ctor = (
            isinstance(func, ast.Attribute) and func.attr == "ArgumentParser"
        ) or (isinstance(func, ast.Name) and func.id == "ArgumentParser")

        if not is_parser_ctor:
            continue

        for keyword in node.keywords:
            if (
                keyword.arg == "description"
                and isinstance(keyword.value, ast.Constant)
                and isinstance(keyword.value.value, str)
            ):
                return keyword.value.value

    raise AssertionError("argparse description 문자열을 찾지 못했다")


def _qty_argument_help(tree: ast.Module) -> str:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "add_argument"):
            continue

        if not node.args:
            continue

        first = node.args[0]
        if not (isinstance(first, ast.Constant) and first.value == "--qty"):
            continue

        for keyword in node.keywords:
            if (
                keyword.arg == "help"
                and isinstance(keyword.value, ast.Constant)
                and isinstance(keyword.value.value, str)
            ):
                return keyword.value.value

    raise AssertionError("--qty add_argument help 문자열을 찾지 못했다")


def test_module_docstring_states_full_and_partial_cancel_contract() -> None:
    doc = _module_docstring(_parse_source())

    # 수량 생략 = 전량 취소 (ORD_QTY="0", QTY_ALL_ORD_YN="Y")
    assert "수량 생략(--qty 미지정)은 전량 취소" in doc
    assert 'ORD_QTY="0"' in doc
    assert 'QTY_ALL_ORD_YN="Y"' in doc

    # 수량 지정 = 부분 취소 (QTY_ALL_ORD_YN="N")
    assert "수량 지정은 해당 수량만 부분 취소" in doc
    assert 'QTY_ALL_ORD_YN="N"' in doc


def test_module_docstring_keeps_paper_level_note_without_live_claim() -> None:
    doc = _module_docstring(_parse_source())

    # Paper 환경 동작 확인 수준으로만 기술한다.
    assert "Paper 환경" in doc
    assert "동작 확인" in doc

    # task 9.1이 사용한 실제 문구: 실 환경 검증 주장으로 확장하지 않는다.
    assert "실 환경(live) 검증 주장으로 확장하지 않는다" in doc

    # 실 환경 검증 완료 같은 거짓 live 검증 주장이 없어야 한다.
    forbidden = [
        "실 환경 검증 완료",
        "실 환경(live) 검증 완료",
        "live 검증 완료",
        "실 환경에서 검증 완료",
    ]
    present = [phrase for phrase in forbidden if phrase in doc]
    assert not present, f"거짓 live 검증 주장 문구 발견: {present}"


def test_cancel_order_docstring_states_quantity_branching() -> None:
    doc = _function_docstring(_parse_source(), "cancel_order")

    assert "qty를 생략하면 전량 취소" in doc
    assert "qty를 지정하면 부분 취소" in doc


def test_cli_description_states_full_and_partial_cancel() -> None:
    description = _argparse_description(_parse_source())

    assert description == "KIS 주문 취소 (수량 생략 시 전량 취소, 수량 지정 시 부분 취소)"


def test_cli_qty_help_states_full_and_partial_cancel() -> None:
    qty_help = _qty_argument_help(_parse_source())

    assert qty_help == "취소 수량. 생략 시 전량 취소, 지정 시 부분 취소"
