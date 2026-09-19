#!/usr/bin/env python3
"""E-01 静态残留检查：禁止 SecretRef/SecretProvider 代码路径复活。"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCAN_ROOTS = ("apps", "packages", "migrations", "scripts", "config", "tests")
EXCLUDED_PARTS = {".git", ".venv", "node_modules", "__pycache__", ".code-flow", ".data"}
# 例外（均需保留旧引用以完成或断言迁移本身）：
# - 本脚本与 E-01 用例：模式定义/断言
# - 0002 初始迁移：历史列定义（已发布迁移不可改写）
# - 0005 contract 迁移：需要旧列名执行 drop
# - backfill CLI：在 expand→contract 窗口读取遗留 secret_ref
# - 迁移/策略/消费侧验收用例：降级播种或断言“无残留”
EXCLUDED_FILES = {
    "scripts/check_secret_ref_residue.py",
    "tests/test_secret_ref_residue.py",
    "tests/test_secret_policy_docs.py",
    "tests/acceptance/test_secret_migration.py",
    "tests/acceptance/test_secret_consumers.py",
    # Schema 验收仅断言旧 MCP 密钥引用列已移除，必须保留原列名。
    "tests/acceptance/test_mcp_schema_constraints.py",
    "apps/console-platform/backend/src/muad_console_platform/cli.py",
    "migrations/versions/0002_initial_schema.py",
    "migrations/versions/0005_secret_plaintext_contract.py",
}
PATTERNS = (
    re.compile(r"secret://"),
    re.compile(r"SecretProvider"),
    re.compile(r"secret_ref"),
    re.compile(r"auth_secret_ref"),
    re.compile(r"EnvSecretProvider"),
)


def scan() -> list[str]:
    hits: list[str] = []
    for root_name in SCAN_ROOTS:
        root = ROOT / root_name
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            if any(part in EXCLUDED_PARTS for part in path.parts):
                continue
            relative = path.relative_to(ROOT).as_posix()
            if relative in EXCLUDED_FILES:
                continue
            if path.suffix not in {".py", ".ts", ".tsx", ".yml", ".yaml", ".json"}:
                continue
            try:
                source = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for lineno, line in enumerate(source.splitlines(), 1):
                if any(pattern.search(line) for pattern in PATTERNS):
                    hits.append(f"{relative}:{lineno}: {line.strip()[:120]}")
    return hits


def main() -> int:
    hits = scan()
    if hits:
        print("\n".join(hits))
        return 1
    print("secret ref residue check OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
