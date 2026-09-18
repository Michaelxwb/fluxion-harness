#!/usr/bin/env python3
"""Frontend components must reach the backend only through src/api/*."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND_SRC = ROOT / "apps/console-platform/frontend/src"
API_DIR = FRONTEND_SRC / "api"

PATTERNS = (
    re.compile(r"^\s*import\s+axios\b"),
    re.compile(r"from\s+['\"]axios['\"]"),
    re.compile(r"\bfetch\s*\("),
)


def main() -> int:
    violations: list[str] = []
    for path in sorted(FRONTEND_SRC.rglob("*.ts")) + sorted(FRONTEND_SRC.rglob("*.tsx")):
        if API_DIR in path.parents:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if any(pattern.search(line) for pattern in PATTERNS):
                violations.append(f"{path.relative_to(ROOT)}:{lineno}: {line.strip()}")
    if violations:
        print("\n".join(violations))
        return 1
    print("frontend api usage check OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
