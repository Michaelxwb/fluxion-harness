"""[S-08] 管理员解绑+软删：单事务解除全部绑定/用户 grant、保留 artifact/审批/审计。

真实边界：Console API → 解绑/软删事务 → 真实 PostgreSQL → 审计；解绑后用真实
`resolve-definition` 验证执行入口不再返回该 Skill。
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from muad_console_platform.infrastructure.models.control import (
    AgentSkillBinding,
    ConfigAuditLog,
    Skill,
    SkillArtifact,
    SkillUserGrant,
)
from muad_console_platform.infrastructure.skill_artifact_store import artifact_path
from sqlalchemy.ext.asyncio import AsyncSession

from .approval_support import (
    ApprovalEnv,
    approve,
    resolve_definition,
    seed_model_credential,
    seed_second_agent,
    submit_user_skill,
)

pytestmark = pytest.mark.integration


def _delete_url(skill_id: str, *, unbind: bool, confirm: bool) -> str:
    params = []
    if unbind:
        params.append("unbind=true")
    if confirm:
        params.append("confirm=true")
    suffix = f"?{'&'.join(params)}" if params else ""
    return f"/api/v1/skills/{skill_id}{suffix}"


async def test_s08_unbind_and_soft_delete_keeps_artifact_review_and_audit(
    internal_client: AsyncClient,
    admin_client: AsyncClient,
    approval_env: ApprovalEnv,
    session: AsyncSession,
) -> None:
    submitted = await submit_user_skill(internal_client, approval_env, key="s08-delete")
    skill_id = uuid.UUID(submitted.skill_id)
    artifact_id = uuid.UUID(submitted.artifact_id)
    approved = await approve(admin_client, submitted.skill_id, submitted.artifact_id)
    assert approved.status_code == 200, approved.text

    second_agent = await seed_second_agent(session, approval_env)
    second_binding = await admin_client.post(
        f"/api/v1/agents/{second_agent}/skills/{submitted.skill_id}"
    )
    assert second_binding.status_code == 200, second_binding.text
    other_grant = await admin_client.post(
        f"/api/v1/skills/{submitted.skill_id}/users/{approval_env.other_user_id}"
    )
    assert other_grant.status_code == 200, other_grant.text
    await seed_model_credential(session, approval_env)

    before = await resolve_definition(internal_client, approval_env)
    assert submitted.skill_id in {item["skill_id"] for item in before["skills"]}

    # 未传 unbind 保持旧行为：有绑定即 SKILL_IN_USE，不偷偷解绑。
    rejected = await admin_client.delete(_delete_url(submitted.skill_id, unbind=False, confirm=False))
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["code"] == "SKILL_IN_USE"

    # 二次确认参数缺失：拒绝，且不改任何关系/soft-delete 状态。
    no_confirm = await admin_client.delete(_delete_url(submitted.skill_id, unbind=True, confirm=False))
    assert no_confirm.status_code == 422, no_confirm.text
    assert no_confirm.json()["code"] == "COMMON_VALIDATION_ERROR"

    session.expire_all()
    skill = await session.get(Skill, skill_id)
    assert skill is not None and skill.is_deleted is False
    active_bindings = await session.scalar(
        sa.select(sa.func.count())
        .select_from(AgentSkillBinding)
        .where(
            AgentSkillBinding.skill_id == skill_id,
            AgentSkillBinding.is_deleted.is_(False),
        )
    )
    active_grants = await session.scalar(
        sa.select(sa.func.count())
        .select_from(SkillUserGrant)
        .where(SkillUserGrant.skill_id == skill_id, SkillUserGrant.is_deleted.is_(False))
    )
    assert active_bindings == 2
    assert active_grants == 2

    deleted = await admin_client.delete(_delete_url(submitted.skill_id, unbind=True, confirm=True))
    assert deleted.status_code == 200, deleted.text
    data = deleted.json()["data"]
    assert data["skill_id"] == submitted.skill_id
    assert data["deleted"] is True
    assert {item["agent_id"] for item in data["unbound_agents"]} == {
        str(approval_env.agent_id),
        str(second_agent),
    }

    session.expire_all()
    skill = await session.get(Skill, skill_id)
    assert skill is not None
    assert skill.is_deleted is True
    assert skill.current_artifact_id == artifact_id

    bindings = list(
        (
            await session.scalars(
                sa.select(AgentSkillBinding).where(AgentSkillBinding.skill_id == skill_id)
            )
        ).all()
    )
    assert len(bindings) == 2
    assert all(row.is_deleted for row in bindings)

    grants = list(
        (
            await session.scalars(
                sa.select(SkillUserGrant).where(SkillUserGrant.skill_id == skill_id)
            )
        ).all()
    )
    assert len(grants) == 2
    assert all(row.is_deleted for row in grants)

    artifact = await session.get(SkillArtifact, artifact_id)
    assert artifact is not None
    assert artifact.is_deleted is False
    assert artifact.review_status == "APPROVED"
    assert artifact_path(artifact.storage_key).is_file()

    audits = list(
        (
            await session.scalars(
                sa.select(ConfigAuditLog)
                .where(
                    ConfigAuditLog.tenant_id == approval_env.tenant,
                    ConfigAuditLog.resource_id == skill_id,
                    ConfigAuditLog.action.in_(["SKILL_UNBIND", "SKILL_DELETE"]),
                )
                .order_by(ConfigAuditLog.create_time)
            )
        ).all()
    )
    by_action = {row.action: row for row in audits}
    assert set(by_action) == {"SKILL_UNBIND", "SKILL_DELETE"}
    for row in by_action.values():
        assert row.actor_user_id == approval_env.admin_id
        recorded = {item["agent_id"] for item in row.after_json["unbound_agents"]}
        assert recorded == {str(approval_env.agent_id), str(second_agent)}

    after = await resolve_definition(internal_client, approval_env)
    assert submitted.skill_id not in {item["skill_id"] for item in after["skills"]}

    download_after = await admin_client.get(
        f"/api/v1/skills/{submitted.skill_id}/artifacts/{submitted.artifact_id}/download"
    )
    assert download_after.status_code == 404, download_after.text

    # 幂等：已删除再调用返回成功，不重复产生解绑记录。
    again = await admin_client.delete(_delete_url(submitted.skill_id, unbind=True, confirm=True))
    assert again.status_code == 200, again.text
    assert again.json()["data"]["deleted"] is True
    assert again.json()["data"]["unbound_agents"] == []
