"""Build a deterministic Git SHA-based MarketConnector deployment bundle.

The builder reads only explicitly listed files. It does not import or execute
MarketConnector modules and does not access token, broker, database, or AWS.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[2]
INCLUDE_FILE = ROOT / ".devops" / "bundle" / "include.txt"
ARTIFACT_DIR = ROOT / ".devops" / "artifacts"

FORBIDDEN_NAMES = {
    "access_token.txt",
    ".env",
    ".env.local",
}

FORBIDDEN_PARTS = {
    ".git",
    ".github",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "tests",
}

FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)


def run_git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def load_include_paths() -> list[Path]:
    if not INCLUDE_FILE.is_file():
        raise RuntimeError(f"Include manifest missing: {INCLUDE_FILE}")

    relative_paths: list[Path] = []

    for raw_line in INCLUDE_FILE.read_text(
        encoding="utf-8-sig"
    ).splitlines():
        value = raw_line.strip()

        if not value or value.startswith("#"):
            continue

        relative_path = Path(value)

        if relative_path.is_absolute():
            raise RuntimeError(
                f"Absolute paths are not allowed: {value}"
            )

        if ".." in relative_path.parts:
            raise RuntimeError(
                f"Parent traversal is not allowed: {value}"
            )

        if relative_path.name.lower() in FORBIDDEN_NAMES:
            raise RuntimeError(
                f"Forbidden file in bundle manifest: {value}"
            )

        if any(
            part in FORBIDDEN_PARTS
            for part in relative_path.parts
        ):
            raise RuntimeError(
                f"Forbidden path in bundle manifest: {value}"
            )

        source_path = ROOT / relative_path

        if not source_path.is_file():
            raise RuntimeError(
                f"Bundle source file missing: {value}"
            )

        relative_paths.append(relative_path)

    if not relative_paths:
        raise RuntimeError("Bundle include manifest is empty.")

    if len(relative_paths) != len(set(relative_paths)):
        raise RuntimeError(
            "Duplicate entries exist in bundle include manifest."
        )

    return sorted(
        relative_paths,
        key=lambda path: path.as_posix(),
    )



def list_git_python_paths() -> list[Path]:
    """Return committed Python source paths available at HEAD."""
    output = run_git("ls-files", "*.py")

    if not output:
        return []

    return [
        Path(line)
        for line in output.splitlines()
        if line.strip()
    ]


def python_module_name(relative_path: Path) -> str | None:
    """Convert a repository-relative Python path to its importable module name."""
    if relative_path.suffix != ".py":
        return None

    parts = list(relative_path.with_suffix("").parts)

    if not parts:
        return None

    if parts[-1] == "__init__":
        parts = parts[:-1]

    if not parts:
        return None

    return ".".join(parts)


def build_local_module_index() -> dict[str, Path]:
    """Build a module-name -> committed source-path index for repository-local Python."""
    module_index: dict[str, Path] = {}

    for relative_path in list_git_python_paths():
        if any(
            part in FORBIDDEN_PARTS
            for part in relative_path.parts
        ):
            continue

        module_name = python_module_name(relative_path)

        if not module_name:
            continue

        previous = module_index.get(module_name)

        if previous and previous != relative_path:
            raise RuntimeError(
                "Ambiguous local Python module mapping: "
                f"{module_name} -> "
                f"{previous.as_posix()}, {relative_path.as_posix()}"
            )

        module_index[module_name] = relative_path

    return module_index


def resolve_absolute_local_import(
    module_name: str,
    module_index: dict[str, Path],
) -> set[Path]:
    """Resolve an absolute import to repository-local source files, if any."""
    dependencies: set[Path] = set()

    if module_name in module_index:
        dependencies.add(module_index[module_name])
        return dependencies

    parts = module_name.split(".")

    # Resolve the longest repository-local prefix.
    for end in range(len(parts) - 1, 0, -1):
        prefix = ".".join(parts[:end])

        if prefix in module_index:
            dependencies.add(module_index[prefix])
            break

    return dependencies


def package_name_for_source(
    source_path: Path,
) -> str:
    """Return the source module's containing package name."""
    module_name = python_module_name(source_path)

    if not module_name or "." not in module_name:
        return ""

    return module_name.rsplit(".", 1)[0]


def resolve_relative_import_module(
    source_path: Path,
    level: int,
    module: str | None,
) -> str | None:
    """Resolve a relative import target to an absolute module name."""
    package_name = package_name_for_source(source_path)
    package_parts = (
        package_name.split(".")
        if package_name
        else []
    )

    if level <= 0:
        return module

    ascend = level - 1

    if ascend > len(package_parts):
        return None

    base_parts = package_parts[: len(package_parts) - ascend]

    if module:
        base_parts.extend(module.split("."))

    if not base_parts:
        return None

    return ".".join(base_parts)


def collect_local_python_dependencies(
    source_path: Path,
    content: bytes,
    module_index: dict[str, Path],
) -> set[Path]:
    """Statically collect repository-local Python dependencies without importing."""
    try:
        tree = ast.parse(
            content.decode("utf-8-sig"),
            filename=source_path.as_posix(),
        )
    except (UnicodeDecodeError, SyntaxError) as exc:
        raise RuntimeError(
            "Unable to parse bundled Python source for dependency validation: "
            f"{source_path.as_posix()}: {exc}"
        ) from exc

    dependencies: set[Path] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                dependencies.update(
                    resolve_absolute_local_import(
                        alias.name,
                        module_index,
                    )
                )

        elif isinstance(node, ast.ImportFrom):
            target_module = resolve_relative_import_module(
                source_path,
                node.level,
                node.module,
            )

            if target_module:
                dependencies.update(
                    resolve_absolute_local_import(
                        target_module,
                        module_index,
                    )
                )

            # Support "from package import submodule" when submodule is a
            # repository-local Python module rather than a symbol.
            if target_module:
                for alias in node.names:
                    if alias.name == "*":
                        continue

                    candidate = f"{target_module}.{alias.name}"

                    if candidate in module_index:
                        dependencies.add(module_index[candidate])

    dependencies.discard(source_path)
    return dependencies


