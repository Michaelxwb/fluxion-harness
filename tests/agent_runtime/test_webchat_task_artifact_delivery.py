"""WebChat/MSSWWEB：无会话任务产物的发布与下载授权。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from muad_api import AppError
from muad_api.error_codes import ErrorCode

from muad_agent_runtime.application.attachments.publication import (
    PublicationError,
    publish_artifact_to_conversation,
)
from muad_agent_runtime.application.attachments.task_artifacts import load_task_owner
from muad_agent_runtime.application.webchat_artifacts import resolve_webchat_artifact
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import (
    Artifact,
    Conversation,
    RunRecord,
)

from agent_runtime.conftest import TenantContext

pytestmark = pytest.mark.integration


def _checksum() -> str:
    return "sha256:" + "a" * 64


async def _seed_conversation(tenant: TenantContext) -> tuple[uuid.UUID, uuid.UUID]:
    conversation = Conversation(
        id=uuid.uuid4(),
        tenant_id=tenant.tenant_id,
        user_id=tenant.platform_user_id,
        agent_id=tenant.agent_id,
        status="ACTIVE",
        last_seq=0,
        channel_account_id=uuid.uuid4(),
    )
    run = RunRecord(
        id=uuid.uuid4(),
        tenant_id=tenant.tenant_id,
        conversation_id=conversation.id,
        user_id=tenant.platform_user_id,
        agent_id=tenant.agent_id,
        status="RUNNING",
        input_text="x",
        trace_id=uuid.uuid4().hex,
        cancel_requested=False,
    )
    async with get_session_factory()() as session:
        session.add(conversation)
        session.add(run)
        await session.commit()
    return conversation.id, run.id


async def _seed_task_execution(tenant: TenantContext) -> uuid.UUID:
    execution_id = uuid.uuid4()
    async with get_session_factory()() as session:
        await session.execute(
            sa.text(
                "INSERT INTO task.task_execution"
                " (id, tenant_id, agent_id, actor_user_id, trigger_type, status, input_json,"
                "  execution_snapshot_json, snapshot_hash, idempotency_key)"
                " VALUES (:id, :tenant_id, :agent_id, :actor_user_id, 'SCHEDULED', 'COMPLETED',"
                "  '{}'::jsonb, '{}'::jsonb, :snapshot_hash, :idempotency_key)"
            ),
            {
                "id": execution_id,
                "tenant_id": tenant.tenant_id,
                "agent_id": tenant.agent_id,
                "actor_user_id": tenant.platform_user_id,
                "snapshot_hash": "sha256:" + "e" * 64,
                "idempotency_key": f"exec-{execution_id}",
            },
        )
        await session.commit()
    return execution_id


async def _seed_task_artifact(
    tenant: TenantContext, root: Path, execution_id: uuid.UUID, *, artifact_type: str = "OUTPUT"
) -> tuple[uuid.UUID, str]:
    artifact_id = uuid.uuid4()
    checksum = _checksum()
    storage_key = f"tasks/{tenant.tenant_id}/{execution_id}/{artifact_id}/v1"
    (root / storage_key).parent.mkdir(parents=True, exist_ok=True)
    (root / storage_key).write_bytes(b"report")
    async with get_session_factory()() as session:
        session.add(
            Artifact(
                id=artifact_id,
                tenant_id=tenant.tenant_id,
                run_id=None,
                task_id=execution_id,
                conversation_id=None,
                agent_id=tenant.agent_id,
                user_id=tenant.platform_user_id,
                artifact_type=artifact_type,
                storage_key=storage_key,
                media_type="text/plain",
                size=6,
                checksum=checksum,
                metadata_json={"kind": "DOCUMENT", "filename": "report.txt"},
            )
        )
        await session.commit()
    return artifact_id, checksum


async def test_task_output_publishes_and_resolves_for_web_surface(
    tenant: TenantContext, tmp_path: Path
) -> None:
    conversation_id, run_id = await _seed_conversation(tenant)
    execution_id = await _seed_task_execution(tenant)
    artifact_id, checksum = await _seed_task_artifact(tenant, tmp_path, execution_id)

    seq = await publish_artifact_to_conversation(
        get_session_factory,
        tenant_id=tenant.tenant_id,
        conversation_id=conversation_id,
        run_id=run_id,
        artifact_id=artifact_id,
    )
    assert seq > 0

    async with get_session_factory()() as session:
        download = await resolve_webchat_artifact(
            session,
            tenant_id=tenant.tenant_id,
            user_id=tenant.platform_user_id,
            artifact_id=artifact_id,
            conversation_id=conversation_id,
            checksum=checksum,
        )
    assert download.reference.artifact_id == artifact_id
    assert download.reference.filename == "report.txt"


async def test_task_output_without_publication_is_not_downloadable(
    tenant: TenantContext, tmp_path: Path
) -> None:
    conversation_id, _run_id = await _seed_conversation(tenant)
    execution_id = await _seed_task_execution(tenant)
    artifact_id, checksum = await _seed_task_artifact(tenant, tmp_path, execution_id)

    async with get_session_factory()() as session:
        with pytest.raises(AppError) as exc:
            await resolve_webchat_artifact(
                session,
                tenant_id=tenant.tenant_id,
                user_id=tenant.platform_user_id,
                artifact_id=artifact_id,
                conversation_id=conversation_id,
                checksum=checksum,
            )
    assert exc.value.code == ErrorCode.COMMON_NOT_FOUND


async def test_tool_result_requires_explicit_internal_publication(
    tenant: TenantContext, tmp_path: Path
) -> None:
    conversation_id, run_id = await _seed_conversation(tenant)
    execution_id = await _seed_task_execution(tenant)
    artifact_id, _checksum_value = await _seed_task_artifact(
        tenant, tmp_path, execution_id, artifact_type="TOOL_RESULT"
    )

    with pytest.raises(PublicationError) as refused:
        await publish_artifact_to_conversation(
            get_session_factory,
            tenant_id=tenant.tenant_id,
            conversation_id=conversation_id,
            run_id=run_id,
            artifact_id=artifact_id,
        )
    assert refused.value.code == "ATTACHMENT_TYPE_UNSUPPORTED"

    seq = await publish_artifact_to_conversation(
        get_session_factory,
        tenant_id=tenant.tenant_id,
        conversation_id=conversation_id,
        run_id=run_id,
        artifact_id=artifact_id,
        allow_internal=True,
    )
    assert seq > 0


async def test_task_owner_helper_checks_live_execution(
    tenant: TenantContext, tmp_path: Path
) -> None:
    execution_id = await _seed_task_execution(tenant)
    async with get_session_factory()() as session:
        owner = await load_task_owner(
            session, tenant_id=tenant.tenant_id, execution_id=execution_id
        )
    assert owner is not None
    assert owner.actor_user_id == tenant.platform_user_id
    assert owner.agent_id == tenant.agent_id
