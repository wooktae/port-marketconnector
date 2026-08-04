"""검증 안전 경계 conftest (Requirement 6).

이 conftest는 모든 테스트에서 실제 외부 side-effect 진입점을 가드해,
검증 중 실제 KIS API 호출, 실제 주문/취소, 운영 DB 접근, token 발급이
발생하지 않도록 보장한다.

가드하는 실제 진입점(모든 시나리오에서 실제 호출 `call_count == 0`):

- `requests.post` / `requests.get`
  `connector_order_common.py`와 `token_manager.py`는 module-level
  `import requests` 후 호출 시점에 `requests.post(...)` 형태로 속성을 조회한다.
  `requests` 모듈의 `post`/`get`을 가드로 대체하면, 어떤 호출 지점이든
  실제 네트워크 호출 이전에 즉시 실패한다.

- `psycopg.connect`
  운영 DB 연결의 실제 저수준 진입점이다. `connector_db.get_conn()`은
  `psycopg.connect(**get_db_config())`를 호출한다. `psycopg.connect`를
  가드로 대체하면, 실제 DB 연결 이전에 즉시 실패한다.

- token 함수(`get_access_token`, `check_and_refresh_token`,
  `issue_new_token`, `force_issue_new_token`)
  `token_manager` 원본과, 이를 `from token_manager import ...`로 가져다 쓰는
  `connector_order_common` 바인딩을 함께 가드한다. 실제 token 발급과
  token 파일 접근이 발생하기 전에 즉시 실패한다.

per-test monkeypatch 우선(precedence):

- 이 autouse 가드는 테스트와 동일한 function-scoped `monkeypatch`를 사용한다.
  fixture setup은 테스트 본문보다 먼저 실행되므로, 테스트가 자체적으로
  `monkeypatch.setattr(common.requests, "post", ...)`처럼 상위 모듈 속성을
  대체하면 그 값이 가드를 덮어써(intercept) 테스트의 fake가 사용된다.
- DB는 저수준 `psycopg.connect`에서 가드하므로, 테스트가 상위 수준
  `execute.get_conn`/`common.get_conn`을 자체 fake로 대체하면 실제
  `get_conn`이 실행되지 않아 `psycopg.connect` 가드에 도달하지 않는다.
  아무것도 대체하지 않은 채 실제 호출이 경계에 도달한 경우에만 가드가
  발동한다.

proactive mock 사전 확인(Requirement 6.4):

- 검증 시작(=fixture setup, 실제 호출 이전) 시점에 위 실제 진입점들이
  가드로 대체된다. 어떤 대상이 mock으로 대체되지 않은 채 실제 호출이
  시도되면, 실제 side effect가 발생하기 전에 가드가 즉시 `AssertionError`로
  실패시키며 어떤 진입점(requests.post / requests.get / DB / token)이
  가드에 도달했는지 오류 메시지로 보고한다.

mock broker 함수 가드 아님(Requirement 6 범위 구분):

- 이 conftest는 실제 외부 side-effect 진입점만 0으로 가드한다.
- mock broker/`_request_api`/`_submit_order` 등의 호출 횟수는 강제로 0으로
  만들지 않으며, 시나리오별 기대값(차단·skip은 0회, 정상 Claim 성공은
  정확히 1회)은 각 테스트가 자체적으로 assert한다.
"""

from __future__ import annotations

import ast
import os
import sys
from pathlib import Path
from typing import Any

import pytest

