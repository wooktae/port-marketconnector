"""Static contracts for the MarketConnector deployment bundle."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INCLUDE_FILE = ROOT / ".devops" / "bundle" / "include.txt"


def load_entries() -> list[str]:
    return [
        line.strip()
        for line in INCLUDE_FILE.read_text(
            encoding="utf-8-sig"
        ).splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def test_bundle_include_manifest_exists() -> None:
    assert INCLUDE_FILE.is_file()


def test_bundle_files_exist() -> None:
    missing = [
        entry
        for entry in load_entries()
        if not (ROOT / entry).is_file()
    ]

    assert not missing, f"Missing bundle files: {missing}"


def test_bundle_excludes_sensitive_files() -> None:
    entries = {
        entry.replace("\\", "/").lower()
        for entry in load_entries()
    }

    forbidden = {
        "access_token.txt",
        ".env",
        ".env.local",
        "config.local.py",
    }

    assert entries.isdisjoint(forbidden)


def test_bundle_excludes_ci_and_document_paths() -> None:
    entries = {
        entry.replace("\\", "/")
        for entry in load_entries()
    }

    forbidden_prefixes = (
        ".git/",
        ".github/",
        "tests/",
        "docs/",
        ".devops/",
    )

    invalid = sorted(
        entry
        for entry in entries
        if entry.startswith(forbidden_prefixes)
    )

    assert not invalid, f"Invalid bundle entries: {invalid}"


def test_daily_wrapper_is_included() -> None:
    assert (
        "scripts/run_connector_balance_daily.sh"
        in load_entries()
    )