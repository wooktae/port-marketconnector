"""Validation safety-boundary conftest (Requirement 6).

This conftest guards the real external side-effect entrypoints in every test so that
no real KIS API calls, real orders/cancellations, operating DB access or token issuance
occur during validation.

Guarded real entrypoints (real call `call_count == 0` in every scenario):

- `requests.post` / `requests.get`
  `connector_order_common.py` and `token_manager.py` do a module-level
  `import requests` and then look up the attribute as `requests.post(...)` at call time.
  Replacing the `requests` module's `post`/`get` with a guard makes any call site
  fail immediately before a real network call.

- `psycopg.connect`
  The real low-level entrypoint for operating DB connections. `connector_db.get_conn()`
  calls `psycopg.connect(**get_db_config())`. Replacing `psycopg.connect` with a guard
  makes it fail immediately before a real DB connection.

- token functions (`get_access_token`, `check_and_refresh_token`,
  `issue_new_token`, `force_issue_new_token`)
  Guards both the `token_manager` originals and the `connector_order_common` binding that
  imports them via `from token_manager import ...`. It fails immediately before real token
  issuance and token file access occur.

per-test monkeypatch precedence:

- This autouse guard uses the same function-scoped `monkeypatch` as the test.
  Since fixture setup runs before the test body, if a test itself replaces an upper-module
  attribute like `monkeypatch.setattr(common.requests, "post", ...)`, that value overwrites
  (intercepts) the guard and the test's fake is used.
- Since the DB is guarded at the low-level `psycopg.connect`, if a test replaces the upper-level
  `execute.get_conn`/`common.get_conn` with its own fake, the real `get_conn` does not run and
  the `psycopg.connect` guard is not reached. The guard fires only when a real call reaches the
  boundary with nothing replaced.

proactive mock pre-check (Requirement 6.4):

- At validation start (= fixture setup, before any real call), the real entrypoints above are
  replaced with guards. If any target is not replaced with a mock and a real call is attempted,
  the guard fails immediately with an `AssertionError` before a real side effect occurs, and
  reports in the error message which entrypoint (requests.post / requests.get / DB / token)
  reached the guard.

not a mock-broker-function guard (Requirement 6 scope distinction):

- This conftest guards only the real external side-effect entrypoints to 0.
- It does not force the call counts of the mock broker/`_request_api`/`_submit_order` etc. to 0;
  each test asserts its own per-scenario expected values (0 for block/skip, exactly 1 for a
  successful normal Claim).
"""

from __future__ import annotations

import ast
import os
import sys
from pathlib import Path
from typing import Any

import pytest

# Add the repo root to sys.path to ensure imports of root modules such as `connector_*`·`token_manager`
# (so it works regardless of where the tests are run from).
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _install_import_only_environment() -> None:
    """Fills only with dummy values the environment variables that config.py requires at import time.

    It does not read or record real KIS key/account values, and it triggers no broker/DB/token side
    effects. It does not overwrite already-set keys. Each test module performs the same installation
    itself, but this is also installed here to make the config import triggered by importing conftest safe.
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
# Real external side-effect entrypoint guards (fail-fast)
#
# Each guard fails immediately with an AssertionError before the real call, and states
# which entrypoint reached the guard (Requirement 6.4's "report un-mocked targets").
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
    """Guards the real external side-effect entrypoints in every test (Requirement 6).

    The guards are installed with the same function-scoped `monkeypatch` as the test, and since
    fixture setup runs before the test body, if a test itself replaces an upper-level attribute
    (e.g. `common.requests.post`, `execute.get_conn`) with a mock, that value overwrites the guard
    (per-test monkeypatch precedence).
    """
    # HTTP boundary: requests.post / requests.get.
    import requests

    monkeypatch.setattr(requests, "post", _guard_requests_post)
    monkeypatch.setattr(requests, "get", _guard_requests_get)

    # DB boundary: the real low-level psycopg.connect.
    # If a test replaces the upper-level get_conn with a fake, the real get_conn does not run
    # and this guard is not reached. It fires only when a real connection is attempted with
    # nothing replaced.
    import psycopg

    monkeypatch.setattr(psycopg, "connect", _guard_psycopg_connect)

    # Token boundary: guard both the token_manager originals and the connector_order_common binding.
    # Handle the import defensively so that conftest does not fail unnecessarily even in contract/smoke
    # tests that do not import the connector module. Since token issuance ultimately uses requests.post,
    # the HTTP guard blocks real token issuance even if the import is not possible.
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
