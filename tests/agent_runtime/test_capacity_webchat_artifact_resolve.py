"""B-255 Owner 侧：附件下载归属解析（design §3.4 API-10、§3.6；TASK-055）。

真实边界：真实 Runtime 应用（内部服务身份门控 + 真实路由）、真实 `resolve_webchat_artifact`、
隔离租户的真实 PostgreSQL（`conversation`/`artifact` 都是真实行，含软删 tombstone）；不 mock
存储、不触模型、不建 Console 行（入口/Agent 是逻辑 UUID，核属在业务边界）。

关键断言（B-255 的 Owner 半场）：

1. 归属解析：`artifact_id` → 本主体**名下会话**的 (entry, agent) + 渠道中立引用（存储键/元信息
   与真实行逐项一致），只读不写。
2. 不泄露存在性：他主体会话、跨租户、软删产物、软删会话、内部留档（`TRANSCRIPT`）、无会话归属、
   入口不可归属、未知 id —— 全部 `COMMON_NOT_FOUND` 且响应不可分辨。
3. 受信身份门控：无内部服务身份一律 403（与 `/internal/admin/runs` 同一口径）。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import Artifact, Conversation
from muad_common import SharedSettings

from agent_runtime.conftest import CLEANUP_ORDER, TenantContext

pytestmark = pytest.mark.integration

#: 本套件的入口（WEBCHAT 稳定入口账号；真实核属维度之一，不建 Console 行）。
ENTRY = uuid.uuid4()
BASE_TIME = datetime(2026, 10, 18, 8, 0, 0, tzinfo=UTC)
#: 清理顺序：制品先于 run_record/conversation（同 Owner 物理 FK），再走套件既有顺序。
PURGE_ORDER = ("artifact", *CLEANUP_ORDER)
#: 响应里必须齐备的引用字段（渠道中立引用；存储键只在受信面出现）。
REFERENCE_FIELDS = {
    "storage_key",
    "kind",
    "media_type",
    "size",
    "filename",
    "checksum",
    "source_channel",
    "artifact_id",
}


@dataclass(frozen=True, slots=True)
class _Seeded:
    conversation_id: uuid.UUID
    artifact_id: uuid.UUID
    storage_key: str
    media_type: str
    size: int
    filename: str | None
    checksum: str


async def _purge_tenant(tenant_id: str) -> None:
    async with get_session_factory()() as session:
        for table in PURGE_ORDER:
            await session.execute(
                sa.text(f"DELETE FROM runtime.{table} WHERE tenant_id = :tenant_id"),
                {"tenant_id": tenant_id},
            )
        await session.commit()


@pytest.fixture
async def resolve_tenant(tenant: TenantContext) -> AsyncIterator[TenantContext]:
    try:
        yield tenant
    finally:
        await _purge_tenant(tenant.tenant_id)


async def _seed_conversation(
    tenant: TenantContext,
    *,
    user_id: uuid.UUID | None = None,
    agent_id: uuid.UUID | None = None,
    entry_id: uuid.UUID | None = ENTRY,
    is_deleted: bool = False,
) -> uuid.UUID:
    conversation = Conversation(
        id=uuid.uuid4(),
        tenant_id=tenant.tenant_id,
        user_id=user_id or tenant.platform_user_id,
        agent_id=agent_id or tenant.agent_id,
        title=None,
        status="ACTIVE",
        last_seq=0,
        channel_account_id=entry_id,
        last_message_at=BASE_TIME,
        create_time=BASE_TIME,
        update_time=BASE_TIME,
        is_deleted=is_deleted,
    )
    async with get_session_factory()() as session:
        session.add(conversation)
        await session.commit()
    return conversation.id


async def _seed_artifact(
    tenant: TenantContext,
    *,
    conversation_id: uuid.UUID | None,
    media_type: str = "application/pdf",
    filename: str | None = "报告.pdf",
    artifact_type: str = "INBOUND_DOCUMENT",
    size: int = 5,
    is_deleted: bool = False,
    storage_key: str | None = None,
) -> uuid.UUID:
    artifact = Artifact(
        id=uuid.uuid4(),
        tenant_id=tenant.tenant_id,
        run_id=uuid.uuid4(),
        task_id=None,
        conversation_id=conversation_id,
        artifact_type=artifact_type,
        storage_key=storage_key or f"runs/{uuid.uuid4()}/attachments/{uuid.uuid4()}",
        media_type=media_type,
        size=size,
        checksum="sha256:" + "b" * 64,
        metadata_json={"kind": "DOCUMENT", "filename": filename},
        is_deleted=is_deleted,
        create_time=BASE_TIME,
        update_time=BASE_TIME,
    )
    async with get_session_factory()() as session:
        session.add(artifact)
        await session.commit()
    return artifact.id


async def _seed_owned_artifact(
    tenant: TenantContext, *, user_id: uuid.UUID | None = None
) -> _Seeded:
    conversation_id = await _seed_conversation(tenant, user_id=user_id)
    storage_key = f"runs/{uuid.uuid4()}/attachments/{uuid.uuid4()}"
    artifact_id = await _seed_artifact(
        tenant, conversation_id=conversation_id, storage_key=storage_key, size=5
    )
    return _Seeded(
        conversation_id=conversation_id,
        artifact_id=artifact_id,
        storage_key=storage_key,
        media_type="application/pdf",
        size=5,
        filename="报告.pdf",
        checksum="sha256:" + "b" * 64,
    )


def _headers(tenant: TenantContext) -> dict[str, str]:
    return {
        "X-Tenant-Id": tenant.tenant_id,
        "X-Internal-Service": SharedSettings().internal_service_token or "",
    }


def _path(artifact_id: uuid.UUID) -> str:
    return f"/internal/webchat/artifacts/{artifact_id}"


async def _resolve(
    client: AsyncClient, tenant: TenantContext, artifact_id: uuid.UUID, *, user_id: uuid.UUID | None = None
) -> Any:
    return await client.get(
        _path(artifact_id),
        params={"user_id": str(user_id or tenant.platform_user_id)},
        headers=_headers(tenant),
    )


async def _artifact_row(tenant_id: str, artifact_id: uuid.UUID) -> Artifact | None:
    async with get_session_factory()() as session:
        return await session.get(Artifact, artifact_id)


# --------------------------------------------------------------------------------------
# 归属解析：本主体名下会话 → (entry, agent) + 渠道中立引用
# --------------------------------------------------------------------------------------


async def test_b255_resolve_returns_owned_scope_and_reference(
    resolve_tenant: TenantContext, client: AsyncClient
) -> None:
    """真实行事实逐项投影：入口/Agent 从会话行导出，引用带存储键与权威元信息，只读不写。"""
    seeded = await _seed_owned_artifact(resolve_tenant)

    response = await _resolve(client, resolve_tenant, seeded.artifact_id)

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert set(data) == {"scope", "reference"}
    assert data["scope"] == {
        "conversation_id": str(seeded.conversation_id),
        "entry_id": str(ENTRY),
        "agent_id": str(resolve_tenant.agent_id),
    }
    reference = data["reference"]
    assert set(reference) == REFERENCE_FIELDS
    assert reference["storage_key"] == seeded.storage_key, "存储键只在受信面返回"
    assert reference["media_type"] == seeded.media_type
    assert reference["size"] == seeded.size
    assert reference["filename"] == seeded.filename
    assert reference["checksum"] == seeded.checksum
    assert reference["artifact_id"] == str(seeded.artifact_id)
    assert reference["kind"] == "DOCUMENT"

    # 只读：行事实与软删标记都不因解析而改变，也不新增任何制品行。
    row = await _artifact_row(resolve_tenant.tenant_id, seeded.artifact_id)
    assert row is not None
    assert row.is_deleted is False and row.storage_key == seeded.storage_key
    async with get_session_factory()() as session:
        count = await session.scalar(
            sa.select(sa.func.count())
            .select_from(Artifact)
            .where(Artifact.tenant_id == resolve_tenant.tenant_id)
        )
    assert count == 1


# --------------------------------------------------------------------------------------
# 不泄露存在性：他主体/跨租户/软删/内部留档/不可归属一律 404
# --------------------------------------------------------------------------------------


async def test_b255_resolve_refuses_other_user_cross_tenant_and_deleted(
    resolve_tenant: TenantContext, tenant: TenantContext, client: AsyncClient
) -> None:
    """八种失败同为 404 且不可分辨；他主体/跨租户不可能借 artifact_id 取件。"""
    other_user = uuid.uuid4()
    foreign_tenant = TenantContext(
        tenant_id=f"test-{uuid.uuid4()}", agent_id=uuid.uuid4(), platform_user_id=uuid.uuid4()
    )
    other_user_artifact = await _seed_owned_artifact(resolve_tenant, user_id=other_user)
    foreign = await _seed_owned_artifact(foreign_tenant)
    deleted_artifact = await _seed_artifact(
        resolve_tenant, conversation_id=other_user_artifact.conversation_id, is_deleted=True
    )
    transcript = await _seed_artifact(
        resolve_tenant,
        conversation_id=other_user_artifact.conversation_id,
        artifact_type="TRANSCRIPT",
    )
    deleted_conversation = await _seed_conversation(resolve_tenant, is_deleted=True)
    orphan_conversation_artifact = await _seed_artifact(
        resolve_tenant, conversation_id=deleted_conversation
    )
    no_conversation = await _seed_artifact(resolve_tenant, conversation_id=None)
    unattributable = await _seed_artifact(
        resolve_tenant,
        conversation_id=await _seed_conversation(resolve_tenant, entry_id=None),
    )

    try:
        responses = [
            # ① 同一租户、产物属于**别人**：会话主体核属失败。
            await _resolve(client, resolve_tenant, other_user_artifact.artifact_id),
            # ② 产物在别的租户：租户谓词先落空。
            await _resolve(client, resolve_tenant, foreign.artifact_id),
            # ③ 软删产物（tombstone）视同不存在。
            await _resolve(client, resolve_tenant, deleted_artifact),
            # ④ 内部留档（TRANSCRIPT）不是公开附件。
            await _resolve(client, resolve_tenant, transcript),
            # ⑤ 会话已软删：不可归属。
            await _resolve(client, resolve_tenant, orphan_conversation_artifact),
            # ⑥ 产物没有会话归属（任务侧产物）：网页下载面无此语义。
            await _resolve(client, resolve_tenant, no_conversation),
            # ⑦ 会话入口不可归属（无法落到任何公开入口）。
            await _resolve(client, resolve_tenant, unattributable),
            # ⑧ 未知 id。
            await _resolve(client, resolve_tenant, uuid.uuid4()),
        ]
    finally:
        await _purge_tenant(foreign_tenant.tenant_id)

    assert [response.status_code for response in responses] == [404] * 8
    signatures = {
        (response.status_code, response.json()["code"], response.json()["msg"], response.json()["data"])
        for response in responses
    }
    assert len(signatures) == 1, "八种失败不可分辨（只有逐请求 trace 元数据不同）"
    assert signatures.pop()[1] == "COMMON_NOT_FOUND"
    assert all(seeded_key not in "".join(r.text for r in responses) for seeded_key in (
        other_user_artifact.storage_key,
        foreign.storage_key,
    )), "被拒的解析不泄露任何存储键"

    # 反向对照：同一主体自己的产物仍然解析得出来（拒绝不是因为实现整体失效）。
    own = await _seed_owned_artifact(resolve_tenant)
    assert (await _resolve(client, resolve_tenant, own.artifact_id)).status_code == 200


async def test_b255_resolve_requires_internal_service_identity(
    resolve_tenant: TenantContext, client: AsyncClient
) -> None:
    """受信身份门控：无内部服务身份（或缺租户）一律 403，不触达归属解析。"""
    seeded = await _seed_owned_artifact(resolve_tenant)

    anonymous = await client.get(
        _path(seeded.artifact_id),
        params={"user_id": str(resolve_tenant.platform_user_id)},
        headers={"X-Tenant-Id": resolve_tenant.tenant_id},
    )
    wrong_token = await client.get(
        _path(seeded.artifact_id),
        params={"user_id": str(resolve_tenant.platform_user_id)},
        headers={
            "X-Tenant-Id": resolve_tenant.tenant_id,
            "X-Internal-Service": "not-the-service-token",
        },
    )

    assert anonymous.status_code == 403 and anonymous.json()["code"] == "FORBIDDEN"
    assert wrong_token.status_code == 403 and wrong_token.json()["code"] == "FORBIDDEN"
