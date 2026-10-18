"""受信聊天身份核验与授权解析（design §3.4 API-12；TASK-043）。

语义要点：

- **门控**：内部端点先过服务身份（`X-Internal-Service`）与部署租户（`X-Tenant-Id`）；CHAT 会话
  原文只作**输入凭据**（`SecretStr`，不打印、不回显、不落审计），主体只从 PG 记录导出：
  `ChatSessionService.resolve` 实时复核会话/来源 Token/用户（未撤销、未过期、用户 `ACTIVE`），
  本层再核对**部署租户**与 **X-Chat-Context**（`info.auth_context_id`）。
- **授权解析**：只返回该用户**当前**可访问的 Agent，口径与 Console 侧 `has_active_grant` 一致
  （`agent_access_grant.is_deleted=false` + Agent `is_deleted=false` + Agent `enabled=true` +
  同租户）。未授权/禁用/软删资源既不返回也不泄露存在性；模型状态不在此层，由定义解析单独判定。
  三层的 Skill/MCP 叠加授权不在此重写：Runtime 走既有 `resolve-definition`（同一公式）。
- **不授予 Console 权限**：响应只有统一 principal（design §3.4：`{tenant_id,platform_user_id,
  session_id,auth_method}`）与授权集合，没有任何角色/权限/Console 账号或会话字段。
- **错误映射**（目录已由 TASK-007 登记，三处一致）：未知会话 `UNAUTHORIZED`(401)；会话/来源
  Token/用户失效沿用 TASK-040 层（`CHAT_SESSION_REVOKED`/`CHAT_SESSION_EXPIRED`/
  `CHAT_TOKEN_EXPIRED`，均 401）；跨部署租户 `FORBIDDEN`(403)；X-Chat-Context 不匹配
  `AUTH_CONTEXT_CHANGED`(409)。
"""

from __future__ import annotations

import uuid

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts.webchat import (
    ChatAuthorizedAgent,
    ChatPrincipal,
    ChatPrincipalRequest,
    ChatPrincipalResponse,
)
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.repositories.agent_access_grant_repository import (
    AgentAccessGrantRepository,
)
from .chat_auth_service import ChatSessionService


class ChannelIdentityService:
    """内部聊天身份门控：CHAT 会话 → 统一 principal + 当前可访问 Agent 集合。"""

    def __init__(self, session: AsyncSession) -> None:
        self._sessions = ChatSessionService(session)
        self._grants = AgentAccessGrantRepository(session)

    async def resolve_chat_principal(
        self,
        tenant_id: str,
        payload: ChatPrincipalRequest,
        *,
        chat_context: uuid.UUID,
    ) -> ChatPrincipalResponse:
        """核验会话/来源 Token/用户/部署租户与上下文，再从 PG 行导出主体与授权集合。"""
        info = await self._sessions.resolve(payload.session_token)
        if info.tenant_id != tenant_id:
            raise AppError(ErrorCode.FORBIDDEN)
        if chat_context != info.auth_context_id:
            raise AppError(ErrorCode.AUTH_CONTEXT_CHANGED)
        rows = await self._grants.list_authorized_agents(tenant_id, info.user_id)
        return ChatPrincipalResponse(
            principal=ChatPrincipal(
                tenant_id=info.tenant_id,
                platform_user_id=info.user_id,
                session_id=info.session_id,
                auth_method=info.auth_method,
            ),
            expires_at=info.expires_at,
            agents=[
                ChatAuthorizedAgent(agent_id=agent_id, key=key, name=name)
                for agent_id, key, name in rows
            ],
        )
