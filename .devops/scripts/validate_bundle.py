"""Validate the generated MarketConnector deployment ZIP artifact.

Checks only the minimum release contract:
1) ZIP can be opened and passes basic integrity validation.
2) deployment-manifest.json exists and is valid JSON.
3) Manifest file set matches the actual ZIP file set.
4) Bundled Python files contain all repository-local Python dependencies.
5) Manifest source_sha matches the expected CodeBuild source SHA when provided.

This validator never imports or executes MarketConnector modules.
"""

from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_NAME = "deployment-manifest.json"

IGNORED_REPOSITORY_PARTS = {
    ".git",
    ".github",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "tests",
}


def run_git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def normalize_archive_name(name: str) -> str:
    path = PurePosixPath(name)

    if path.is_absolute() or ".." in path.parts:
        raise RuntimeError(f"Invalid archive path: {name}")

    return path.as_posix()


def load_manifest(
    archive: zipfile.ZipFile,
) -> dict[str, object]:
    try:
        manifest_bytes = archive.read(MANIFEST_NAME)
    except KeyError as exc:
        raise RuntimeError(
            f"Bundle manifest missing: {MANIFEST_NAME}"
        ) from exc

    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"Bundle manifest is not valid UTF-8 JSON: {exc}"
        ) from exc

    if not isinstance(manifest, dict):
        raise TypeError("Bundle manifest root must be a JSON object.")

    return manifest


def validate_source_sha(
    manifest: dict[str, object],
    expected_sha: str | None,
) -> None:
    if expected_sha is None:
        return

    source_sha = manifest.get("source_sha")

    if source_sha != expected_sha:
        raise RuntimeError(
            "Bundle manifest source_sha does not match expected SHA: "
            f"expected={expected_sha}, actual={source_sha}"
        )

    print("BUNDLE_SOURCE_SHA_CONTRACT=SUCCESS")


def validate_file_set(
    archive: zipfile.ZipFile,
    manifest: dict[str, object],
) -> list[str]:
    raw_files = manifest.get("files")

    if not isinstance(raw_files, list):
        raise TypeError(
            "Bundle manifest files must be a JSON array."
        )

    manifest_paths: list[str] = []

    for index, record in enumerate(raw_files):
        if not isinstance(record, dict):
            raise TypeError(
                f"Bundle manifest files[{index}] must be an object."
            )

        path_value = record.get("path")

        if not isinstance(path_value, str) or not path_value:
            raise RuntimeError(
                f"Bundle manifest files[{index}].path is invalid."
            )

        manifest_paths.append(
            normalize_archive_name(path_value)
        )

    if len(manifest_paths) != len(set(manifest_paths)):
        raise RuntimeError(
            "Duplicate paths exist in bundle manifest."
        )

    archive_paths = [
        normalize_archive_name(info.filename)
        for info in archive.infolist()
        if not info.is_dir()
    ]

    if len(archive_paths) != len(set(archive_paths)):
        raise RuntimeError(
            "Duplicate file entries exist in bundle ZIP."
        )

    expected = set(manifest_paths)
    expected.add(MANIFEST_NAME)
    actual = set(archive_paths)

    if actual != expected:
        missing = sorted(expected - actual)
        unexpected = sorted(actual - expected)

        raise RuntimeError(
            "Bundle ZIP file set does not match deployment manifest: "
            f"missing={missing}, unexpected={unexpected}"
        )

    print("BUNDLE_FILE_SET_CONTRACT=SUCCESS")
    return manifest_paths


def list_git_python_paths() -> list[Path]:
    output = run_git("ls-files", "*.py")

    if not output:
        return []

    return [
        Path(line)
        for line in output.splitlines()
        if line.strip()
    ]