def validate_bundle_python_dependencies(
    include_paths: list[Path],
) -> None:
    """Fail closed when a bundled Python file depends on an omitted local module."""
    included_set = set(include_paths)
    module_index = build_local_module_index()
    missing_records: list[tuple[Path, Path]] = []

    python_include_paths = [
        path
        for path in include_paths
        if path.suffix == ".py"
    ]

    for source_path in python_include_paths:
        content = read_git_blob(source_path)
        dependencies = collect_local_python_dependencies(
            source_path,
            content,
            module_index,
        )

        for dependency_path in sorted(
            dependencies,
            key=lambda path: path.as_posix(),
        ):
            if dependency_path not in included_set:
                missing_records.append(
                    (source_path, dependency_path)
                )

    if missing_records:
        details = "; ".join(
            f"{source.as_posix()} -> {dependency.as_posix()}"
            for source, dependency in missing_records
        )

        raise RuntimeError(
            "Bundle local Python dependency missing from include manifest: "
            f"{details}"
        )

    print(
        "BUNDLE_PYTHON_DEPENDENCY_FILE_COUNT="
        f"{len(python_include_paths)}"
    )
    print("BUNDLE_PYTHON_DEPENDENCY_CONTRACT=SUCCESS")


def read_git_blob(relative_path: Path) -> bytes:
    result = subprocess.run(
        [
            "git",
            "show",
            f"HEAD:{relative_path.as_posix()}",
        ],
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
    )

    return result.stdout


def add_bytes(
    archive: zipfile.ZipFile,
    archive_name: str,
    content: bytes,
    mode: int,
) -> None:
    info = zipfile.ZipInfo(
        filename=archive_name,
        date_time=FIXED_ZIP_TIME,
    )
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = mode << 16

    archive.writestr(info, content)


def build_bundle(expected_sha: str | None) -> Path:
    branch = run_git("branch", "--show-current")
    source_sha = run_git("rev-parse", "HEAD")
    short_sha = source_sha[:12]
    worktree = run_git("status", "--porcelain")

    if branch and branch != "main":
        raise RuntimeError(
            f"Expected branch main or detached HEAD, actual={branch}"
        )

    if not branch and not expected_sha:
        raise RuntimeError(
            "Detached HEAD requires --expected-sha"
        )

    if expected_sha and source_sha != expected_sha:
        raise RuntimeError(
            "Expected SHA does not match current HEAD: "
            f"expected={expected_sha}, actual={source_sha}"
        )

    if worktree:
        raise RuntimeError(
            "Worktree must be clean before bundle build."
        )

    include_paths = load_include_paths()

    print("=== VALIDATE BUNDLE PYTHON DEPENDENCIES ===")
    validate_bundle_python_dependencies(include_paths)

    file_records: list[dict[str, object]] = []

    for relative_path in include_paths:
        content = read_git_blob(relative_path)

        file_records.append(
            {
                "path": relative_path.as_posix(),
                "sha256": sha256_bytes(content),
                "size": len(content),
            }
        )

    manifest = {
        "artifact_type": "marketconnector-versioned-zip",
        "source_branch": "main",
        "source_sha": source_sha,
        "source_short_sha": short_sha,
        "file_count": len(file_records),
        "files": file_records,
    }

    manifest_bytes = (
        json.dumps(
            manifest,
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)

    bundle_path = (
        ARTIFACT_DIR
        / f"port-marketconnector-{short_sha}.zip"
    )

    if bundle_path.exists():
        bundle_path.unlink()

    with zipfile.ZipFile(
        bundle_path,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for relative_path in include_paths:
            content = read_git_blob(relative_path)
            archive_name = PurePosixPath(
                relative_path.as_posix()
            ).as_posix()

            mode = (
                0o100755
                if relative_path.suffix == ".sh"
                else 0o100644
            )

            add_bytes(
                archive,
                archive_name,
                content,
                mode,
            )

        add_bytes(
            archive,
            "deployment-manifest.json",
            manifest_bytes,
            0o100644,
        )

    bundle_hash = sha256_bytes(bundle_path.read_bytes())

    print("SOURCE_BRANCH=main")
    print(
        "CHECKOUT_MODE="
        f"{'branch' if branch else 'detached-head'}"
    )
    print(f"SOURCE_SHA={source_sha}")
    print(f"IMAGE_TAG_EQUIVALENT={short_sha}")
    print(f"BUNDLE_PATH={bundle_path}")
    print(f"BUNDLE_FILE_COUNT={len(file_records) + 1}")
    print(f"BUNDLE_SHA256={bundle_hash}")
    print("VERSIONED_ZIP_BUNDLE_BUILD=SUCCESS")

    return bundle_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-sha")
    args = parser.parse_args()

    try:
        build_bundle(args.expected_sha)
    except (
        OSError,
        RuntimeError,
        subprocess.CalledProcessError,
        zipfile.BadZipFile,
    ) as exc:
        print(f"BUNDLE_BUILD_FAILED={exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())