"""[每次 Fire 的受理材料] 按模式取齐 Snapshot v4 依赖范围内的完整非密钥材料。

真实边界（业务路径不 mock）：真实 PG、真实内部路由与 `require_service_identity`、
真实 `ScheduleResolveService`（Skill/MCP/Model/Memory 都来自真实仓储查询）。
本文件只断言**只读取数**与错误分类，不涉及 Worker 侧快照组装。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from muad_console_platform.infrastructure.db import get_engine, get_session_factory
from muad_console_platform.infrastructure.models.control import (
    AgentAccessGrant,
    AgentDefinition,
    AgentSkillBinding,
    ModelDefinition,
    Skill,
    SkillArtifact,
    SkillUserGrant,
)
from muad_console_platform.infrastructure.models.mcp import (
    AgentMcpBinding,
    McpServer,
)

from console_internal.conftest import TenantContext
from tests.internal_service import internal_service_token, service_headers  # noqa: F401

URL = "/internal/runtime/schedule-admission-materials"

MCP_SECRET = "Bearer mcp-super-secret-must-not-leak"
SKILL_CHECKSUM = "sha256:" + "b" * 64
SKILL_TOOL_NAME = "run_report"
MCP_TOOL_NAME = "customer.report"


@dataclass(frozen=True)
class Materials:
    skill_id: uuid.UUID
    artifact_id: uuid.UUID
    disabled_skill_id: uuid.UUID
    artifactless_skill_id: uuid.UUID
    ungranted_skill_id: uuid.UUID
    mcp_server_id: uuid.UUID


def _payload(
    tenant: TenantContext,
    *,
    mode: str,
    skill_id: uuid.UUID | None = None,
    allowlist: list[dict[str, str]] | None = None,
) -> dict[str, object]:
    body: dict[str, object] = {
        "agent_id": str(tenant.agent_id),
        "actor_user_id": str(tenant.actor_user_id),
        "mode": mode,
    }
    if skill_id is not None:
        body["skill_id"] = str(skill_id)
    body["tool_allowlist"] = allowlist or []
    return body


@pytest.fixture
async def materials(tenant: TenantContext) -> AsyncIterator[Materials]:
    """种一套 Skill/MCP：可用、禁用、无 current artifact、未授权四种状态各一。"""
    session_factory = get_session_factory()
    async with session_factory() as session:
        async def add_skill(
            *, key: str, enabled: bool, with_artifact: bool, user_scope: str = "SELECTED"
        ) -> tuple[Skill, SkillArtifact | None]:
            skill = Skill(
                tenant_id=tenant.tenant_id,
                key=key,
                name=key,
                description=f"{key} description",
                user_scope=user_scope,
                source="ADMIN",
                enabled=enabled,
            )
            session.add(skill)
            await session.flush()
            artifact = None
            if with_artifact:
                artifact = SkillArtifact(
                    skill_id=skill.id,
                    version="1.2.0",
                    checksum=SKILL_CHECKSUM,
                    storage_key=f"skills/{key}.tar.gz",
                    frontmatter_json={
                        "platforms": ["WECOM"],
                        "retry_safety": "IDEMPOTENT",
                    },
                    manifest_json={"entry": "main.py"},
                    execution_mode="ASYNC",
                    package_size=1024,
                    validation_status="VALID",
                    created_by=tenant.actor_user_id,
                    review_status="APPROVED",
                    source="ADMIN",
                )
                session.add(artifact)
                await session.flush()
                skill.current_artifact_id = artifact.id
                await session.flush()
            return skill, artifact

        usable, usable_artifact = await add_skill(
            key=f"reportable-{uuid.uuid4()}", enabled=True, with_artifact=True
        )
        disabled, _ = await add_skill(
            key=f"disabled-{uuid.uuid4()}", enabled=False, with_artifact=True
        )
        artifactless, _ = await add_skill(
            key=f"artifactless-{uuid.uuid4()}", enabled=True, with_artifact=False
        )
        ungranted, _ = await add_skill(
            key=f"ungranted-{uuid.uuid4()}", enabled=True, with_artifact=True
        )
        for skill in (usable, disabled, artifactless):
            session.add(AgentSkillBinding(agent_id=tenant.agent_id, skill_id=skill.id))
        session.add(AgentSkillBinding(agent_id=tenant.agent_id, skill_id=ungranted.id))
        for skill in (usable, disabled, artifactless):
            session.add(
                SkillUserGrant(
                    skill_id=skill.id,
                    user_id=tenant.actor_user_id,
                    granted_by=tenant.other_actor_user_id,
                )
            )
        server = McpServer(
            tenant_id=tenant.tenant_id,
            key=f"crm-{uuid.uuid4()}",
            name="CRM",
            endpoint="https://crm.example.com/mcp",
            auth_secret=MCP_SECRET,
            auth_config_json={"headers": {"X-Tenant": "demo"}, "secret_header": "X-Api-Key"},
            user_scope="ALL",
            enabled=True,
            tool_catalog_json=[
                {
                    "name": MCP_TOOL_NAME,
                    "input_schema": {
                        "type": "object",
                        "properties": {"account": {"type": "string"}},
                        "required": ["account"],
                    },
                    "retry_safety": "READ_ONLY",
                }
            ],
            tool_catalog_revision=4,
        )
        session.add(server)
        await session.flush()
        session.add(AgentMcpBinding(agent_id=tenant.agent_id, mcp_server_id=server.id))
        await session.commit()
        context = Materials(
            skill_id=usable.id,
            artifact_id=usable_artifact.id,  # type: ignore[union-attr]
            disabled_skill_id=disabled.id,
            artifactless_skill_id=artifactless.id,
            ungranted_skill_id=ungranted.id,
            mcp_server_id=server.id,
        )
    try:
        yield context
    finally:
        async with session_factory() as session:
            tenant_skills = "SELECT id FROM control.skill WHERE tenant_id = :tenant_id"
            for statement in (
                f"DELETE FROM control.agent_skill_binding WHERE skill_id IN ({tenant_skills})",
                f"DELETE FROM control.skill_user_grant WHERE skill_id IN ({tenant_skills})",
                "DELETE FROM control.agent_mcp_binding WHERE mcp_server_id IN "
                "(SELECT id FROM control.mcp_server WHERE tenant_id = :tenant_id)",
                "DELETE FROM control.mcp_user_grant WHERE mcp_server_id IN "
                "(SELECT id FROM control.mcp_server WHERE tenant_id = :tenant_id)",
                "UPDATE control.skill SET current_artifact_id = NULL WHERE tenant_id = :tenant_id",
                "DELETE FROM control.skill_artifact WHERE skill_id IN "
                "(SELECT id FROM control.skill WHERE tenant_id = :tenant_id)",
                "DELETE FROM control.skill WHERE tenant_id = :tenant_id",
                "DELETE FROM control.mcp_server WHERE tenant_id = :tenant_id",
            ):
                await session.execute(sa.text(statement), {"tenant_id": tenant.tenant_id})
            await session.commit()


def _allowlist(materials: Materials, server_key: str) -> list[dict[str, str]]:
    return [
        {"source": "MCP", "server_key": server_key, "tool_name": MCP_TOOL_NAME},
        {
            "source": "SKILL",
            "skill_id": str(materials.skill_id),
            "tool_name": SKILL_TOOL_NAME,
        },
    ]


async def _server_key(tenant: TenantContext, materials: Materials) -> str:
    session_factory = get_session_factory()
    async with session_factory() as session:
        key = await session.scalar(
            sa.select(McpServer.key).where(McpServer.id == materials.mcp_server_id)
        )
    return str(key)


async def test_agentic_materials_are_complete_and_secret_free(
    client: AsyncClient, tenant: TenantContext, materials: Materials
) -> None:
    """AGENTIC：Agent/Model 全量、Skill artifact、工具完整 schema、MCP 非密定义、记忆材料。"""
    server_key = await _server_key(tenant, materials)
    response = await client.post(
        URL,
        json=_payload(
            tenant, mode="AGENTIC", allowlist=_allowlist(materials, server_key)
        ),
        headers=service_headers(tenant.tenant_id),
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]

    assert data["mode"] == "AGENTIC"
    assert data["agent"]["instructions"] == tenant.agent_instructions
    assert data["agent"]["runtime_config"] == tenant.agent_runtime_config

    model = data["model"]
    assert model["id"] == str(tenant.model_id)
    assert model["deployment_id"] == str(tenant.model_deployment_id)
    assert model["model_id"] == tenant.model_model_id
    assert model["base_url"] == tenant.model_base_url
    assert model["pool_members"] and model["pool_members"][0]["deployment_id"] == str(
        tenant.model_deployment_id
    )

    assert [skill["skill_id"] for skill in data["skills"]] == [str(materials.skill_id)]
    skill = data["skills"][0]
    assert skill["artifact_id"] == str(materials.artifact_id)
    assert skill["checksum"] == SKILL_CHECKSUM
    assert skill["execution_mode"] == "ASYNC"
    assert skill["frontmatter"]["retry_safety"] == "IDEMPOTENT"

    by_source = {tool["source"]: tool for tool in data["tools"]}
    mcp_tool = by_source["MCP"]
    assert mcp_tool["tool_name"] == MCP_TOOL_NAME
    assert mcp_tool["input_schema"]["required"] == ["account"]
    assert mcp_tool["retry_safety"] == "READ_ONLY"
    assert mcp_tool["catalog_revision"] == 4
    assert mcp_tool["server_id"] == str(materials.mcp_server_id)
    skill_tool = by_source["SKILL"]
    assert skill_tool["artifact_id"] == str(materials.artifact_id)
    assert skill_tool["execution_mode"] == "ASYNC"
    assert skill_tool["retry_safety"] == "IDEMPOTENT"

    server = data["mcp_servers"][0]
    assert server["endpoint"] == "https://crm.example.com/mcp"
    assert server["headers"] == {"X-Tenant": "demo"}
    assert server["secret_header"] == "X-Api-Key"
    assert server["definitions"][0]["name"] == MCP_TOOL_NAME
    assert server["catalog_revision"] == 4

    # 记忆材料是**候选**不是块：任务侧按 LIST 口径渲染一次后冻结。
    assert data["memory"] is not None
    assert data["memory"]["degraded"] is False
    assert data["memory"]["taxonomy_version"] >= 1
    assert data["grants"]["agent_access"] is True

    # 密钥只以引用存在：响应原文里连明文都不出现。
    text = response.text
    assert MCP_SECRET not in text
    assert tenant.model_api_key not in text
    assert "auth_secret" not in text and "api_key" not in text


async def test_reminder_materials_need_no_skill_model_or_credential(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """REMINDER 不依赖 Skill/Model/凭据：模型没有成员凭据也照常受理。"""
    session_factory = get_session_factory()
    async with session_factory() as session:
        model = ModelDefinition(
            tenant_id=tenant.tenant_id,
            key=f"bare-model-{uuid.uuid4()}",
            name="Bare Model",
            protocol="OPENAI",
            model_id="gpt-4o-mini",
            revision=7,
        )
        session.add(model)
        await session.flush()
        agent = AgentDefinition(
            tenant_id=tenant.tenant_id,
            key=f"reminder-agent-{uuid.uuid4()}",
            name="Reminder Agent",
            instructions="Reminder only.",
            model_id=model.id,
            enabled=True,
        )
        session.add(agent)
        await session.flush()
        session.add(
            AgentAccessGrant(
                user_id=tenant.actor_user_id,
                agent_id=agent.id,
                granted_by=tenant.other_actor_user_id,
            )
        )
        await session.commit()
        agent_id = agent.id

    response = await client.post(
        URL,
        json={
            "agent_id": str(agent_id),
            "actor_user_id": str(tenant.actor_user_id),
            "mode": "REMINDER",
        },
        headers=service_headers(tenant.tenant_id),
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["skills"] == [] and data["mcp_servers"] == [] and data["tools"] == []
    assert data["memory"] is None
    assert data["model"]["base_url"] is None
    assert data["model"]["revision"] == 7


async def test_skill_mode_rejects_each_permanent_dependency(
    client: AsyncClient, tenant: TenantContext, materials: Materials
) -> None:
    """SKILL：缺失/禁用/无 artifact/未授权分别给出精确原因与修复动作。"""
    cases = [
        (uuid.uuid4(), "SKILL_MISSING"),
        (materials.disabled_skill_id, "SKILL_DISABLED"),
        (materials.artifactless_skill_id, "ARTIFACT_MISSING"),
        (materials.ungranted_skill_id, "GRANT_REVOKED"),
    ]
    for skill_id, expected in cases:
        response = await client.post(
            URL,
            json=_payload(tenant, mode="SKILL", skill_id=skill_id),
            headers=service_headers(tenant.tenant_id),
        )
        assert response.status_code >= 400, (expected, response.text)
        body = response.json()
        assert body["data"]["admission_code"] == expected, body
        assert body["data"]["repair"], body


async def test_skill_mode_resolves_exactly_the_target_skill(
    client: AsyncClient, tenant: TenantContext, materials: Materials
) -> None:
    response = await client.post(
        URL,
        json=_payload(tenant, mode="SKILL", skill_id=materials.skill_id),
        headers=service_headers(tenant.tenant_id),
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert [skill["skill_id"] for skill in data["skills"]] == [str(materials.skill_id)]
    assert data["memory"] is None


async def test_agentic_rejects_missing_and_revoked_tools(
    client: AsyncClient, tenant: TenantContext, materials: Materials
) -> None:
    server_key = await _server_key(tenant, materials)
    missing_tool = await client.post(
        URL,
        json=_payload(
            tenant,
            mode="AGENTIC",
            allowlist=[{"source": "MCP", "server_key": server_key, "tool_name": "nope"}],
        ),
        headers=service_headers(tenant.tenant_id),
    )
    assert missing_tool.status_code >= 400
    assert missing_tool.json()["data"]["admission_code"] == "TOOL_MISSING"

    revoked_server = await client.post(
        URL,
        json=_payload(
            tenant,
            mode="AGENTIC",
            allowlist=[{"source": "MCP", "server_key": "unknown", "tool_name": MCP_TOOL_NAME}],
        ),
        headers=service_headers(tenant.tenant_id),
    )
    assert revoked_server.status_code >= 400
    assert revoked_server.json()["data"]["admission_code"] == "TOOL_REVOKED"


async def test_agentic_rejects_skill_tool_wildcard(
    client: AsyncClient, tenant: TenantContext, materials: Materials
) -> None:
    response = await client.post(
        URL,
        json=_payload(
            tenant,
            mode="AGENTIC",
            allowlist=[
                {"source": "SKILL", "skill_id": str(materials.skill_id), "tool_name": "*"}
            ],
        ),
        headers=service_headers(tenant.tenant_id),
    )

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "SCHEDULE_TOOL_WILDCARD_UNSUPPORTED"
    assert body["data"]["admission_code"] == "WILDCARD_NOT_SUPPORTED"
    assert body["data"]["allowed_values"] == [f"SKILL:{materials.skill_id}:execute"]


async def test_service_identity_and_tenant_isolation(
    client: AsyncClient, tenant: TenantContext
) -> None:
    unauthenticated = await client.post(
        URL,
        json=_payload(tenant, mode="REMINDER"),
        headers={"X-Tenant-Id": tenant.tenant_id},
    )
    assert unauthenticated.status_code == 403
    assert unauthenticated.json()["code"] == "FORBIDDEN"

    other_tenant = await client.post(
        URL,
        json=_payload(tenant, mode="REMINDER"),
        headers=service_headers(tenant.other_tenant_id),
    )
    assert other_tenant.status_code == 404
    assert other_tenant.json()["code"] == "AGENT_NOT_FOUND"


async def test_materials_read_has_no_side_effects(
    client: AsyncClient, tenant: TenantContext, materials: Materials
) -> None:
    """静态 Preflight 不写库：整个过程只有 SELECT。"""
    writes: list[str] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):  # type: ignore[no-untyped-def]
        head = statement.lstrip().split(" ", 1)[0].upper()
        if head in {"INSERT", "UPDATE", "DELETE", "TRUNCATE"}:
            writes.append(statement)

    engine = get_engine().sync_engine
    sa.event.listen(engine, "before_cursor_execute", _capture)
    try:
        response = await client.post(
            URL,
            json=_payload(tenant, mode="SKILL", skill_id=materials.skill_id),
            headers=service_headers(tenant.tenant_id),
        )
    finally:
        sa.event.remove(engine, "before_cursor_execute", _capture)
    assert response.status_code == 200, response.text
    assert writes == [], writes
