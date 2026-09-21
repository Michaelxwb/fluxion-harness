import uuid

from httpx import AsyncClient

from console_skill.conftest import SkillContext, tenant_headers


async def test_skill_agents_list_reflects_bindings(
    client: AsyncClient, skill_env: SkillContext
) -> None:
    listed = await client.get(
        f"/api/v1/skills/{skill_env.skill_all_id}/agents",
        headers=tenant_headers(skill_env),
    )
    assert listed.status_code == 200
    data = listed.json()["data"]
    assert data["total"] == 1
    entry = data["items"][0]
    assert entry["agent_id"] == str(skill_env.agent_id)
    assert entry["key"]
    assert entry["name"]
    assert entry["enabled"] is True

    unbound = await client.delete(
        f"/api/v1/agents/{skill_env.agent_id}/skills/{skill_env.skill_all_id}",
        headers=tenant_headers(skill_env),
    )
    assert unbound.status_code == 200
    after = await client.get(
        f"/api/v1/skills/{skill_env.skill_all_id}/agents",
        headers=tenant_headers(skill_env),
    )
    assert after.json()["data"]["total"] == 0

    missing = await client.get(
        f"/api/v1/skills/{uuid.uuid4()}/agents",
        headers=tenant_headers(skill_env),
    )
    assert missing.status_code == 404


async def test_agent_skill_binding_flow(client: AsyncClient, skill_env: SkillContext) -> None:
    listed = await client.get(
        f"/api/v1/agents/{skill_env.agent_id}/skills",
        headers=tenant_headers(skill_env),
    )
    assert listed.status_code == 200
    keys = {item["key"] for item in listed.json()["data"]["items"]}
    assert keys == {"skill-all", "skill-granted", "skill-ungranted", "skill-disabled"}

    bound = await client.post(
        f"/api/v1/agents/{skill_env.agent_id}/skills/{skill_env.skill_unbound_id}",
        headers=tenant_headers(skill_env),
    )
    assert bound.status_code == 200
    binding = bound.json()["data"]
    assert binding["skill_id"] == str(skill_env.skill_unbound_id)
    assert binding["key"] == "skill-unbound"
    assert binding["current_artifact_version"] == "1.0.0"
    assert binding["sort_order"] == 0

    repeated = await client.post(
        f"/api/v1/agents/{skill_env.agent_id}/skills/{skill_env.skill_unbound_id}",
        headers=tenant_headers(skill_env),
    )
    assert repeated.status_code == 200

    after_bind = (
        await client.get(
            f"/api/v1/agents/{skill_env.agent_id}/skills",
            headers=tenant_headers(skill_env),
        )
    ).json()["data"]["items"]
    assert len(after_bind) == 5

    unbound = await client.delete(
        f"/api/v1/agents/{skill_env.agent_id}/skills/{skill_env.skill_unbound_id}",
        headers=tenant_headers(skill_env),
    )
    assert unbound.status_code == 200
    assert unbound.json()["data"]["is_deleted"] is True

    after_unbind = (
        await client.get(
            f"/api/v1/agents/{skill_env.agent_id}/skills",
            headers=tenant_headers(skill_env),
        )
    ).json()["data"]["items"]
    assert len(after_unbind) == 4

    # v1.1 契约：解除幂等，再次解除仍返回成功
    again = await client.delete(
        f"/api/v1/agents/{skill_env.agent_id}/skills/{skill_env.skill_unbound_id}",
        headers=tenant_headers(skill_env),
    )
    assert again.status_code == 200
    assert again.json()["data"]["is_deleted"] is True


async def test_binding_unknown_agent_or_skill_returns_not_found(
    client: AsyncClient,
    skill_env: SkillContext,
) -> None:
    unknown_agent = await client.get(
        f"/api/v1/agents/{uuid.uuid4()}/skills",
        headers=tenant_headers(skill_env),
    )
    assert unknown_agent.status_code == 404
    assert unknown_agent.json()["code"] == "AGENT_NOT_FOUND"

    bind_unknown_agent = await client.post(
        f"/api/v1/agents/{uuid.uuid4()}/skills/{skill_env.skill_all_id}",
        headers=tenant_headers(skill_env),
    )
    assert bind_unknown_agent.status_code == 404
    assert bind_unknown_agent.json()["code"] == "AGENT_NOT_FOUND"

    bind_unknown_skill = await client.post(
        f"/api/v1/agents/{skill_env.agent_id}/skills/{uuid.uuid4()}",
        headers=tenant_headers(skill_env),
    )
    assert bind_unknown_skill.status_code == 404
    assert bind_unknown_skill.json()["code"] == "COMMON_NOT_FOUND"

    cross_tenant_skill = await client.post(
        f"/api/v1/agents/{skill_env.agent_id}/skills/{skill_env.skill_other_tenant_id}",
        headers=tenant_headers(skill_env),
    )
    assert cross_tenant_skill.status_code == 404
    assert cross_tenant_skill.json()["code"] == "COMMON_NOT_FOUND"
