"""B-243 受信聊天身份核验与授权解析（design §3.4 API-12；TASK-043）。

真实边界（业务路径不 mock）：

- **真实 Console 应用服务/HTTP 依赖**：直连 `muad_console_platform.main.app`（`tests/console_internal/`
  `conftest.py` 的 `client`）——真实路由表、真实 api-kit 封套/错误目录、真实服务身份门控；
- **真实 PostgreSQL 与事务**：CHAT 会话/来源 Token/用户与三层授权行都是真实行（会话经真实公开
  exchange 建立，失效边界用真实 SQL 压时间），主体与授权集合只从 PG 记录导出并回读核对；
- **不发起模型调用**：本路径无外部模型请求，故"外部模型真实 HTTP 探针"边界在此不适用。

断言要点：

- 门控验证 CHAT 会话/来源 Token/用户/部署租户：未知凭据 401、撤销/停用 401、过期 401、
  跨部署租户 403、X-Chat-Context 不匹配 409，且都不回显任何凭据或主体；
- 主体从 PG 记录导出：自报 user_id/tenant/agent_id 在契约层被拒（422），伪造 X-User-Id 头无效；
- 授权解析口径与 Console 侧一致（`has_active_grant` 同口径）：真实 `AgentAccessGrant` +
  `is_deleted=false` + Agent `enabled`；未授权/禁用/软删不返回，也不泄露存在性；
- CHAT 身份不授予 Console 权限：响应只有统一 principal 与授权集合，无角色/权限/账号字段；
- 三层能力授权不变：门控导出的主体走既有内部定义解析，SELECTED 资源仍需用户级叠加授权，
  禁用/软删/未绑定资源不进入 Skill/MCP 解析结果。

清库隔离：`conftest.py` 的租户清理不含 CHAT 新表；本文件自带按 `tenant_id` 的清理
（会话→Token→审计/设置），tenant 每用例唯一。
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from httpx import Response as HttpResponse
from muad_console_platform.api.chat_security import CHAT_SESSION_COOKIE
from muad_console_platform.application.audit_service import AuditActor
from muad_console_platform.application.chat_token_service import (
    ChatTokenService,
    hash_chat_token,
)
from muad_console_platform.infrastructure.db import get_session_factory
from muad_console_platform.infrastructure.models.auth import ChatAccessToken, ChatSession
from muad_console_platform.infrastructure.models.control import (
    AgentSkillBinding,
    PlatformUser,
    Skill,
    SkillArtifact,
    SkillUserGrant,
)
from muad_console_platform.infrastructure.models.mcp import (
    AgentMcpBinding,
    McpServer,
    McpUserGrant,
)
from sqlalchemy import select, text, update

from console_internal.conftest import TenantContext
from tests.internal_service import internal_service_token, service_headers  # noqa: F401  (fixture 注册)

pytestmark = pytest.mark.integration

#: 内部身份解析端点（design API-12；与 `/internal/channel/*` 同服务身份门控）。
URL = "/internal/channel/chat-principal"
EXCHANGE_URL = "/api/v1/webchat/auth/exchange"
RESOLVE_URL = "/internal/runtime/resolve-definition"
#: 与真实 Console 应用共用同一 Host（httpx `base_url=http://test`）。
SAME_ORIGIN = "http://test"
#: 统一 principal 恰好这四个字段（design §3.4）；多一个都是权限面。
PRINCIPAL_FIELDS = {"tenant_id", "platform_user_id", "session_id", "auth_method"}
#: 管理角色的 Console 语义字段：CHAT principal 不得继承任何一个。
CONSOLE_ROLE_FIELDS = {"role", "permissions", "username", "account_id", "user_code"}
AGENT_FIELDS = {"agent_id", "key", "name"}
PURGE_STATEMENTS = (
    "DELETE FROM control.chat_session WHERE tenant_id = :tenant_id",
    "DELETE FROM control.chat_access_token WHERE tenant_id = :tenant_id",
    "DELETE FROM control.config_audit_log WHERE tenant_id = :tenant_id",
    "DELETE FROM control.platform_setting WHERE tenant_id = :tenant_id",
)
ACTOR = AuditActor(account_id=uuid.uuid4(), source_ip="203.0.113.9")


@dataclass(frozen=True)
class ChatIdentity:
    """本栈租户里的一条真实 CHAT 会话（经真实签发 + 真实 exchange 建立）。"""

    user_token: str
    session_token: str
    session_id: uuid.UUID
    chat_context: uuid.UUID
    expires_at: datetime


# --- 真实链路搭建与清库 ---------------------------------------------------------------


async def _purge(tenant_id: str) -> None:
    async with get_session_factory()() as session:
        for statement in PURGE_STATEMENTS:
            await session.execute(text(statement), {"tenant_id": tenant_id})
        await session.commit()


async def _identity(client: AsyncClient, tenant_id: str, user_id: uuid.UUID) -> ChatIdentity:
    """真实签发 + 真实公开 exchange：会话原文只经 HttpOnly Cookie 面出现一次。"""
    async with get_session_factory()() as session:
        issued = await ChatTokenService(session).issue_token(
            tenant_id=tenant_id, user_id=user_id, expected_revision=0, actor=ACTOR
        )
        await session.commit()
        user_token = issued.token.get_secret_value()
    response = await client.post(
        EXCHANGE_URL, json={"token": user_token}, headers={"Origin": SAME_ORIGIN}
    )
    assert response.status_code == 200, response.text
    session_token = client.cookies.get(CHAT_SESSION_COOKIE)
    assert session_token, "exchange 必须下发独立 CHAT 会话 Cookie"
    info = response.json()["data"]
    identity = ChatIdentity(
        user_token=user_token,
        session_token=session_token,
        session_id=uuid.UUID(info["session_id"]),
        chat_context=uuid.UUID(info["auth_context_id"]),
        expires_at=_expires_at(info["expires_at"]),
    )
    assert identity.chat_context == identity.session_id
    assert identity.session_token != identity.user_token
    return identity


@pytest.fixture
async def chat_identity(client: AsyncClient, tenant: TenantContext) -> AsyncIterator[ChatIdentity]:
    """真实平台用户的 CHAT 会话：主体只能从 Token/会话记录导出。"""
    identity = await _identity(client, tenant.tenant_id, tenant.actor_user_id)
    try:
        yield identity
    finally:
        await _purge(tenant.tenant_id)


@pytest.fixture
async def foreign_identity(client: AsyncClient, tenant: TenantContext) -> AsyncIterator[ChatIdentity]:
    """另一个部署租户的有效 CHAT 会话：跨部署租户必须被本栈明确拒绝。"""
    async with get_session_factory()() as session:
        user = PlatformUser(
            tenant_id=tenant.other_tenant_id,
            user_code=f"u-{uuid.uuid4().hex[:8]}",
            display_name="Other Tenant User",
        )
        session.add(user)
        await session.commit()
        user_id = user.id
    identity = await _identity(client, tenant.other_tenant_id, user_id)
    try:
        yield identity
    finally:
        await _purge(tenant.other_tenant_id)
        async with get_session_factory()() as session:
            await session.execute(
                text("DELETE FROM control.platform_user WHERE tenant_id = :tenant_id"),
                {"tenant_id": tenant.other_tenant_id},
            )
            await session.commit()


# --- 断言辅助 -------------------------------------------------------------------------


def _expires_at(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _body(session_token: str, **extra: str) -> dict[str, str]:
    return {"session_token": session_token, **extra}


def _headers(tenant_id: str, chat_context: uuid.UUID | str | None = None) -> dict[str, str]:
    headers = service_headers(tenant_id)
    if chat_context is not None:
        headers["X-Chat-Context"] = str(chat_context)
    return headers


def _error(response: HttpResponse, status_code: int, code: str) -> None:
    assert response.status_code == status_code, response.text
    assert response.json()["code"] == code
    assert "principal" not in response.text and "agents" not in response.text


async def _gate(
    client: AsyncClient,
    tenant: TenantContext,
    identity: ChatIdentity,
    *,
    tenant_id: str | None = None,
    chat_context: uuid.UUID | str | None = None,
    body: dict[str, str] | None = None,
) -> HttpResponse:
    context = identity.chat_context if chat_context is None else chat_context
    return await client.post(
        URL,
        json=_body(identity.session_token) if body is None else body,
        headers=_headers(tenant_id or tenant.tenant_id, context),
    )


async def _set_source_token_revoked(tenant_id: str, *, revoked: bool) -> None:
    async with get_session_factory()() as session:
        await session.execute(
            update(ChatAccessToken)
            .where(ChatAccessToken.tenant_id == tenant_id)
            .values(revoked_at=sa.text("now()") if revoked else None)
        )
        await session.commit()


async def _set_user_status(tenant_id: str, status: str) -> None:
    async with get_session_factory()() as session:
        await session.execute(
            update(PlatformUser)
            .where(PlatformUser.tenant_id == tenant_id)
            .values(status=status)
        )
        await session.commit()


async def _shift_source_token(tenant_id: str, *, expired: bool) -> None:
    issued_at = sa.text("now() - interval '2 days'" if expired else "now() - interval '1 minute'")
    expires_at = sa.text("now() - interval '1 day'" if expired else "now() + interval '29 days'")
    async with get_session_factory()() as session:
        await session.execute(
            update(ChatAccessToken)
            .where(ChatAccessToken.tenant_id == tenant_id)
            .values(issued_at=issued_at, expires_at=expires_at)
        )
        await session.commit()


async def _shift_session_window(session_id: uuid.UUID, *, expired: bool) -> None:
    issued_at = sa.text("now() - interval '2 hours'" if expired else "now() - interval '1 minute'")
    expires_at = sa.text("now() - interval '1 second'" if expired else "now() + interval '11 hours'")
    async with get_session_factory()() as session:
        await session.execute(
            update(ChatSession)
            .where(ChatSession.id == session_id)
            .values(issued_at=issued_at, expires_at=expires_at)
        )
        await session.commit()


async def _session_row(session_id: uuid.UUID) -> ChatSession | None:
    async with get_session_factory()() as session:
        return await session.scalar(select(ChatSession).where(ChatSession.id == session_id))


# --- B-243：主体与授权集合只从 PG 记录导出 ----------------------------------------------


async def test_b243_gate_derives_principal_and_authorized_agents_from_pg(
    client: AsyncClient, tenant: TenantContext, chat_identity: ChatIdentity
) -> None:
    """[B-243] 门控核验 CHAT/来源 Token/用户后，主体与授权集合来自 PG 真实行。"""
    response = await _gate(client, tenant, chat_identity)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert set(data) == {"principal", "expires_at", "agents"}
    assert data["principal"] == {
        "tenant_id": tenant.tenant_id,
        "platform_user_id": str(tenant.actor_user_id),
        "session_id": str(chat_identity.session_id),
        "auth_method": "USER_TOKEN",
    }
    assert set(data["principal"]) == PRINCIPAL_FIELDS
    assert CONSOLE_ROLE_FIELDS & set(data["principal"]) == set()
    row = await _session_row(chat_identity.session_id)
    assert row is not None and _expires_at(data["expires_at"]) == row.expires_at

    # 授权集合口径 = Console 侧 has_active_grant：真实 grant + 未软删 + Agent enabled/未软删。
    # 模型状态不在此门控（模型在定义解析处单独判定）。
    authorized = {item["agent_id"] for item in data["agents"]}
    assert authorized == {
        str(tenant.agent_id),
        str(tenant.deleted_model_agent_id),
        str(tenant.disabled_model_agent_id),
    }
    assert {
        str(tenant.disabled_agent_id),
        str(tenant.deleted_agent_id),
        str(tenant.ungranted_agent_id),
        str(tenant.revoked_agent_id),
    } & authorized == set()
    assert all(set(item) == AGENT_FIELDS for item in data["agents"])
    granted = next(item for item in data["agents"] if item["agent_id"] == str(tenant.agent_id))
    assert (granted["key"], granted["name"]) == (tenant.agent_key, "Granted Agent")

    # 凭据卫生：会话原文与摘要都不进响应体/非 Cookie 响应头。
    assert chat_identity.session_token not in response.text
    assert hash_chat_token(chat_identity.session_token) not in response.text

    # CHAT 身份不授予 Console 权限：同一凭据不能充当管理会话。
    console = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {chat_identity.session_token}"},
    )
    assert console.status_code == 401
    assert chat_identity.session_token not in console.text


async def test_b243_forged_headers_and_self_reported_identity_cannot_authenticate(
    client: AsyncClient, tenant: TenantContext, chat_identity: ChatIdentity
) -> None:
    """[B-243] 自报 user_id/tenant 与伪造头都不是证明；服务身份是硬门控。"""
    for field in ("user_id", "platform_user_id", "tenant_id", "agent_id", "context"):
        forged = await _gate(
            client,
            tenant,
            chat_identity,
            body=_body(chat_identity.session_token, **{field: str(uuid.uuid4())}),
        )
        _error(forged, 422, "COMMON_VALIDATION_ERROR")
        assert chat_identity.session_token not in forged.text

    # 伪造 X-User-Id 头不改变主体：主体只来自 PG 会话/Token/用户记录。
    spoofed = await client.post(
        URL,
        json=_body(chat_identity.session_token),
        headers={
            **_headers(tenant.tenant_id, chat_identity.chat_context),
            "X-User-Id": str(tenant.other_actor_user_id),
            "X-Platform-User-Id": str(tenant.other_actor_user_id),
        },
    )
    assert spoofed.status_code == 200, spoofed.text
    assert spoofed.json()["data"]["principal"]["platform_user_id"] == str(tenant.actor_user_id)

    # 服务身份缺失或错误：两条分支都拒绝，且不返回主体/授权集合。
    for internal_service in (None, "not-the-token"):
        headers = _headers(tenant.tenant_id, chat_identity.chat_context)
        if internal_service is None:
            headers.pop("X-Internal-Service")
        else:
            headers["X-Internal-Service"] = internal_service
        denied = await client.post(URL, json=_body(chat_identity.session_token), headers=headers)
        _error(denied, 403, "FORBIDDEN")
        assert chat_identity.session_token not in denied.text


async def test_b243_chat_session_source_token_and_user_must_be_live(
    client: AsyncClient, tenant: TenantContext, chat_identity: ChatIdentity
) -> None:
    """[B-243] 会话、来源 Token 与用户任一失效都即时拒绝；恢复后同一凭据再次可用。"""
    unknown = await client.post(
        URL,
        json=_body("unknown-session-token-canary"),
        headers=_headers(tenant.tenant_id, uuid.uuid4()),
    )
    _error(unknown, 401, "UNAUTHORIZED")
    assert "unknown-session-token-canary" not in unknown.text

    await _set_source_token_revoked(tenant.tenant_id, revoked=True)
    _error(await _gate(client, tenant, chat_identity), 401, "CHAT_SESSION_REVOKED")
    await _set_source_token_revoked(tenant.tenant_id, revoked=False)
    assert (await _gate(client, tenant, chat_identity)).status_code == 200

    await _set_user_status(tenant.tenant_id, "DISABLED")
    _error(await _gate(client, tenant, chat_identity), 401, "CHAT_SESSION_REVOKED")
    await _set_user_status(tenant.tenant_id, "ACTIVE")
    assert (await _gate(client, tenant, chat_identity)).status_code == 200

    await _shift_source_token(tenant.tenant_id, expired=True)
    _error(await _gate(client, tenant, chat_identity), 401, "CHAT_TOKEN_EXPIRED")
    await _shift_source_token(tenant.tenant_id, expired=False)
    assert (await _gate(client, tenant, chat_identity)).status_code == 200

    await _shift_session_window(chat_identity.session_id, expired=True)
    _error(await _gate(client, tenant, chat_identity), 401, "CHAT_SESSION_EXPIRED")
    await _shift_session_window(chat_identity.session_id, expired=False)
    assert (await _gate(client, tenant, chat_identity)).status_code == 200


async def test_b243_deployment_tenant_and_chat_context_are_enforced(
    client: AsyncClient,
    tenant: TenantContext,
    chat_identity: ChatIdentity,
    foreign_identity: ChatIdentity,
) -> None:
    """[B-243] 跨部署租户 403、X-Chat-Context 不匹配 409；两者都不返回任何主体数据。"""
    _error(
        await _gate(client, tenant, chat_identity, chat_context=uuid.uuid4()),
        409,
        "AUTH_CONTEXT_CHANGED",
    )
    for missing in (None, "not-a-uuid"):
        headers = _headers(tenant.tenant_id)
        if missing is not None:
            headers["X-Chat-Context"] = missing
        malformed = await client.post(
            URL, json=_body(chat_identity.session_token), headers=headers
        )
        _error(malformed, 422, "COMMON_VALIDATION_ERROR")

    # 本栈租户里的调用者不得用别的部署租户会话换主体，也不得把自己的会话报成别的租户。
    foreign = await _gate(client, tenant, foreign_identity)
    _error(foreign, 403, "FORBIDDEN")
    assert foreign_identity.session_token not in foreign.text
    misreported = await _gate(
        client, tenant, chat_identity, tenant_id=tenant.other_tenant_id
    )
    _error(misreported, 403, "FORBIDDEN")

    # 各自租户内仍然有效：403 来自租户不匹配，而不是会话本身不可用。
    own = await client.post(
        URL,
        json=_body(foreign_identity.session_token),
        headers=_headers(tenant.other_tenant_id, foreign_identity.chat_context),
    )
    assert own.status_code == 200, own.text
    assert own.json()["data"]["principal"]["tenant_id"] == tenant.other_tenant_id


# --- B-243：三层能力授权不变 -----------------------------------------------------------


@dataclass(frozen=True)
class CapabilityGraph:
    effective_skill_ids: frozenset[uuid.UUID]
    effective_mcp_ids: frozenset[uuid.UUID]
    selected_skill_id: uuid.UUID
    all_scope_skill_id: uuid.UUID


@pytest.fixture
async def capability(tenant: TenantContext) -> AsyncIterator[CapabilityGraph]:
    """给已授权 Agent 挂上真实的 Skill/MCP 绑定与用户级叠加授权（三层关系）。"""
    suffix = uuid.uuid4().hex[:8]
    factory = get_session_factory()
    skill_ids: dict[str, uuid.UUID] = {}
    binding_ids: list[uuid.UUID] = []
    grant_ids: list[uuid.UUID] = []
    mcp_ids: list[uuid.UUID] = []
    mcp_binding_ids: list[uuid.UUID] = []
    mcp_grant_ids: list[uuid.UUID] = []
    async with factory() as session:
        for order, (label, scope, enabled, binding_deleted) in enumerate(
            (
                ("selected-granted", "SELECTED", True, False),
                ("selected-ungranted", "SELECTED", True, False),
                ("all-scope", "ALL", True, False),
                ("all-disabled", "ALL", False, False),
                ("all-unbound", "ALL", True, True),
            )
        ):
            skill = Skill(
                tenant_id=tenant.tenant_id,
                key=f"skill-{label}-{suffix}",
                name=f"Skill {label}",
                description="B-243 capability fixture",
                user_scope=scope,
                enabled=enabled,
            )
            session.add(skill)
            await session.flush()
            artifact = SkillArtifact(
                skill_id=skill.id,
                version="1.0.0",
                checksum="sha256:" + hashlib.sha256(f"{skill.key}-v1".encode()).hexdigest(),
                storage_key=f"skills/{skill.key}.zip",
                package_size=1024,
                validation_status="VALIDATED",
                created_by=tenant.other_actor_user_id,
            )
            session.add(artifact)
            await session.flush()
            skill.current_artifact_id = artifact.id
            binding = AgentSkillBinding(
                agent_id=tenant.agent_id,
                skill_id=skill.id,
                sort_order=order,
                is_deleted=binding_deleted,
            )
            session.add(binding)
            await session.flush()
            skill_ids[label] = skill.id
            binding_ids.append(binding.id)
        grant = SkillUserGrant(
            skill_id=skill_ids["selected-granted"],
            user_id=tenant.actor_user_id,
            granted_by=tenant.other_actor_user_id,
        )
        session.add(grant)
        await session.flush()
        grant_ids.append(grant.id)
        for label, scope, enabled, binding_deleted in (
            ("all-scope", "ALL", True, False),
            ("selected-granted", "SELECTED", True, False),
            ("selected-ungranted", "SELECTED", True, False),
            ("all-disabled", "ALL", False, False),
            ("all-unbound", "ALL", True, True),
        ):
            server = McpServer(
                tenant_id=tenant.tenant_id,
                key=f"mcp-{label}-{suffix}",
                name=f"MCP {label}",
                endpoint="http://127.0.0.1:9/mcp",
                user_scope=scope,
                enabled=enabled,
            )
            session.add(server)
            await session.flush()
            binding = AgentMcpBinding(
                agent_id=tenant.agent_id, mcp_server_id=server.id, is_deleted=binding_deleted
            )
            session.add(binding)
            await session.flush()
            mcp_ids.append(server.id)
            mcp_binding_ids.append(binding.id)
        mcp_grant = McpUserGrant(
            mcp_server_id=mcp_ids[1], user_id=tenant.actor_user_id, granted_by=tenant.other_actor_user_id
        )
        session.add(mcp_grant)
        await session.flush()
        mcp_grant_ids.append(mcp_grant.id)
        await session.commit()
        graph = CapabilityGraph(
            effective_skill_ids=frozenset(
                {skill_ids["selected-granted"], skill_ids["all-scope"]}
            ),
            effective_mcp_ids=frozenset({mcp_ids[0], mcp_ids[1]}),
            selected_skill_id=skill_ids["selected-granted"],
            all_scope_skill_id=skill_ids["all-scope"],
        )
    try:
        yield graph
    finally:
        async with factory() as session:
            for statement in (
                sa.delete(SkillUserGrant).where(SkillUserGrant.id.in_(grant_ids)),
                sa.delete(AgentSkillBinding).where(AgentSkillBinding.id.in_(binding_ids)),
                sa.delete(SkillArtifact).where(
                    SkillArtifact.skill_id.in_(list(skill_ids.values()))
                ),
                sa.delete(Skill).where(Skill.id.in_(list(skill_ids.values()))),
                sa.delete(McpUserGrant).where(McpUserGrant.id.in_(mcp_grant_ids)),
                sa.delete(AgentMcpBinding).where(AgentMcpBinding.id.in_(mcp_binding_ids)),
                sa.delete(McpServer).where(McpServer.id.in_(mcp_ids)),
            ):
                await session.execute(statement)
            await session.commit()


async def test_b243_authorization_chain_keeps_the_three_layer_capability_model(
    client: AsyncClient, tenant: TenantContext, chat_identity: ChatIdentity, capability: CapabilityGraph
) -> None:
    """[B-243] 门控主体进入既有解析链路后，User→Agent 与 Skill/MCP 叠加授权语义不变。"""
    gated = await _gate(client, tenant, chat_identity)
    assert gated.status_code == 200, gated.text
    data = gated.json()["data"]
    actor = data["principal"]["platform_user_id"]
    assert str(tenant.agent_id) in {item["agent_id"] for item in data["agents"]}

    resolved = await client.post(
        RESOLVE_URL,
        json={"agent_id": str(tenant.agent_id), "actor_user_id": actor},
        headers=service_headers(tenant.tenant_id),
    )
    assert resolved.status_code == 200, resolved.text
    payload = resolved.json()["data"]
    assert {item["skill_id"] for item in payload["skills"]} == {
        str(value) for value in capability.effective_skill_ids
    }
    assert {item["mcp_server_id"] for item in payload["mcp_servers"]} == {
        str(value) for value in capability.effective_mcp_ids
    }

    # 叠加授权是**按用户实时**裁决：撤销 SkillUserGrant 后同一主体立即失去该 SELECTED 技能。
    async with get_session_factory()() as session:
        await session.execute(
            update(SkillUserGrant)
            .where(
                SkillUserGrant.skill_id == capability.selected_skill_id,
                SkillUserGrant.user_id == tenant.actor_user_id,
            )
            .values(is_deleted=True)
        )
        await session.commit()
    again = await client.post(
        RESOLVE_URL,
        json={"agent_id": str(tenant.agent_id), "actor_user_id": actor},
        headers=service_headers(tenant.tenant_id),
    )
    assert again.status_code == 200, again.text
    remaining = {item["skill_id"] for item in again.json()["data"]["skills"]}
    assert remaining == {str(capability.all_scope_skill_id)}
