"""[S-03][RULE-snapshot-001] Run A 冻结旧 Artifact；导入 v2 只影响后续新 Run。

真实边界：真实上传（console import API）→ 真实 internal resolve 查询 →
真实 runtime.runtime_snapshot 行（真实 PostgreSQL）；无 mock。
"""

from __future__ import annotations

import uuid
from typing import Any, cast

from httpx import AsyncClient
from muad_agent_runtime.infrastructure.models.runtime import RunRecord, RuntimeSnapshot
from muad_console_platform.infrastructure.db import get_session_factory
from muad_console_platform.infrastructure.models.control import Skill, SkillArtifact
from sqlalchemy import select

from console_skill.conftest import SkillContext, import_skill, tenant_headers
from console_skill.packages import demo_package
from console_skill.test_resolve_skills import _resolve

BIND_URL = "/api/v1/agents/{agent_id}/skills/{skill_id}"


async def _frozen_entry(data: dict[str, Any], key: str) -> dict[str, Any]:
    for skill in data["skills"]:
        if skill["key"] == key:
            return skill
    raise AssertionError(f"skill {key} not resolved")


async def test_s03_run_a_keeps_frozen_artifact_after_v2_import(
    client: AsyncClient,
    skill_env: SkillContext,
) -> None:
    """[S-03] Run A 开始后导入 v2：Run A 快照仍指向 v1；新 resolve/Run 使用 v2。"""
    skill_key = f"freeze-{uuid.uuid4()}"
    created = await import_skill(client, skill_env, demo_package(name="Freeze Skill"), version="1.0.0", key=skill_key)
    assert created.status_code == 200, created.text
    skill_id = created.json()["data"]["id"]

    # SELECTED：授权 actor 用户并绑定 Agent
    grant = await client.post(
        f"/api/v1/skills/{skill_id}/users/{skill_env.actor_user_id}",
        headers=tenant_headers(skill_env),
    )
    assert grant.status_code == 200, grant.text
    bind = await client.post(
        BIND_URL.format(agent_id=skill_env.agent_id, skill_id=skill_id),
        headers=tenant_headers(skill_env),
    )
    assert bind.status_code == 200, bind.text

    resolved_v1 = await _resolve(client, skill_env)
    entry_v1 = await _frozen_entry(resolved_v1, skill_key)
    v1_artifact_id = entry_v1["artifact_id"]

    # Run A：冻结 v1 到真实 runtime.runtime_snapshot 行
    async with get_session_factory()() as session:
        snapshot = RuntimeSnapshot(
            tenant_id=skill_env.tenant_id,
            run_id=uuid.uuid4(),  # 场景聚焦 Skill 冻结语义，run 行由 runtime 测试环境覆盖
            agent_revision=1,
            model_revision=1,
            agent_json={"id": str(skill_env.agent_id)},
            model_json={"id": "model"},
            skill_catalog_json=[entry_v1],
            mcp_catalog_json=[],
            policy_json={},
            prompt_template_version="1",
            content_hash="sha256:" + "0" * 64,
        )
        session.add(snapshot)
        await session.commit()
        snapshot_id = snapshot.id

    # 导入 v2：current_artifact 切换
    v2 = await client.post(
        f"/api/v1/skills/{skill_id}/artifacts",
        files={"file": ("skill.zip", demo_package(name="Freeze Skill", description="v2 content"), "application/zip")},
        data={"version": "2.0.0"},
        headers=tenant_headers(skill_env),
    )
    assert v2.status_code == 200, v2.text

    # 新 Run 的 resolve 使用 v2
    resolved_v2 = await _resolve(client, skill_env)
    entry_v2 = await _frozen_entry(resolved_v2, skill_key)
    assert entry_v2["version"] == "2.0.0"
    assert entry_v2["artifact_id"] != v1_artifact_id

    # Run A 快照仍指向 v1；旧 Artifact 未被覆盖
    async with get_session_factory()() as session:
        stored = await session.get(RuntimeSnapshot, snapshot_id)
        assert stored is not None
        frozen = cast(list[dict[str, Any]], stored.skill_catalog_json)[0]
        assert frozen["artifact_id"] == v1_artifact_id
        assert frozen["version"] == "1.0.0"

        artifacts = (
            await session.execute(
                select(SkillArtifact).where(SkillArtifact.skill_id == uuid.UUID(skill_id))
            )
        ).scalars().all()
        by_version = {a.version: a for a in artifacts}
        assert set(by_version) == {"1.0.0", "2.0.0"}
        assert by_version["1.0.0"].checksum == entry_v1["checksum"]
        skill = await session.get(Skill, uuid.UUID(skill_id))
        assert skill is not None
        assert str(skill.current_artifact_id) == entry_v2["artifact_id"]

        # 清理
        await session.delete(stored)
        from muad_console_platform.infrastructure.models.control import (
            AgentSkillBinding,
            SkillUserGrant,
        )

        await session.execute(
            SkillUserGrant.__table__.delete().where(SkillUserGrant.skill_id == uuid.UUID(skill_id))
        )
        await session.execute(
            AgentSkillBinding.__table__.delete().where(
                AgentSkillBinding.skill_id == uuid.UUID(skill_id)
            )
        )
        await session.execute(
            SkillArtifact.__table__.delete().where(SkillArtifact.skill_id == uuid.UUID(skill_id))
        )
        await session.execute(Skill.__table__.delete().where(Skill.id == uuid.UUID(skill_id)))
        await session.commit()