# repo 루트를 sys.path에 추가해 `connector_*`·`token_manager` 등 루트 모듈
# import를 보장한다(테스트 실행 위치와 무관하게 동작하도록).
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _install_import_only_environment() -> None:
    """config.py가 import 시 요구하는 환경변수를 더미 값으로만 채운다.

    실제 KIS 키·계좌 값을 읽거나 기록하지 않고, broker/DB/token side effect도
    유발하지 않는다. 이미 설정된 키는 덮어쓰지 않는다. 각 테스트 모듈이 자체적으로
    동일 설치를 수행하지만, conftest가 import되며 유발되는 config import도 안전하게
    만들기 위해 여기서도 설치한다.
    """
    config_path = _REPO_ROOT / "config.py"
    if not config_path.exists():
        return

    source = config_path.read_text(encoding="utf-8-sig")
    tree = ast.parse(source)

    keys: set[str] = set()

    for node in ast.walk(tree):
        # os.environ["KEY"]
        if isinstance(node, ast.Subscript):
            value = node.value
            if (
                isinstance(value, ast.Attribute)
                and isinstance(value.value, ast.Name)
                and value.value.id == "os"
                and value.attr == "environ"
                and isinstance(node.slice, ast.Constant)
                and isinstance(node.slice.value, str)
            ):
                keys.add(node.slice.value)

        if isinstance(node, ast.Call):
            func = node.func

            # os.getenv("KEY")
            if (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "os"
                and func.attr == "getenv"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                keys.add(node.args[0].value)

            # os.environ.get("KEY")
            if (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Attribute)
                and isinstance(func.value.value, ast.Name)
                and func.value.value.id == "os"
                and func.value.attr == "environ"
                and func.attr == "get"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                keys.add(node.args[0].value)

    for key in keys:
        if key in os.environ:
            continue

        if "URL" in key:
            value = "https://example.invalid"
        elif any(token in key for token in ("ACNT", "ACCOUNT", "CANO")):
            value = "00000000"
        elif any(token in key for token in ("PRDT", "PRODUCT")):
            value = "01"
        else:
            value = "TEST_ONLY_DUMMY"

        os.environ[key] = value


_install_import_only_environment()


# ---------------------------------------------------------------------------
# 실제 외부 side-effect 진입점 가드(fail-fast)
#
# 각 가드는 실제 호출 이전에 즉시 AssertionError로 실패시키며, 어떤 진입점이
# 가드에 도달했는지 명시한다(Requirement 6.4의 "mock 미적용 대상 보고").
# ---------------------------------------------------------------------------
def _guard_requests_post(*args: Any, **kwargs: Any):
    raise AssertionError(
        "검증 안전 경계 위반: 실제 requests.post 호출이 시도되었습니다. "
        "un-mocked entrypoint: requests.post "
        "(Requirement 6.1, 6.3, 6.4 - 이 진입점은 mock으로 대체되어야 합니다)"
    )


def _guard_requests_get(*args: Any, **kwargs: Any):
    raise AssertionError(
        "검증 안전 경계 위반: 실제 requests.get 호출이 시도되었습니다. "
        "un-mocked entrypoint: requests.get "
        "(Requirement 6.3, 6.4 - 이 진입점은 mock으로 대체되어야 합니다)"
    )


def _guard_psycopg_connect(*args: Any, **kwargs: Any):
    raise AssertionError(
        "검증 안전 경계 위반: 실제 운영 DB 연결(psycopg.connect)이 시도되었습니다. "
        "un-mocked entrypoint: DB (psycopg.connect / get_conn) "
        "(Requirement 6.3, 6.4 - DB 함수는 mock으로 대체되어야 합니다)"
    )


def _guard_token_call(*args: Any, **kwargs: Any):
    raise AssertionError(
        "검증 안전 경계 위반: 실제 token 함수 호출이 시도되었습니다. "
        "un-mocked entrypoint: token function "
        "(Requirement 6.3, 6.4 - token 함수는 mock으로 대체되어야 합니다)"
    )


_TOKEN_FUNCTION_NAMES = (
    "get_access_token",
    "check_and_refresh_token",
    "issue_new_token",
    "force_issue_new_token",
)


@pytest.fixture(autouse=True)
def _guard_real_external_side_effects(monkeypatch):
    """모든 테스트에서 실제 외부 side-effect 진입점을 가드한다(Requirement 6).

    가드는 테스트와 동일한 function-scoped `monkeypatch`로 설치되며, fixture
    setup이 테스트 본문보다 먼저 실행되므로 테스트가 자체적으로 상위 수준
    속성(예: `common.requests.post`, `execute.get_conn`)을 mock으로 대체하면
    그 값이 가드를 덮어쓴다(per-test monkeypatch 우선).
    """
    # HTTP 경계: requests.post / requests.get.
    import requests

    monkeypatch.setattr(requests, "post", _guard_requests_post)
    monkeypatch.setattr(requests, "get", _guard_requests_get)

    # DB 경계: 실제 저수준 psycopg.connect.
    # 상위 수준 get_conn을 테스트가 fake로 대체하면 실제 get_conn이 실행되지
    # 않아 이 가드에 도달하지 않는다. 아무것도 대체하지 않은 채 실제 연결이
    # 시도된 경우에만 발동한다.
    import psycopg

    monkeypatch.setattr(psycopg, "connect", _guard_psycopg_connect)

    # Token 경계: token_manager 원본과 connector_order_common 바인딩 모두 가드.
    # import는 방어적으로 처리해, connector 모듈을 import하지 않는 계약/스모크
    # 테스트에서도 conftest가 불필요하게 실패하지 않도록 한다. token 발급은
    # 결국 requests.post를 사용하므로, import가 불가하더라도 HTTP 가드가
    # 실제 token 발급을 차단한다.
    try:
        import token_manager
    except ImportError:
        token_manager = None

    if token_manager is not None:
        for name in _TOKEN_FUNCTION_NAMES:
            if hasattr(token_manager, name):
                monkeypatch.setattr(token_manager, name, _guard_token_call)

    try:
        import connector_order_common as _common
    except ImportError:
        _common = None

    if _common is not None:
        for name in ("get_access_token", "check_and_refresh_token"):
            if hasattr(_common, name):
                monkeypatch.setattr(_common, name, _guard_token_call)

    yield
