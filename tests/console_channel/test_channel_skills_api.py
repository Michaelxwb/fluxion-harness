"""B-104: Console Effective Skills 内部端点（真实 handler → 生产授权服务 → PostgreSQL）。

不得 Mock 的真实边界：真实 FastAPI handler、真实授权公式（AgentAccessGrant +
AgentSkillBinding + SkillUserGrant + enabled/is_deleted），真实 PostgreSQL 查询。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from httpx import AsyncClient
from muad_console_platform.infrastructure.db import get_engine, get_session_factory
from muad_console_platform.infrastructure.models.control import (
    AgentSkillBinding,
    Skill,
    SkillArtifact,
    SkillUserGrant,
)
from sqlalchemy import delete, event, select
from sqlalchemy.ext.asyncio import AsyncSession

from console_channel.conftest import ChannelContext

SKILLS_URL = "/internal/channel/skills"
HIDDEN_CANARY = "hidden-skill-canary-do-not-leak"
ALWAYS_CANARY = "artifact-frontmatter-canary-do-not-leak"


async def _seed_skill(
    session: AsyncSession,
    tenant_id: str,
    key: str,
    *,
    user_scope: str,
    enabled: bool = True,
    description: str = "Seeded skill.",
    artifact: bool = True,
) -> tuple[Skill, SkillArtifact | None]:
    skill = Skill(
        tenant_id=tenant_id,
        key=key,
        name=f"Skill {key}",
        description=description,
        platform_label=f"Label {key}",
        user_scope=user_scope,
        enabled=enabled,
    )
    session.add(skill)
    await session.flush()
    created: SkillArtifact | None = None
    if artifact:
        created = SkillArtifact(
            skill_id=skill.id,
            version="1.0.0",
            checksum="sha256:" + "a" * 64,
            storage_key=f"skills/{skill.id}/{uuid.uuid4()}/skill.zip",
            frontmatter_json={"name": f"Skill {key}", "notes": ALWAYS_CANARY},
            manifest_json={"files": [], "file_count": 0, "total_size": 0},
            instructions=ALWAYS_CANARY,
            execution_mode="SYNC",
            package_size=128,
            validation_status="READY",
            created_by=uuid.uuid4(),
        )
        session.add(created)
        await session.flush()
        skill.current_artifact_id = created.id
        await session.flush()
    return skill, created


@pytest.fixture()
async def skills_env(channel: ChannelContext) -> AsyncIterator[dict[str, str]]:
    """真实 PG 数据：可见 2 条（ALL / SELECTED+Grant），不可见 4 条（未授权/禁用/撤销/解绑）。"""
    seeded_ids: list[uuid.UUID] = []
    keys: dict[str, str] = {}
    async with get_session_factory()() as session:
        visible_all = await _seed_skill(
            session, channel.tenant_id, f"skill-all-{uuid.uuid4().hex[:8]}", user_scope="ALL"
        )
        visible_granted = await _seed_skill(
            session,
            channel.tenant_id,
            f"skill-granted-{uuid.uuid4().hex[:8]}",
            user_scope="SELECTED",
        )
        hidden_ungranted = await _seed_skill(
            session,
            channel.tenant_id,
            f"skill-ungranted-{uuid.uuid4().hex[:8]}",
            user_scope="SELECTED",
            description=HIDDEN_CANARY,
        )
        hidden_disabled = await _seed_skill(
            session,
            channel.tenant_id,
            f"skill-disabled-{uuid.uuid4().hex[:8]}",
            user_scope="ALL",
            enabled=False,
            description=HIDDEN_CANARY,
        )
        hidden_revoked = await _seed_skill(
            session,
            channel.tenant_id,
            f"skill-revoked-{uuid.uuid4().hex[:8]}",
            user_scope="SELECTED",
            description=HIDDEN_CANARY,
        )
        hidden_unbound = await _seed_skill(
            session,
            channel.tenant_id,
            f"skill-unbound-{uuid.uuid4().hex[:8]}",
            user_scope="ALL",
            description=HIDDEN_CANARY,
        )
        seeded = [
            visible_all,
            visible_granted,
            hidden_ungranted,
            hidden_disabled,
            hidden_revoked,
            hidden_unbound,
        ]
        seeded_ids = [skill.id for skill, _ in seeded]
        session.add_all(
            [
                AgentSkillBinding(agent_id=channel.agent_id, skill_id=visible_all[0].id),
                AgentSkillBinding(agent_id=channel.agent_id, skill_id=visible_granted[0].id),
                AgentSkillBinding(agent_id=channel.agent_id, skill_id=hidden_ungranted[0].id),
                AgentSkillBinding(agent_id=channel.agent_id, skill_id=hidden_disabled[0].id),
                AgentSkillBinding(agent_id=channel.agent_id, skill_id=hidden_revoked[0].id),
                AgentSkillBinding(
                    agent_id=channel.agent_id, skill_id=hidden_unbound[0].id, is_deleted=True
                ),
                SkillUserGrant(
                    skill_id=visible_granted[0].id,
                    user_id=channel.actor_user_id,
                    granted_by=channel.actor_user_id,
                ),
                SkillUserGrant(
                    skill_id=hidden_revoked[0].id,
                    user_id=channel.actor_user_id,
                    granted_by=channel.actor_user_id,
                    is_deleted=True,
                ),
            ]
        )
        await session.commit()
        keys = {
            "visible_all": visible_all[0].key,
            "visible_granted": visible_granted[0].key,
            "hidden_ungranted": hidden_ungranted[0].key,
            "hidden_disabled": hidden_disabled[0].key,
            "hidden_revoked": hidden_revoked[0].key,
            "hidden_unbound": hidden_unbound[0].key,
        }
    try:
        yield keys
    finally:
        # 硬删除本次种子数据，避免阻塞共享 conftest 的租户清理（FK 物理约束）
        async with get_session_factory()() as session:
            if seeded_ids:
                await session.execute(
                    delete(SkillUserGrant).where(SkillUserGrant.skill_id.in_(seeded_ids))
                )
                await session.execute(
                    delete(AgentSkillBinding).where(
                        AgentSkillBinding.skill_id.in_(seeded_ids)
                    )
                )
                await session.execute(
                    delete(SkillArtifact).where(SkillArtifact.skill_id.in_(seeded_ids))
                )
                await session.execute(delete(Skill).where(Skill.id.in_(seeded_ids)))
            await session.commit()


def _query_params(
    channel: ChannelContext,
    *,
    platform_user_id: uuid.UUID | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict[str, str]:
    return {
        "agent_id": str(channel.agent_id),
        "platform_user_id": str(platform_user_id or channel.actor_user_id),
        "page": str(page),
        "page_size": str(page_size),
    }


async def test_b104_returns_only_effective_catalog_with_allowed_fields(
    client: AsyncClient, channel: ChannelContext, skills_env: dict[str, str]
) -> None:
    response = await client.get(
        SKILLS_URL, params=_query_params(channel), headers={"X-Tenant-Id": channel.tenant_id}
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["page"] == 1 and data["page_size"] == 20
    assert data["total"] == 2
    assert {item["key"] for item in data["items"]} == {
        skills_env["visible_all"],
        skills_env["visible_granted"],
    }
    for item in data["items"]:
        assert set(item) == {"skill_id", "key", "name", "platform_label", "description"}

    body = response.text
    for hidden_key in (
        skills_env["hidden_ungranted"],
        skills_env["hidden_disabled"],
        skills_env["hidden_revoked"],
        skills_env["hidden_unbound"],
    ):
        assert hidden_key not in body
    assert HIDDEN_CANARY not in body  # 未授权/禁用资源的存在性与描述不泄露
    assert ALWAYS_CANARY not in body  # 不返回 SKILL.md 全文/frontmatter


async def test_b104_rejects_user_without_agent_grant(
    client: AsyncClient, channel: ChannelContext, skills_env: dict[str, str]
) -> None:
    response = await client.get(
        SKILLS_URL,
        params=_query_params(channel, platform_user_id=channel.ungranted_user_id),
        headers={"X-Tenant-Id": channel.tenant_id},
    )
    assert response.status_code in (403, 404)
    assert response.json()["code"] == "AGENT_ACCESS_DENIED"
    assert skills_env["visible_all"] not in response.text


async def test_b104_pagination_is_bounded_and_validated(
    client: AsyncClient, channel: ChannelContext, skills_env: dict[str, str]
) -> None:
    statements: list[str] = []

    def _record(conn, cursor, statement, parameters, context, executemany) -> None:
        statements.append(statement)

    engine = get_engine().sync_engine
    event.listen(engine, "before_cursor_execute", _record)
    try:
        first_page = await client.get(
            SKILLS_URL,
            params=_query_params(channel, page=1, page_size=1),
            headers={"X-Tenant-Id": channel.tenant_id},
        )
        second_page = await client.get(
            SKILLS_URL,
            params=_query_params(channel, page=2, page_size=1),
            headers={"X-Tenant-Id": channel.tenant_id},
        )
        beyond = await client.get(
            SKILLS_URL,
            params=_query_params(channel, page=3, page_size=1),
            headers={"X-Tenant-Id": channel.tenant_id},
        )
        bad_page = await client.get(
            SKILLS_URL,
            params=_query_params(channel, page=0),
            headers={"X-Tenant-Id": channel.tenant_id},
        )
        bad_size = await client.get(
            SKILLS_URL,
            params=_query_params(channel, page_size=101),
            headers={"X-Tenant-Id": channel.tenant_id},
        )
    finally:
        event.remove(engine, "before_cursor_execute", _record)

    first = first_page.json()["data"]
    second = second_page.json()["data"]
    assert len(first["items"]) == 1 and first["total"] == 2
    assert len(second["items"]) == 1 and second["total"] == 2
    assert first["items"][0]["key"] != second["items"][0]["key"]
    assert beyond.json()["data"]["items"] == []
    assert beyond.json()["data"]["total"] == 2
    for response in (bad_page, bad_size):
        assert response.status_code == 422
        assert response.json()["code"] == "COMMON_VALIDATION_ERROR"

    skill_queries = [sql for sql in statements if "skill" in sql.lower()]
    assert any("count(" in sql.lower() for sql in skill_queries), skill_queries
    assert any("limit" in sql.lower() for sql in skill_queries), skill_queries
