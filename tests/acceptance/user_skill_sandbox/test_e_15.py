"""[E-15] Artifact checksum 不匹配：拒绝下载与执行，且告警可见。

真实边界：真实 Artifact Store 文件被篡改 → Console 下载 API / Worker Skill 执行入口的
checksum 校验 → 真实 PostgreSQL TaskExecution；不 mock 文件系统或校验器。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from muad_agent_worker.infrastructure.models.task import TaskExecution
from muad_agent_worker.worker.executor import SkillTaskExecutor, TaskExecutionError
from muad_artifact_store import NfsArtifactStore, SkillArtifactCache
from muad_console_platform.infrastructure.models.control import SkillArtifact
from muad_console_platform.infrastructure.skill_artifact_store import artifact_path
from sqlalchemy.ext.asyncio import AsyncSession

from .approval_support import ApprovalEnv, submit_user_skill

pytestmark = pytest.mark.integration


def _download_url(skill_id: str, artifact_id: str) -> str:
    return f"/api/v1/skills/{skill_id}/artifacts/{artifact_id}/download"


def _snapshot(
    *,
    tenant_id: str,
    owner_id: uuid.UUID,
    agent_id: uuid.UUID,
    skill_id: str,
    artifact_id: str,
    checksum: str,
    storage_key: str,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "agent": {"key": "e15-agent"},
        "model": {"key": "e15-model"},
        "skills": [
            {
                "skill_id": skill_id,
                "artifact_id": artifact_id,
                "key": "e15-integrity",
                "version": "1.0.0",
                "checksum": checksum,
                "storage_key": storage_key,
                "execution_mode": "ASYNC",
                "source": "USER",
                "workspace": {
                    "tenant_id": tenant_id,
                    "user_id": str(owner_id),
                    "agent_id": str(agent_id),
                },
            }
        ],
        "mcp": [],
        "prompt_template_version": "v1",
        "budget": {"max_tokens": 1024},
    }


async def test_e15_tampered_artifact_is_rejected_by_download_and_execution(
    internal_client: AsyncClient,
    admin_client: AsyncClient,
    approval_env: ApprovalEnv,
    session: AsyncSession,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    submitted = await submit_user_skill(internal_client, approval_env, key="e15-integrity")
    artifact_uuid = uuid.UUID(submitted.artifact_id)
    session.expire_all()
    artifact = await session.get(SkillArtifact, artifact_uuid)
    assert artifact is not None
    path = artifact_path(artifact.storage_key)
    original = path.read_bytes()
    tampered = b"PK\x03\x04 tampered-skill-package"
    path.write_bytes(tampered)

    with caplog.at_level(
        logging.WARNING, logger="muad_console_platform.application.skill_artifact_download"
    ):
        download = await admin_client.get(
            _download_url(submitted.skill_id, submitted.artifact_id)
        )

    assert download.status_code == 422, download.text
    body = download.json()
    assert body["code"] == "SKILL_ARTIFACT_INTEGRITY_ERROR"
    assert download.headers["content-type"].startswith("application/json")
    assert b"tampered-skill-package" not in download.content
    assert artifact.storage_key not in json.dumps(body, ensure_ascii=False)
    assert any(
        "skill_artifact_integrity_error" in record.getMessage() for record in caplog.records
    )

    task = TaskExecution(
        tenant_id=approval_env.tenant,
        agent_id=approval_env.agent_id,
        actor_user_id=approval_env.owner_id,
        intent_key="e15-integrity",
        skill_id=uuid.UUID(submitted.skill_id),
        skill_artifact_id=artifact_uuid,
        trigger_type="SCHEDULED",
        execution_mode="ASYNC",
        status="QUEUED",
        input_json={},
        execution_snapshot_schema_version=1,
        execution_snapshot_json=_snapshot(
            tenant_id=approval_env.tenant,
            owner_id=approval_env.owner_id,
            agent_id=approval_env.agent_id,
            skill_id=submitted.skill_id,
            artifact_id=submitted.artifact_id,
            checksum=artifact.checksum,
            storage_key=artifact.storage_key,
        ),
        snapshot_hash="sha256:" + hashlib.sha256(submitted.artifact_id.encode()).hexdigest(),
        idempotency_key=f"e15-{uuid.uuid4()}",
        delivery_key=f"task:{uuid.uuid4()}:final",
    )
    session.add(task)
    await session.commit()
    task_id = task.id

    cache_root = tmp_path / "skill-cache"
    cache = SkillArtifactCache(
        NfsArtifactStore(Path(os.environ["ARTIFACT_ROOT"])), cache_root
    )
    caplog.clear()
    with caplog.at_level(
        logging.WARNING, logger="muad_agent_worker.worker.executor"
    ):
        with pytest.raises(TaskExecutionError) as excinfo:
            await SkillTaskExecutor(cache).execute(task)

    assert excinfo.value.code == "SKILL_ARTIFACT_INTEGRITY_ERROR"
    assert not list(cache_root.rglob("READY"))
    assert any(
        "skill_artifact_integrity_error" in record.getMessage() for record in caplog.records
    )

    session.expire_all()
    stored = await session.get(TaskExecution, task_id)
    assert stored is not None
    assert stored.status == "QUEUED"
    assert stored.result_json is None
    assert stored.error_code is None

    path.write_bytes(original)
    await session.execute(
        sa.delete(TaskExecution).where(TaskExecution.tenant_id == approval_env.tenant)
    )
    await session.commit()
