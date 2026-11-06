"""[RULE-api-002] 草稿提交幂等：同 key 同指纹重放，不同指纹/actor/tenant 冲突或隔离。

真实边界：ASGI 内部 API → 真实 PostgreSQL `control.skill_import_idempotency` /
`control.skill_artifact`；advisory lock 串行化并发同键请求。
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from httpx import AsyncClient
from muad_console_platform.infrastructure.models.control import (
    SkillArtifact,
    SkillImportIdempotency,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.acceptance.user_skill_sandbox.draft_support import (
    create_draft,
    new_tenant,
    skill_md,
    submit_draft,
    write_file,
)

pytestmark = pytest.mark.integration


async def _draft(client: AsyncClient, tenant: str, owner: uuid.UUID, key: str) -> tuple[str, int]:
    draft = await create_draft(client, tenant_id=tenant, owner_user_id=owner, key=key)
    written = await write_file(
        client,
        tenant_id=tenant,
        owner_user_id=owner,
        draft_id=draft["id"],
        path="SKILL.md",
        content=skill_md(platforms=["mssp"]),
        expected_revision=1,
    )
    assert written.status_code == 200, written.text
    return draft["id"], written.json()["data"]["revision"]


async def test_same_key_same_fingerprint_replays_first_result(
    internal_client: AsyncClient,
    draft_tenant,
    session: AsyncSession,
) -> None:
    """[RULE-api-002] 同 key 同指纹重放首次结果，不新增 artifact。"""
    tenant = new_tenant("acc-idem-replay")
    owner = uuid.uuid4()
    draft_id, revision = await _draft(internal_client, tenant, owner, "idem-replay")

    first = await submit_draft(
        internal_client,
        tenant_id=tenant,
        owner_user_id=owner,
        draft_id=draft_id,
        expected_revision=revision,
        version="1.0.0",
        idempotency_key="idem-replay-key",
    )
    assert first.status_code == 200, first.text
    replay = await submit_draft(
        internal_client,
        tenant_id=tenant,
        owner_user_id=owner,
        draft_id=draft_id,
        expected_revision=revision,
        version="1.0.0",
        idempotency_key="idem-replay-key",
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["data"] == first.json()["data"]

    artifact_id = uuid.UUID(first.json()["data"]["artifact_id"])
    count = await session.scalar(
        select(func.count())
        .select_from(SkillArtifact)
        .where(SkillArtifact.id == artifact_id)
    )
    assert count == 1
    record = await session.scalar(
        select(SkillImportIdempotency).where(
            SkillImportIdempotency.tenant_id == tenant,
            SkillImportIdempotency.idempotency_key == "idem-replay-key",
            SkillImportIdempotency.endpoint == "draft_submit",
        )
    )
    assert record is not None
    assert record.request_fingerprint.startswith("sha256:")


async def test_same_key_different_fingerprint_mismatches(
    internal_client: AsyncClient,
    draft_tenant,
) -> None:
    """[RULE-api-002] 同 key 不同请求指纹返回 IDEMPOTENCY_MISMATCH。"""
    tenant = new_tenant("acc-idem-mismatch")
    owner = uuid.uuid4()
    draft_id, revision = await _draft(internal_client, tenant, owner, "idem-mismatch")

    first = await submit_draft(
        internal_client,
        tenant_id=tenant,
        owner_user_id=owner,
        draft_id=draft_id,
        expected_revision=revision,
        version="1.0.0",
        idempotency_key="idem-mismatch-key",
    )
    assert first.status_code == 200, first.text

    mismatched = await submit_draft(
        internal_client,
        tenant_id=tenant,
        owner_user_id=owner,
        draft_id=draft_id,
        expected_revision=revision,
        version="1.0.1",  # 指纹变化
        idempotency_key="idem-mismatch-key",
    )
    assert mismatched.status_code == 409
    assert mismatched.json()["code"] == "IDEMPOTENCY_MISMATCH"


async def test_same_key_is_scoped_by_tenant_and_actor(
    internal_client: AsyncClient,
    draft_tenant,
    session: AsyncSession,
) -> None:
    """[RULE-api-002] 幂等键含 tenant/actor 判别：跨租户、跨 owner 不互相重放。"""
    tenant_a = new_tenant("acc-idem-tenant-a")
    tenant_b = new_tenant("acc-idem-tenant-b")
    owner_a, owner_b = uuid.uuid4(), uuid.uuid4()
    draft_a, revision_a = await _draft(internal_client, tenant_a, owner_a, "idem-scope")
    draft_b, revision_b = await _draft(internal_client, tenant_b, owner_b, "idem-scope")

    first = await submit_draft(
        internal_client,
        tenant_id=tenant_a,
        owner_user_id=owner_a,
        draft_id=draft_a,
        expected_revision=revision_a,
        version="1.0.0",
        idempotency_key="shared-key",
    )
    assert first.status_code == 200, first.text

    other_tenant = await submit_draft(
        internal_client,
        tenant_id=tenant_b,
        owner_user_id=owner_b,
        draft_id=draft_b,
        expected_revision=revision_b,
        version="1.0.0",
        idempotency_key="shared-key",
    )
    assert other_tenant.status_code == 200, other_tenant.text
    assert other_tenant.json()["data"]["artifact_id"] != first.json()["data"]["artifact_id"]

    # 同一租户内换 actor 但复用 key + 相同参数：指纹含 owner，必须冲突。
    owner_c = uuid.uuid4()
    draft_c, revision_c = await _draft(internal_client, tenant_a, owner_c, "idem-scope")
    same_tenant_other_actor = await submit_draft(
        internal_client,
        tenant_id=tenant_a,
        owner_user_id=owner_c,
        draft_id=draft_c,
        expected_revision=revision_c,
        version="1.0.0",
        idempotency_key="shared-key",
    )
    assert same_tenant_other_actor.status_code == 409
    assert same_tenant_other_actor.json()["code"] == "IDEMPOTENCY_MISMATCH"

    rows = await session.scalar(
        select(func.count())
        .select_from(SkillImportIdempotency)
        .where(
            SkillImportIdempotency.idempotency_key == "shared-key",
            SkillImportIdempotency.endpoint == "draft_submit",
        )
    )
    assert rows == 2


async def test_concurrent_same_key_creates_exactly_one_artifact(
    internal_client: AsyncClient,
    draft_tenant,
    session: AsyncSession,
) -> None:
    """[RULE-api-002] 并发同键：advisory lock 串行化，落败者读首次结果。"""
    tenant = new_tenant("acc-idem-concurrent")
    owner = uuid.uuid4()
    draft_id, revision = await _draft(internal_client, tenant, owner, "idem-concurrent")

    async def submit():
        return await submit_draft(
            internal_client,
            tenant_id=tenant,
            owner_user_id=owner,
            draft_id=draft_id,
            expected_revision=revision,
            version="3.0.0",
            idempotency_key="concurrent-key",
        )

    responses = await asyncio.gather(submit(), submit())
    assert [response.status_code for response in responses] == [200, 200]
    artifact_ids = {response.json()["data"]["artifact_id"] for response in responses}
    assert len(artifact_ids) == 1

    artifact_id = uuid.UUID(artifact_ids.pop())
    count = await session.scalar(
        select(func.count())
        .select_from(SkillArtifact)
        .where(SkillArtifact.id == artifact_id)
    )
    assert count == 1
