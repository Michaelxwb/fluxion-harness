"""[S-07] 管理员下载 PENDING/APPROVED artifact：正确 zip/checksum、审计、越权拒绝。

真实边界：Console API → 真实 Artifact Store（临时 NFS 根）→ 下载响应 → 真实 PostgreSQL 审计；
管理员走真实登录 + CSRF，不 mock ORM/HTTP/文件系统。
"""

from __future__ import annotations

import hashlib
import json
import uuid

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from muad_console_platform.infrastructure.models.control import ConfigAuditLog, SkillArtifact
from muad_console_platform.infrastructure.skill_artifact_store import artifact_path
from sqlalchemy.ext.asyncio import AsyncSession

from .approval_support import (
    ApprovalEnv,
    approve,
    authenticated_builder,
    seed_builder_account,
    submit_user_skill,
)

pytestmark = pytest.mark.integration


def _download_url(skill_id: str, artifact_id: str) -> str:
    return f"/api/v1/skills/{skill_id}/artifacts/{artifact_id}/download"


async def test_s07_admin_downloads_pending_and_approved_with_checksum_and_audit(
    internal_client: AsyncClient,
    admin_client: AsyncClient,
    approval_env: ApprovalEnv,
    session: AsyncSession,
) -> None:
    submitted = await submit_user_skill(internal_client, approval_env, key="s07-download")
    url = _download_url(submitted.skill_id, submitted.artifact_id)
    artifact_uuid = uuid.UUID(submitted.artifact_id)

    pending = await admin_client.get(url)
    assert pending.status_code == 200, pending.text
    session.expire_all()
    artifact = await session.get(SkillArtifact, artifact_uuid)
    assert artifact is not None
    assert artifact.review_status == "PENDING"
    assert pending.headers["x-checksum"] == artifact.checksum
    assert hashlib.sha256(pending.content).hexdigest() == artifact.checksum.removeprefix("sha256:")
    assert pending.headers["content-type"].startswith("application/zip")
    assert "attachment" in pending.headers["content-disposition"]
    stored_bytes = artifact_path(artifact.storage_key).read_bytes()
    assert pending.content == stored_bytes

    # 响应不暴露存储键：头部、响应体与 Content-Disposition 都不得出现 storage_key。
    response_headers = json.dumps(dict(pending.headers), ensure_ascii=False)
    assert artifact.storage_key not in response_headers
    assert artifact.storage_key.encode() not in pending.content

    approved = await approve(admin_client, submitted.skill_id, submitted.artifact_id)
    assert approved.status_code == 200, approved.text
    session.expire_all()
    artifact = await session.get(SkillArtifact, artifact_uuid)
    assert artifact is not None and artifact.review_status == "APPROVED"

    approved_download = await admin_client.get(url)
    assert approved_download.status_code == 200, approved_download.text
    assert approved_download.headers["x-checksum"] == artifact.checksum
    assert approved_download.content == pending.content

    session.expire_all()
    audits = list(
        (
            await session.scalars(
                sa.select(ConfigAuditLog)
                .where(
                    ConfigAuditLog.tenant_id == approval_env.tenant,
                    ConfigAuditLog.action == "SKILL_ARTIFACT_DOWNLOAD",
                )
                .order_by(ConfigAuditLog.create_time)
            )
        ).all()
    )
    assert len(audits) == 2
    assert {row.actor_user_id for row in audits} == {approval_env.admin_id}
    assert all(row.resource_id == uuid.UUID(submitted.skill_id) for row in audits)
    assert {row.after_json["review_status"] for row in audits} == {"PENDING", "APPROVED"}
    assert all(row.after_json["artifact_id"] == submitted.artifact_id for row in audits)
    assert all("storage_key" not in row.after_json for row in audits)


async def test_s07_builder_cannot_download_and_artifact_must_belong_to_skill(
    internal_client: AsyncClient,
    admin_client: AsyncClient,
    approval_env: ApprovalEnv,
    session: AsyncSession,
) -> None:
    submitted = await submit_user_skill(internal_client, approval_env, key="s07-owner")
    other = await submit_user_skill(internal_client, approval_env, key="s07-other")

    builder_username = await seed_builder_account(session, approval_env)
    async with authenticated_builder(approval_env, builder_username) as builder_client:
        denied = await builder_client.get(
            _download_url(submitted.skill_id, submitted.artifact_id)
        )
        assert denied.status_code == 403, denied.text
        assert denied.json()["code"] == "FORBIDDEN"

    cross_skill = await admin_client.get(
        _download_url(other.skill_id, submitted.artifact_id)
    )
    assert cross_skill.status_code == 404, cross_skill.text
    assert cross_skill.json()["code"] == "COMMON_NOT_FOUND"
