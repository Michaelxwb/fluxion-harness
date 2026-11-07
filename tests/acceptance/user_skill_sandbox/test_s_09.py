"""[S-09] 系统 Skill：管理员导入自动通过、当前租户全局生效、无需绑定、普通用户不可改删。

真实边界：Console 登录/权限 → `/api/v1/skills/import` 事务 → `control.skill`/`skill_artifact`
→ `SkillRepository` effective 查询；不 mock 路由、审批字段或授权表。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient, Response
from muad_console_platform.api.security import CSRF_COOKIE, CSRF_HEADER
from muad_console_platform.application.auth_service import hash_password
from muad_console_platform.infrastructure.models.auth import ROLE_BUILDER, ConsoleAccount
from muad_console_platform.infrastructure.models.control import (
    AgentSkillBinding,
    ConfigAuditLog,
    Skill,
    SkillArtifact,
)
from muad_console_platform.infrastructure.repositories.skill_repository import SkillRepository
from muad_console_platform.main import app
from sqlalchemy.ext.asyncio import AsyncSession

from tests.console_skill.packages import demo_package

from .approval_support import ApprovalEnv, seed_second_agent

pytestmark = pytest.mark.integration

BUILDER_PASSWORD = "system-skill-builder-password"


async def _import_skill(
    client: AsyncClient,
    env: ApprovalEnv,
    *,
    key: str,
    version: str = "1.0.0",
    source: str | None = None,
    user_scope: str | None = None,
) -> Response:
    data: dict[str, str] = {"version": version, "key": key}
    if source is not None:
        data["source"] = source
    if user_scope is not None:
        data["user_scope"] = user_scope
    return await client.post(
        "/api/v1/skills/import",
        files={
            "file": (
                "skill.zip",
                demo_package(name=f"System Skill {key}"),
                "application/zip",
            )
        },
        data=data,
        headers={"X-Tenant-Id": env.tenant},
    )


@asynccontextmanager
async def _authenticated(
    tenant_id: str,
    username: str,
    password: str,
) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": password},
            headers={"X-Tenant-Id": tenant_id},
        )
        assert response.status_code == 200, response.text
        csrf_token = client.cookies.get(CSRF_COOKIE)
        assert csrf_token
        client.headers[CSRF_HEADER] = csrf_token
        client.headers["X-Tenant-Id"] = tenant_id
        yield client


async def _seed_builder(session: AsyncSession, env: ApprovalEnv) -> tuple[str, str]:
    username = f"builder-{uuid.uuid4().hex}"
    session.add(
        ConsoleAccount(
            tenant_id=env.tenant,
            username=username,
            display_name="System Skill Builder",
            password_hash=hash_password(BUILDER_PASSWORD),
            role=ROLE_BUILDER,
        )
    )
    await session.commit()
    return username, BUILDER_PASSWORD


async def _effective_keys(
    session: AsyncSession,
    tenant_id: str,
    agent_id: uuid.UUID,
    user_id: uuid.UUID,
) -> set[str]:
    rows = await SkillRepository(session).list_effective_for_agent(
        tenant_id, agent_id, user_id
    )
    return {skill.key for skill, _artifact in rows}


async def test_s09_system_import_auto_approves_and_is_effective_without_binding(
    admin_client: AsyncClient,
    approval_env: ApprovalEnv,
    session: AsyncSession,
) -> None:
    """[S-09] SYSTEM 导入自动 APPROVED/ALL/enabled；没有 binding 仍对所有用户/Agent 生效。"""
    key = f"s09-system-{uuid.uuid4().hex[:10]}"
    response = await _import_skill(
        admin_client,
        approval_env,
        key=key,
        source="SYSTEM",
        user_scope="SELECTED",
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["source"] == "SYSTEM"
    assert data["review_status"] == "APPROVED"
    assert data["user_scope"] == "ALL"
    assert data["enabled"] is True
    assert data["current_artifact"]["source"] == "SYSTEM"
    assert data["current_artifact"]["review_status"] == "APPROVED"

    skill_id = uuid.UUID(data["id"])
    artifact_id = uuid.UUID(data["current_artifact_id"])
    session.expire_all()
    skill = await session.get(Skill, skill_id)
    artifact = await session.get(SkillArtifact, artifact_id)
    assert skill is not None
    assert artifact is not None
    assert skill.source == "SYSTEM"
    assert skill.owner_user_id is None
    assert skill.user_scope == "ALL"
    assert skill.enabled is True
    assert skill.current_artifact_id == artifact.id
    assert artifact.source == "SYSTEM"
    assert artifact.review_status == "APPROVED"

    bindings = await session.scalar(
        sa.select(sa.func.count())
        .select_from(AgentSkillBinding)
        .where(
            AgentSkillBinding.skill_id == skill_id,
            AgentSkillBinding.is_deleted.is_(False),
        )
    )
    assert bindings == 0, "系统 Skill 导入不得自动创建 AgentSkillBinding"

    assert key in await _effective_keys(
        session, approval_env.tenant, approval_env.agent_id, approval_env.owner_id
    )
    assert key in await _effective_keys(
        session,
        approval_env.tenant,
        approval_env.agent_id,
        approval_env.other_user_id,
    )

    second_agent_id = await seed_second_agent(session, approval_env)
    assert key in await _effective_keys(
        session, approval_env.tenant, second_agent_id, approval_env.owner_id
    )


async def test_s09_system_import_source_admin_keeps_selected_scope(
    admin_client: AsyncClient,
    approval_env: ApprovalEnv,
) -> None:
    """[S-09] source=ADMIN 仍自动审批，但保留普通导入的 SELECTED 默认范围。"""
    key = f"s09-admin-{uuid.uuid4().hex[:10]}"
    response = await _import_skill(
        admin_client, approval_env, key=key, source="ADMIN"
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["source"] == "ADMIN"
    assert data["review_status"] == "APPROVED"
    assert data["user_scope"] == "SELECTED"
    assert data["enabled"] is True
    assert data["current_artifact"]["source"] == "ADMIN"


async def test_s09_builder_cannot_import_modify_or_delete_system_skill(
    admin_client: AsyncClient,
    approval_env: ApprovalEnv,
    session: AsyncSession,
) -> None:
    """[S-09] 普通用户不能导入/修改/删除系统 Skill，也不能向它追加 artifact。"""
    system_key = f"s09-locked-{uuid.uuid4().hex[:10]}"
    created = await _import_skill(
        admin_client, approval_env, key=system_key, source="SYSTEM"
    )
    assert created.status_code == 200, created.text
    data = created.json()["data"]
    skill_id = data["id"]

    username, password = await _seed_builder(session, approval_env)
    async with _authenticated(approval_env.tenant, username, password) as builder:
        forbidden_import = await _import_skill(
            builder,
            approval_env,
            key=f"s09-builder-system-{uuid.uuid4().hex[:10]}",
            source="SYSTEM",
        )
        assert forbidden_import.status_code == 403, forbidden_import.text
        assert forbidden_import.json()["code"] == "FORBIDDEN"

        updated = await builder.put(
            f"/api/v1/skills/{skill_id}",
            json={"description": "builder must not change system skill"},
            headers={"X-Tenant-Id": approval_env.tenant},
        )
        assert updated.status_code == 403, updated.text
        assert updated.json()["code"] == "FORBIDDEN"

        deleted = await builder.delete(
            f"/api/v1/skills/{skill_id}",
            headers={"X-Tenant-Id": approval_env.tenant},
        )
        assert deleted.status_code == 403, deleted.text
        assert deleted.json()["code"] == "FORBIDDEN"

        appended = await builder.post(
            f"/api/v1/skills/{skill_id}/artifacts",
            files={
                "file": (
                    "skill.zip",
                    demo_package(name="Builder Replacement"),
                    "application/zip",
                )
            },
            data={"version": "2.0.0"},
            headers={"X-Tenant-Id": approval_env.tenant},
        )
        assert appended.status_code == 403, appended.text
        assert appended.json()["code"] == "FORBIDDEN"

    session.expire_all()
    stored = await session.get(Skill, uuid.UUID(skill_id))
    assert stored is not None
    assert stored.is_deleted is False
    assert stored.description != "builder must not change system skill"


async def test_s09_admin_delete_soft_deletes_and_audits_system_skill(
    admin_client: AsyncClient,
    approval_env: ApprovalEnv,
    session: AsyncSession,
) -> None:
    """[S-09] 管理员删除系统 Skill 走软删 + 审计，删除后 effective 查询不再返回。"""
    key = f"s09-delete-{uuid.uuid4().hex[:10]}"
    created = await _import_skill(
        admin_client, approval_env, key=key, source="SYSTEM"
    )
    skill_id = uuid.UUID(created.json()["data"]["id"])

    deleted = await admin_client.delete(
        f"/api/v1/skills/{skill_id}",
        headers={"X-Tenant-Id": approval_env.tenant},
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["data"]["is_deleted"] is True

    session.expire_all()
    stored = await session.get(Skill, skill_id)
    assert stored is not None
    assert stored.is_deleted is True
    assert key not in await _effective_keys(
        session, approval_env.tenant, approval_env.agent_id, approval_env.owner_id
    )

    audit = await session.scalar(
        sa.select(ConfigAuditLog).where(
            ConfigAuditLog.tenant_id == approval_env.tenant,
            ConfigAuditLog.resource_type == "SKILL",
            ConfigAuditLog.resource_id == skill_id,
            ConfigAuditLog.action == "DELETE",
        )
    )
    assert audit is not None
    assert audit.actor_user_id == approval_env.admin_id
    assert audit.after_json is None
    assert audit.before_json is not None
    assert audit.before_json["key"] == key
