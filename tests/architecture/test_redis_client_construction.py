"""S-08: Redis 客户端构造点与 key 字面量的增量不变量。

新代码不得绕过 `muad_common.redis_client` 工厂；已有构造点登记在冻结清单里，
由 TASK-003/004/005 逐个迁移并从清单删除（新增项会立即失败）。
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FACTORY = "packages/common/src/muad_common/redis_client.py"
KEY_OWNER = "packages/contracts/src/muad_contracts/im.py"

CONSTRUCTION_PATTERN = re.compile(
    r"(?:Redis|redis\.asyncio\.Redis|redis)\.from_url\(|\bRedisCluster\("
)
KEY_LITERAL_PREFIXES = ("im.inbound:", "im.outbound:", "connector:bot:")

#: 尚未迁移的构造点：只允许缩减，不允许新增。
PENDING_CONSTRUCTION_SITES = frozenset(
    {
        "apps/agent-runtime/src/muad_agent_runtime/application/event_tail_hub.py",
        "apps/agent-runtime/src/muad_agent_runtime/infrastructure/cancel_hint.py",
        "apps/agent-runtime/src/muad_agent_runtime/infrastructure/notification_publisher.py",
        "apps/agent-worker/src/muad_agent_worker/infrastructure/cancel_hint.py",
        "apps/agent-worker/src/muad_agent_worker/infrastructure/wakeup_hint.py",
        "apps/console-platform/backend/src/muad_console_platform/api/deps.py",
        "apps/console-platform/backend/src/muad_console_platform/main.py",
        "apps/im-connector/src/muad_im_connector/main.py",
        "apps/im-gateway/src/muad_im_gateway/infrastructure/dedupe.py",
        "apps/im-gateway/src/muad_im_gateway/main.py",
        "packages/platform-sdk/src/muad_platform_sdk/skill_access.py",
    }
)


def _source_files() -> Iterator[tuple[str, Path]]:
    for base in ("apps", "packages"):
        for path in (ROOT / base).rglob("*.py"):
            relative = path.relative_to(ROOT).as_posix()
            if "/tests/" in f"/{relative}":
                continue
            yield relative, path


def _docstring_nodes(tree: ast.AST) -> set[int]:
    found: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            continue
        body = getattr(node, "body", [])
        if not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            found.add(id(first.value))
    return found


def test_only_factory_constructs_clients() -> None:
    found: set[str] = set()
    for relative, path in _source_files():
        if relative == FACTORY:
            continue
        if CONSTRUCTION_PATTERN.search(path.read_text(encoding="utf-8")):
            found.add(relative)

    unexpected = found - PENDING_CONSTRUCTION_SITES
    assert not unexpected, f"新增直连构造点，必须改用工厂: {sorted(unexpected)}"
    stale = PENDING_CONSTRUCTION_SITES - found
    assert not stale, f"已迁移的构造点请从冻结清单删除: {sorted(stale)}"


def test_key_prefixes_live_in_contracts() -> None:
    leaks: dict[str, set[str]] = {}
    for relative, path in _source_files():
        if relative == KEY_OWNER:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = _docstring_nodes(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            if id(node) in docstrings:
                continue
            for prefix in KEY_LITERAL_PREFIXES:
                if prefix in node.value:
                    leaks.setdefault(relative, set()).add(prefix)

    assert not leaks, f"key 前缀必须由契约提供: {sorted(leaks)}"
