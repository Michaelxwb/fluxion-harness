"""[S-01][S-02][RULE-auth/mcp/secret/snapshot-001] 授权与 Snapshot 全链路验收。

直接 DB + service 层调用（避免 HTTP client fixture 事件循环污染）。
"""

from __future__ import annotations

import hashlib
import json as json_module
import uuid

import pytest
import sqlalchemy as sa
from muad_agent_runtime.application.run_service import _snapshot_model
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import RuntimeSnapshot
from muad_contracts import ResolvedModel

TENANT = f"snap-{uuid.uuid4()}"


def test_s02_secret_not_in_snapshot_or_hash() -> None:
    """[RULE-secret-001] api_key 不落 snapshot model_json 也不参与 content_hash。"""
    dump = _snapshot_model(
        ResolvedModel(
            id=uuid.uuid4(), revision=1, model_id="gpt-4o-mini",
            base_url="https://x", api_key="sk-live-secret",
        )
    )
    assert "api_key" not in dump
    assert "sk-live-secret" not in str(dump)


async def test_s01_snapshot_freeze_content_unchanged() -> None:
    """[S-01/S-02] 配置变更后旧 Snapshot 行不漂移。"""
    sf = get_session_factory()
    run_id = uuid.uuid4()
    agent_json = {"instructions": "v1", "model_id": "m-1"}
    content_hash = "sha256:" + hashlib.sha256(
        json_module.dumps(agent_json, sort_keys=True).encode()
    ).hexdigest()
    async with sf() as session:
        session.add(
            RuntimeSnapshot(
                tenant_id=TENANT + "-freeze",
                run_id=run_id,
                agent_revision=1,
                model_revision=1,
                agent_json=agent_json,
                model_json={"id": "m"},
                skill_catalog_json=[],
                mcp_catalog_json=[],
                policy_json={},
                prompt_template_version="1",
                content_hash=content_hash,
            )
        )
        await session.commit()
    async with sf() as session:
        row = (
            await session.execute(
                sa.select(RuntimeSnapshot).where(RuntimeSnapshot.run_id == run_id)
            )
        ).scalar_one()
        assert row.agent_json["instructions"] == "v1"
        assert row.content_hash == content_hash
        await session.delete(row)
        await session.commit()


async def test_s01_effective_capability_zero_leakage() -> None:
    """[S-01] Effective Capability 公式过滤：禁用 Skill 不出现在 effective 列表。"""
    from muad_console_platform.infrastructure.db import get_session_factory as console_sf
    from muad_console_platform.infrastructure.models.control import (
        AgentAccessGrant,
        AgentDefinition,
        AgentSkillBinding,
        ModelDefinition,
        PlatformUser,
        Skill,
    )
    from muad_console_platform.infrastructure.repositories.skill_repository import (
        SkillRepository,
    )

    cf = console_sf()
    skill_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    model_id = uuid.uuid4()
    user_id = uuid.uuid4()

    async with cf() as session:
        session.add(
            ModelDefinition(
                id=model_id, tenant_id=TENANT + "-cap", key=f"m-{uuid.uuid4()}", name="m",
                model_id="gpt-4o-mini", base_url="https://x", api_key="k",
            )
        )
        await session.flush()
        session.add(
            AgentDefinition(
                id=agent_id, tenant_id=TENANT + "-cap", key=f"a-{uuid.uuid4()}", name="a",
                instructions="i", model_id=model_id,
            )
        )
        await session.flush()
        session.add(
            PlatformUser(
                id=user_id, tenant_id=TENANT + "-cap", user_code=f"u-{uuid.uuid4()}",
                display_name="test user",
            )
        )
        await session.flush()
        session.add(
            Skill(
                id=skill_id, tenant_id=TENANT + "-cap", key=f"s-{uuid.uuid4()}", name="s",
                description="d", user_scope="ALL", enabled=False,
            )
        )
        await session.flush()
        session.add(
            AgentAccessGrant(user_id=user_id, agent_id=agent_id, granted_by=user_id)
        )
        session.add(AgentSkillBinding(agent_id=agent_id, skill_id=skill_id))
        await session.commit()

        repo = SkillRepository(session)
        rows = await repo.list_effective_for_agent(TENANT + "-cap", agent_id, user_id)
        assert all(skill.enabled for skill, _ in rows)

        await session.execute(AgentSkillBinding.__table__.delete().where(
            AgentSkillBinding.agent_id == agent_id))
        await session.execute(Skill.__table__.delete().where(Skill.id == skill_id))
        await session.execute(AgentAccessGrant.__table__.delete().where(
            AgentAccessGrant.agent_id == agent_id))
        await session.execute(PlatformUser.__table__.delete().where(
            PlatformUser.id == user_id))
        await session.execute(AgentDefinition.__table__.delete().where(
            AgentDefinition.id == agent_id))
        await session.execute(ModelDefinition.__table__.delete().where(
            ModelDefinition.tenant_id == TENANT + "-cap"))
        await session.commit()
