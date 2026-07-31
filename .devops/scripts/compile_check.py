from __future__ import annotations

import py_compile
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

EXCLUDED_PARTS = {
    ".git",
    ".venv",
    "__pycache__",
    "build",
    "dist",
}


def main() -> int:
    source_files = [
        path
        for path in ROOT.rglob("*.py")
        if not any(
            part in EXCLUDED_PARTS
            for part in path.parts
        )
    ]

    failures: list[str] = []

    for path in sorted(source_files):
        try:
            py_compile.compile(
                str(path),
                doraise=True,
            )
            print(f"COMPILE_OK={path.relative_to(ROOT)}")
        except py_compile.PyCompileError as exc:
            failures.append(
                f"{path.relative_to(ROOT)}: {exc.msg}"
            )

    if failures:
        for failure in failures:
            print(
                f"COMPILE_FAILED={failure}",
                file=sys.stderr,
            )

        return 1

    print(f"COMPILE_FILE_COUNT={len(source_files)}")
    print("PYTHON_COMPILE=SUCCESS")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())