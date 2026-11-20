"""任务产物：owner 读取/列表一致性与精确归属（真实 PostgreSQL）。"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from muad_agent_core.tools import ToolRegistry
from muad_agent_runtime.application.attachments.tools import (
    ATTACHMENT_NOT_FOUND,
    LIST_ATTACHMENTS_TOOL,
    READ_ATTACHMENT_TOOL,
    AttachmentToolError,
    AttachmentToolSet,
)
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import Artifact

from .conftest import TenantContext

pytestmark = pytest.mark.integration


@pytest.fixture
def artifact_root(tmp_path: Path) -> Path:
    root = tmp_path / "artifacts"
    root.mkdir()
    return root


async def _seed_execution(
    tenant: TenantContext, *, agent_id: uuid.UUID | None = None,
    actor_user_id: uuid.UUID | None = None, deleted: bool = False,
) -> uuid.UUID:
    execution_id = uuid.uuid4()
    async with get_session_factory()() as session:
        await session.execute(
            sa.text(
                "INSERT INTO task.task_execution"
                " (id, tenant_id, agent_id, actor_user_id, trigger_type, status, input_json,"
                "  execution_snapshot_json, snapshot_hash, idempotency_key, is_deleted)"
                " VALUES (:id, :tenant_id, :agent_id, :actor_user_id, 'SCHEDULED', 'COMPLETED',"
                "  '{}'::jsonb, '{}'::jsonb, :snapshot_hash, :idempotency_key, :is_deleted)"
            ),
            {
                "id": execution_id,
                "tenant_id": tenant.tenant_id,
                "agent_id": agent_id or tenant.agent_id,
                "actor_user_id": actor_user_id or tenant.platform_user_id,
                "snapshot_hash": "sha256:" + "e" * 64,
                "idempotency_key": f"exec-{execution_id}",
                "is_deleted": deleted,
            },
        )
        await session.commit()
    return execution_id


async def _seed_task_artifact(
    tenant: TenantContext,
    root: Path,
    execution_id: uuid.UUID,
    *,
    agent_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    filename: str = "report.txt",
    data: bytes = b"hello",
) -> uuid.UUID:
    artifact_id = uuid.uuid4()
    storage_key = f"tasks/{tenant.tenant_id}/{execution_id}/{artifact_id}/v1"
    (root / storage_key).parent.mkdir(parents=True, exist_ok=True)
    (root / storage_key).write_bytes(data)
    async with get_session_factory()() as session:
        session.add(
            Artifact(
                id=artifact_id,
                tenant_id=tenant.tenant_id,
                run_id=None,
                task_id=execution_id,
                conversation_id=None,
                agent_id=agent_id if agent_id is not None else tenant.agent_id,
                user_id=user_id if user_id is not None else tenant.platform_user_id,
                artifact_type="OUTPUT",
                storage_key=storage_key,
                media_type="text/plain",
                size=len(data),
                checksum="sha256:" + "a" * 64,
                metadata_json={"kind": "DOCUMENT", "filename": filename},
            )
        )
        await session.commit()
    return artifact_id


def _tool_set(
    tenant: TenantContext,
    root: Path,
    *,
    user_id: uuid.UUID | None = None,
    agent_id: uuid.UUID | None = None,
) -> AttachmentToolSet:
    return AttachmentToolSet(
        session_factory=get_session_factory,
        artifact_root=root,
        tenant_id=tenant.tenant_id,
        run_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        user_id=user_id or tenant.platform_user_id,
        agent_id=agent_id or tenant.agent_id,
    )


async def _call(tool_set: AttachmentToolSet, name: str, arguments: dict[str, Any]) -> str:
    registry = ToolRegistry()
    tool_set.register(registry)
    definition = registry.get(name)
    assert definition.handler is not None
    return await definition.handler(arguments, call_id="call-1")


async def test_task_artifact_is_listed_read_and_owner_scoped(
    tenant: TenantContext, artifact_root: Path
) -> None:
    execution_id = await _seed_execution(tenant)
    artifact_id = await _seed_task_artifact(tenant, artifact_root, execution_id)
    tool_set = _tool_set(tenant, artifact_root)

    listed = await _call(tool_set, LIST_ATTACHMENTS_TOOL, {"scope": "agent"})
    assert str(artifact_id) in listed
    assert "report.txt" in listed

    read_back = await _call(
        tool_set, READ_ATTACHMENT_TOOL, {"artifact_id": str(artifact_id)}
    )
    assert "hello" in read_back


async def test_task_artifact_rejects_other_user_and_agent(
    tenant: TenantContext, artifact_root: Path
) -> None:
    execution_id = await _seed_execution(tenant)
    artifact_id = await _seed_task_artifact(tenant, artifact_root, execution_id)

    other_user = _tool_set(tenant, artifact_root, user_id=uuid.uuid4())
    with pytest.raises(AttachmentToolError) as user_exc:
        await _call(other_user, READ_ATTACHMENT_TOOL, {"artifact_id": str(artifact_id)})
    assert user_exc.value.code == ATTACHMENT_NOT_FOUND
    assert str(artifact_id) not in await _call(
        other_user, LIST_ATTACHMENTS_TOOL, {"scope": "agent"}
    )

    other_agent = _tool_set(tenant, artifact_root, agent_id=uuid.uuid4())
    with pytest.raises(AttachmentToolError) as agent_exc:
        await _call(other_agent, READ_ATTACHMENT_TOOL, {"artifact_id": str(artifact_id)})
    assert agent_exc.value.code == ATTACHMENT_NOT_FOUND


async def test_deleted_task_or_missing_owner_is_rejected(
    tenant: TenantContext, artifact_root: Path
) -> None:
    deleted_execution = await _seed_execution(tenant, deleted=True)
    deleted_artifact = await _seed_task_artifact(
        tenant, artifact_root, deleted_execution, filename="deleted.txt"
    )
    tool_set = _tool_set(tenant, artifact_root)
    with pytest.raises(AttachmentToolError) as deleted_exc:
        await _call(tool_set, READ_ATTACHMENT_TOOL, {"artifact_id": str(deleted_artifact)})
    assert deleted_exc.value.code == ATTACHMENT_NOT_FOUND

    execution_id = await _seed_execution(tenant)
    orphan_artifact = await _seed_task_artifact(
        tenant, artifact_root, execution_id, agent_id=None, user_id=None, filename="orphan.txt"
    )
    # 显式写成 NULL，模拟历史空 owner 行；不能因为 user_id/agent_id 默认值被种子填充而误判。
    async with get_session_factory()() as session:
        await session.execute(
            sa.text(
                "UPDATE runtime.artifact SET agent_id = NULL, user_id = NULL WHERE id = :id"
            ),
            {"id": orphan_artifact},
        )
        await session.commit()
    with pytest.raises(AttachmentToolError) as orphan_exc:
        await _call(tool_set, READ_ATTACHMENT_TOOL, {"artifact_id": str(orphan_artifact)})
    assert orphan_exc.value.code == ATTACHMENT_NOT_FOUND
    assert str(orphan_artifact) not in await _call(
        tool_set, LIST_ATTACHMENTS_TOOL, {"scope": "agent"}
    )
