"""[TASK-010] 审批验收共用：真实 Console 登录、真实草稿提交与审批请求封装。

只走真实 HTTP API、真实 PostgreSQL 与真实 Artifact Store；不在测试里调用审批服务实现。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient, Response
from muad_console_platform.api.security import CSRF_COOKIE, CSRF_HEADER
from muad_console_platform.application.auth_service import hash_password
from muad_console_platform.infrastructure.models.auth import ROLE_ADMIN, ConsoleAccount
from muad_console_platform.infrastructure.models.control import (
    AgentAccessGrant,
    AgentDefinition,
    ModelDefinition,
    PlatformUser,
)
from muad_console_platform.main import app
from sqlalchemy.ext.asyncio import AsyncSession

from .draft_support import create_draft, skill_md, submit_draft, write_file

ADMIN_PASSWORD = "approval-admin-password"


@dataclass(frozen=True)
class ApprovalEnv:
    """一个租户内的真实审批参与者：管理员、提交者、其他用户、上传 Agent。"""

    tenant: str
    admin_id: uuid.UUID
    admin_username: str
    owner_id: uuid.UUID
    other_user_id: uuid.UUID
    agent_id: uuid.UUID
    model_id: uuid.UUID
    owner_grant_id: uuid.UUID
    other_user_grant_id: uuid.UUID


@dataclass(frozen=True)
class SubmittedSkill:
    draft_id: str
    revision: int
    skill_id: str
    artifact_id: str
    version: str


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


async def seed_approval_env(session: AsyncSession, prefix: str) -> ApprovalEnv:
    """写入管理员/提交者/其他用户/Agent/AgentAccessGrant，返回只读上下文。"""
    tenant = _unique(prefix)
    username = _unique("approval-admin")
    model = ModelDefinition(
        tenant_id=tenant,
        key=_unique("approval-model"),
        name="Approval Model",
        model_id="gpt-4o-mini",
    )
    session.add(model)
    await session.flush()
    agent = AgentDefinition(
        tenant_id=tenant,
        key=_unique("approval-agent"),
        name="Approval Agent",
        instructions="run user skills",
        model_id=model.id,
    )
    owner = PlatformUser(
        tenant_id=tenant, user_code=_unique("owner"), display_name="Owner"
    )
    other_user = PlatformUser(
        tenant_id=tenant, user_code=_unique("other"), display_name="Other User"
    )
    admin = ConsoleAccount(
        tenant_id=tenant,
        username=username,
        display_name="Approval Admin",
        password_hash=hash_password(ADMIN_PASSWORD),
        role=ROLE_ADMIN,
    )
    session.add_all([agent, owner, other_user, admin])
    await session.flush()
    owner_grant = AgentAccessGrant(
        user_id=owner.id, agent_id=agent.id, granted_by=admin.id
    )
    other_grant = AgentAccessGrant(
        user_id=other_user.id, agent_id=agent.id, granted_by=admin.id
    )
    session.add_all([owner_grant, other_grant])
    await session.commit()
    return ApprovalEnv(
        tenant=tenant,
        admin_id=admin.id,
        admin_username=username,
        owner_id=owner.id,
        other_user_id=other_user.id,
        agent_id=agent.id,
        model_id=model.id,
        owner_grant_id=owner_grant.id,
        other_user_grant_id=other_grant.id,
    )


async def cleanup_approval_env(session: AsyncSession, env: ApprovalEnv) -> None:
    """按外键顺序清理本租户的审批/关系/账号/模型行。"""
    params = {"tenant": env.tenant}
    statements = (
        "DELETE FROM control.skill_user_grant WHERE skill_id IN "
        "(SELECT id FROM control.skill WHERE tenant_id = :tenant)",
        "DELETE FROM control.agent_skill_binding WHERE skill_id IN "
        "(SELECT id FROM control.skill WHERE tenant_id = :tenant)",
        "DELETE FROM control.skill_import_idempotency WHERE tenant_id = :tenant",
        "DELETE FROM control.skill_artifact WHERE skill_id IN "
        "(SELECT id FROM control.skill WHERE tenant_id = :tenant)",
        "DELETE FROM control.skill WHERE tenant_id = :tenant",
        "DELETE FROM control.skill_draft WHERE tenant_id = :tenant",
        "DELETE FROM control.config_audit_log WHERE tenant_id = :tenant",
        "DELETE FROM control.agent_access_grant WHERE agent_id IN "
        "(SELECT id FROM control.agent_definition WHERE tenant_id = :tenant)",
        "DELETE FROM control.agent_definition WHERE tenant_id = :tenant",
        "DELETE FROM control.model_definition WHERE tenant_id = :tenant",
        "DELETE FROM control.platform_user WHERE tenant_id = :tenant",
        "DELETE FROM control.console_session WHERE account_id IN "
        "(SELECT id FROM control.console_account WHERE tenant_id = :tenant)",
        "DELETE FROM control.console_account WHERE tenant_id = :tenant",
    )
    for statement in statements:
        await session.execute(sa.text(statement), params)
    await session.commit()


async def seed_second_agent(session: AsyncSession, env: ApprovalEnv) -> uuid.UUID:
    """为同一租户补一个真实 Agent，用于验证单关系解绑不误伤其他绑定。"""
    agent = AgentDefinition(
        tenant_id=env.tenant,
        key=_unique("approval-agent-2"),
        name="Approval Agent Two",
        instructions="run user skills",
        model_id=env.model_id,
    )
    session.add(agent)
    await session.commit()
    return agent.id


@asynccontextmanager
async def authenticated_admin(env: ApprovalEnv) -> AsyncIterator[AsyncClient]:
    """真实登录 + CSRF 头；与浏览器管理员路径一致。"""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/auth/login",
            json={"username": env.admin_username, "password": ADMIN_PASSWORD},
            headers={"X-Tenant-Id": env.tenant},
        )
        assert response.status_code == 200, response.text
        csrf_token = client.cookies.get(CSRF_COOKIE)
        assert csrf_token
        client.headers[CSRF_HEADER] = csrf_token
        client.headers["X-Tenant-Id"] = env.tenant
        yield client


def review_request(
    client: AsyncClient,
    skill_id: str,
    artifact_id: str,
    *,
    action: str,
    review_note: str | None = None,
) -> Any:
    body: dict[str, Any] = {"artifact_id": artifact_id, "action": action}
    if review_note is not None:
        body["review_note"] = review_note
    return client.post(f"/api/v1/skills/{skill_id}/review", json=body)


async def approve(
    client: AsyncClient,
    skill_id: str,
    artifact_id: str,
    *,
    review_note: str | None = None,
) -> Response:
    return await review_request(
        client, skill_id, artifact_id, action="APPROVE", review_note=review_note
    )


async def reject(
    client: AsyncClient,
    skill_id: str,
    artifact_id: str,
    *,
    review_note: str | None = None,
) -> Response:
    return await review_request(
        client, skill_id, artifact_id, action="REJECT", review_note=review_note
    )


def _package_files(*, platform_value: str = "mssp") -> list[tuple[str, str]]:
    return [
        ("SKILL.md", skill_md(platforms=[platform_value])),
        (
            "scripts/run.py",
            "def main():\n    print('ok')\n\n\nif __name__ == '__main__':\n    main()\n",
        ),
        (
            "muad.skill.json",
            '{"runtime": "script", "entrypoint": "scripts/run.py", "author": "张三"}\n',
        ),
    ]


async def fill_draft(
    client: AsyncClient,
    env: ApprovalEnv,
    *,
    key: str,
    owner_id: uuid.UUID | None = None,
) -> tuple[str, int]:
    """创建草稿并写入最小合法包，返回 `(draft_id, revision)`。"""
    owner = owner_id or env.owner_id
    draft = await create_draft(
        client, tenant_id=env.tenant, owner_user_id=owner, key=key
    )
    draft_id, revision = draft["id"], 1
    for path, content in _package_files():
        response = await write_file(
            client,
            tenant_id=env.tenant,
            owner_user_id=owner,
            draft_id=draft_id,
            path=path,
            content=content,
            expected_revision=revision,
        )
        assert response.status_code == 200, response.text
        revision = response.json()["data"]["revision"]
    return draft_id, revision


async def submit_user_skill(
    client: AsyncClient,
    env: ApprovalEnv,
    *,
    key: str,
    owner_id: uuid.UUID | None = None,
    version: str = "1.0.0",
    with_agent: bool = True,
    channel: str = "WEBCHAT",
) -> SubmittedSkill:
    """创建草稿、写入最小合法包并提交，返回 PENDING 提交结果。"""
    owner = owner_id or env.owner_id
    draft_id, revision = await fill_draft(
        client, env, key=key, owner_id=owner
    )
    response = await submit_draft(
        client,
        tenant_id=env.tenant,
        owner_user_id=owner,
        draft_id=draft_id,
        expected_revision=revision,
        version=version,
        submitted_agent_id=env.agent_id if with_agent else None,
        submitted_channel=channel,
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    return SubmittedSkill(
        draft_id=draft_id,
        revision=revision,
        skill_id=data["skill_id"],
        artifact_id=data["artifact_id"],
        version=data["version"],
    )


async def modify_and_submit(
    client: AsyncClient,
    env: ApprovalEnv,
    submitted: SubmittedSkill,
    *,
    owner_id: uuid.UUID | None = None,
    version: str,
    content: str = "print('v2')\n",
) -> Response:
    """修改草稿内容推进 revision，再提交一个新版本；返回原始响应供断言冲突。"""
    owner = owner_id or env.owner_id
    changed = await write_file(
        client,
        tenant_id=env.tenant,
        owner_user_id=owner,
        draft_id=submitted.draft_id,
        path="scripts/run.py",
        content=content,
        expected_revision=submitted.revision,
    )
    assert changed.status_code == 200, changed.text
    next_revision = changed.json()["data"]["revision"]
    return await submit_draft(
        client,
        tenant_id=env.tenant,
        owner_user_id=owner,
        draft_id=submitted.draft_id,
        expected_revision=next_revision,
        version=version,
        submitted_agent_id=env.agent_id,
        submitted_channel="WEBCHAT",
    )
