"""Workspace 清理/配额验收的共享夹具（真实 PG + 真实文件系统）。

不 mock ORM、指标注册表或文件系统：测试直接写 ``task.workspace`` /
``task.workspace_lease`` 与 workspace 根目录，维护循环从真实元数据和目录得出结论。
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from muad_agent_core.sandbox import WorkspaceLayout
from muad_agent_worker.infrastructure.models.workspace import Workspace, WorkspaceLease
from muad_api import render_metrics
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

DAY = 24 * 60 * 60
MIB = 1024 * 1024

_SAMPLE_PATTERN = re.compile(
    r"^(?P<name>[A-Za-z_:][A-Za-z0-9_:]*)(?P<labels>\{.*\})?\s+(?P<value>[0-9eE+\-.]+)$"
)


@dataclass(frozen=True, slots=True)
class SeededWorkspace:
    id: uuid.UUID
    tenant_id: str
    user_id: uuid.UUID
    agent_id: uuid.UUID
    directory: Path
    data_dir: Path
    output_dir: Path


def make_layout(
    root: Path,
    *,
    quota_bytes: int = 500 * MIB,
    user_quota_bytes: int = 500 * MIB,
    file_limit: int = 5000,
    output_ttl_days: int = 30,
    data_ttl_days: int = 30,
) -> WorkspaceLayout:
    return WorkspaceLayout(
        root=root,
        quota_bytes=quota_bytes,
        user_quota_bytes=user_quota_bytes,
        file_limit=file_limit,
        data_ttl_seconds=data_ttl_days * DAY,
    )


def write_sparse(path: Path, size_bytes: int) -> None:
    """逻辑大小足够、几乎不占磁盘的配额样本（不截断/伪造配额判定）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.truncate(size_bytes)


def metric_value(name: str, labels: dict[str, str] | None = None, *, text: str | None = None) -> float:
    """从真实 ``/metrics`` 同源的 api-kit 注册表文本取一个样本。"""
    rendered = text if text is not None else render_metrics()
    expected = labels or {}
    for line in rendered.splitlines():
        if not line or line.startswith("#"):
            continue
        matched = _SAMPLE_PATTERN.match(line.strip())
        if matched is None or matched.group("name") != name:
            continue
        parsed: dict[str, str] = {}
        raw = matched.group("labels")
        if raw:
            for pair in raw[1:-1].split(","):
                key, _, value = pair.partition("=")
                parsed[key.strip()] = value.strip().strip('"')
        if all(parsed.get(key) == value for key, value in expected.items()):
            return float(matched.group("value"))
    return 0.0


async def seed_workspace(
    session: AsyncSession,
    layout: WorkspaceLayout,
    *,
    tenant_id: str,
    user_id: uuid.UUID | None = None,
    agent_id: uuid.UUID | None = None,
    last_access_time: datetime | None = None,
    state: str = "ACTIVE",
    lease_until: datetime | None = None,
    execution_id: str = "seed-execution",
    data_files: Iterable[str] = ("state.json",),
    output_files: Iterable[str] = ("result.json",),
) -> SeededWorkspace:
    user = user_id or uuid.uuid4()
    agent = agent_id or uuid.uuid4()
    access = last_access_time or datetime.now(UTC)
    workspace = Workspace(
        tenant_id=tenant_id,
        user_id=user,
        agent_id=agent,
        last_access_time=access,
        state=state,
    )
    session.add(workspace)
    await session.flush()
    directory = layout.workspace_dir(
        tenant_id=tenant_id, user_id=str(user), agent_id=str(agent)
    )
    data_dir = directory / "data"
    output_dir = directory / "outputs" / execution_id
    data_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    for name in data_files:
        (data_dir / name).write_text("data", encoding="utf-8")
    for name in output_files:
        (output_dir / name).write_text("output", encoding="utf-8")
    if lease_until is not None:
        session.add(
            WorkspaceLease(
                workspace_id=workspace.id,
                run_id="run-active",
                task_id="task-active",
                execution_id=execution_id,
                lease_until=lease_until,
            )
        )
    await session.commit()
    return SeededWorkspace(
        id=workspace.id,
        tenant_id=tenant_id,
        user_id=user,
        agent_id=agent,
        directory=directory,
        data_dir=data_dir,
        output_dir=output_dir,
    )


async def cleanup_tenant(session: AsyncSession, tenant_id: str) -> None:
    workspace_ids = select(Workspace.id).where(Workspace.tenant_id == tenant_id)
    await session.execute(
        delete(WorkspaceLease).where(WorkspaceLease.workspace_id.in_(workspace_ids))
    )
    await session.execute(delete(Workspace).where(Workspace.tenant_id == tenant_id))
    await session.commit()


async def fetch_workspace(session: AsyncSession, workspace_id: uuid.UUID) -> Workspace:
    """Always re-read from PG: maintenance updates rows outside this session's identity map."""
    row = await session.scalar(
        select(Workspace)
        .where(Workspace.id == workspace_id)
        .execution_options(populate_existing=True)
    )
    assert row is not None
    return row


def expired_at(days: int = 31) -> datetime:
    return datetime.now(UTC) - timedelta(days=days)


def active_until(minutes: int = 10) -> datetime:
    return datetime.now(UTC) + timedelta(minutes=minutes)
