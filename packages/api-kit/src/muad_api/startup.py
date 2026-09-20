from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


class StartupValidationError(RuntimeError):
    def __init__(self, failures: Sequence[str]) -> None:
        self.failures = list(failures)
        super().__init__("startup validation failed: " + "; ".join(self.failures))


def migration_head(migrations_dir: Path) -> str | None:
    graph: dict[str, str | None] = {}
    for path in sorted(migrations_dir.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        revision = re.search(r'^revision = "([^"]+)"', source, re.M)
        down = re.search(r'^down_revision = (?:None|"([^"]+)")', source, re.M)
        if revision:
            graph[revision.group(1)] = down.group(1) if down else None
    heads = sorted(set(graph) - {down for down in graph.values() if down})
    if len(heads) != 1:
        return None
    return heads[0]


async def _database_revision(engine: AsyncEngine) -> str | None:
    async with engine.connect() as connection:
        result = await connection.execute(text("SELECT version_num FROM alembic_version"))
        return result.scalar_one_or_none()


async def validate_startup(
    settings: Any,
    engine: AsyncEngine | None,
    artifact_store: Any,
    *,
    migrations_dir: str | Path | None = None,
) -> None:
    """启动初始化校验：配置、artifact 挂载、迁移到 head。

    不持有数据库的服务（如 im-gateway）传 `engine=None` 且 `migrations_dir=None`；
    若要校验迁移到 head，必须同时给出 engine 与 migrations_dir。
    """
    failures: list[str] = []
    if not getattr(settings, "database_url", None):
        failures.append("database_url is not configured")

    root = getattr(artifact_store, "root", None) or getattr(settings, "artifact_root", None)
    if not root or not Path(root).is_dir():
        failures.append(f"artifact storage is not mounted: {root}")

    if migrations_dir is not None:
        if engine is None:
            failures.append("engine is required to validate the schema revision")
        else:
            expected = migration_head(Path(migrations_dir))
            if expected is None:
                failures.append(f"cannot determine migration head under {migrations_dir}")
            else:
                try:
                    current = await _database_revision(engine)
                except Exception as exc:
                    failures.append(f"cannot read schema revision: {exc}")
                else:
                    if current != expected:
                        failures.append(f"database schema revision {current!r} is not at head {expected!r}")

    if failures:
        raise StartupValidationError(failures)
