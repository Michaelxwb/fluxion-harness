"""[RULE-skill-001] 下载必须读 Artifact Store 的不可变文件，DB 只存相对 storage_key。

这是 rule 级验收（S-07 是功能级）：确认 artifact 行保存的是相对键、响应不含该键，
且下载字节与 Artifact Store 中的文件一致——不绕过去直接读数据库外的副本。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import PurePosixPath

import pytest
from httpx import AsyncClient
from muad_console_platform.infrastructure.models.control import SkillArtifact
from muad_console_platform.infrastructure.skill_artifact_store import artifact_path
from sqlalchemy.ext.asyncio import AsyncSession

from .approval_support import ApprovalEnv, submit_user_skill

pytestmark = pytest.mark.integration


async def test_artifact_download_reads_immutable_store_with_relative_key(
    internal_client: AsyncClient,
    admin_client: AsyncClient,
    approval_env: ApprovalEnv,
    session: AsyncSession,
) -> None:
    submitted = await submit_user_skill(internal_client, approval_env, key="rule-skill-download")
    session.expire_all()
    artifact = await session.get(SkillArtifact, uuid.UUID(submitted.artifact_id))
    assert artifact is not None

    key = PurePosixPath(artifact.storage_key)
    assert not key.is_absolute(), artifact.storage_key
    assert ".." not in key.parts, artifact.storage_key

    stored = artifact_path(artifact.storage_key).read_bytes()
    assert hashlib.sha256(stored).hexdigest() == artifact.checksum.removeprefix("sha256:")

    response = await admin_client.get(
        f"/api/v1/skills/{submitted.skill_id}/artifacts/{submitted.artifact_id}/download"
    )
    assert response.status_code == 200, response.text
    assert response.content == stored
    assert response.headers["x-checksum"] == artifact.checksum
    assert artifact.storage_key not in json.dumps(dict(response.headers), ensure_ascii=False)