def python_module_name(relative_path: Path) -> str | None:
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
    module_index: dict[str, Path] = {}

    for relative_path in list_git_python_paths():
        if any(
            part in IGNORED_REPOSITORY_PARTS
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
    dependencies: set[Path] = set()

    if module_name in module_index:
        dependencies.add(module_index[module_name])
        return dependencies

    parts = module_name.split(".")

    for end in range(len(parts) - 1, 0, -1):
        prefix = ".".join(parts[:end])

        if prefix in module_index:
            dependencies.add(module_index[prefix])
            break

    return dependencies


def package_name_for_source(
    source_path: Path,
) -> str:
    module_name = python_module_name(source_path)

    if not module_name or "." not in module_name:
        return ""

    return module_name.rsplit(".", 1)[0]


def resolve_relative_import_module(
    source_path: Path,
    level: int,
    module: str | None,
) -> str | None:
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
    try:
        tree = ast.parse(
            content.decode("utf-8-sig"),
            filename=source_path.as_posix(),
        )
    except (UnicodeDecodeError, SyntaxError) as exc:
        raise RuntimeError(
            "Unable to parse bundled Python source: "
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

            if not target_module:
                continue

            dependencies.update(
                resolve_absolute_local_import(
                    target_module,
                    module_index,
                )
            )

            for alias in node.names:
                if alias.name == "*":
                    continue

                candidate = f"{target_module}.{alias.name}"

                if candidate in module_index:
                    dependencies.add(module_index[candidate])

    dependencies.discard(source_path)
    return dependencies


def validate_python_dependencies(
    archive: zipfile.ZipFile,
    manifest_paths: list[str],
) -> None:
    bundled_paths = {
        Path(path)
        for path in manifest_paths
    }
    module_index = build_local_module_index()
    missing: list[tuple[Path, Path]] = []

    python_paths = sorted(
        (
            path
            for path in bundled_paths
            if path.suffix == ".py"
        ),
        key=lambda path: path.as_posix(),
    )

    for source_path in python_paths:
        content = archive.read(source_path.as_posix())
        dependencies = collect_local_python_dependencies(
            source_path,
            content,
            module_index,
        )

        for dependency_path in sorted(
            dependencies,
            key=lambda path: path.as_posix(),
        ):
            if dependency_path not in bundled_paths:
                missing.append(
                    (source_path, dependency_path)
                )

    if missing:
        details = "; ".join(
            f"{source.as_posix()} -> {dependency.as_posix()}"
            for source, dependency in missing
        )

        raise RuntimeError(
            "Bundle artifact local Python dependency missing: "
            f"{details}"
        )

    print("BUNDLE_ARTIFACT_DEPENDENCY_CONTRACT=SUCCESS")


def validate_bundle(
    bundle_path: Path,
    expected_sha: str | None,
) -> None:
    if not bundle_path.is_file():
        raise RuntimeError(
            f"Bundle file missing: {bundle_path}"
        )

    with zipfile.ZipFile(bundle_path, mode="r") as archive:
        bad_member = archive.testzip()

        if bad_member is not None:
            raise RuntimeError(
                f"Corrupt ZIP member detected: {bad_member}"
            )

        print("BUNDLE_ZIP_INTEGRITY=SUCCESS")

        manifest = load_manifest(archive)
        print("BUNDLE_MANIFEST=SUCCESS")

        validate_source_sha(
            manifest,
            expected_sha,
        )

        manifest_paths = validate_file_set(
            archive,
            manifest,
        )

        validate_python_dependencies(
            archive,
            manifest_paths,
        )

    print(f"BUNDLE_VALIDATE_PATH={bundle_path}")
    print("BUNDLE_ARTIFACT_CONTRACT=SUCCESS")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle_path", type=Path)
    parser.add_argument("--expected-sha")
    args = parser.parse_args()

    try:
        validate_bundle(
            args.bundle_path,
            args.expected_sha,
        )
    except (
        OSError,
        RuntimeError,
        TypeError,
        subprocess.CalledProcessError,
        zipfile.BadZipFile,
    ) as exc:
        print(
            f"BUNDLE_VALIDATION_FAILED={exc}",
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
