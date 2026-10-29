from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, Header, Request
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_im_core import InboundPublisher
from pydantic import SecretStr

from ..application.chat_principal import (
    CHAT_SESSION_COOKIE,
    ChatIdentity,
    ChatPrincipalGate,
    ChatSessionCredentials,
    ChatStreamGuard,
)
from ..application.console_client import CHAT_CONTEXT_HEADER, ConsoleClientPort
from ..channels.base import ChannelRegistry
from ..infrastructure.artifact_resolver import ArtifactResolverPort
from ..infrastructure.dedupe import DedupeStore
from ..infrastructure.streams import DeliveryStreams


def get_registry(request: Request) -> ChannelRegistry:
    registry: ChannelRegistry = request.app.state.registry
    return registry


def get_dedupe_store(request: Request) -> DedupeStore:
    store: DedupeStore = request.app.state.dedupe_store
    return store


def get_console_client(request: Request) -> ConsoleClientPort:
    client: ConsoleClientPort = request.app.state.console_client
    return client


def get_gateway_tenant(request: Request) -> str:
    """**本网关部署的租户**（单租户部署）：投递请求声明的租户必须与它一致。

    与入站用的 `DEFAULT_TENANT_ID` 是同一个值 —— 一个网关实例只服务一个租户的 bot，
    所以"另一个租户的投递"在这里没有任何合法含义。
    """
    tenant_id: str = request.app.state.tenant_id
    return tenant_id


def get_artifact_resolver(request: Request) -> ArtifactResolverPort:
    resolver: ArtifactResolverPort = request.app.state.artifact_resolver
    return resolver


ConsoleClientDep = Annotated[ConsoleClientPort, Depends(get_console_client)]
RegistryDep = Annotated[ChannelRegistry, Depends(get_registry)]
DedupeStoreDep = Annotated[DedupeStore, Depends(get_dedupe_store)]
GatewayTenantDep = Annotated[str, Depends(get_gateway_tenant)]
ArtifactResolverDep = Annotated[ArtifactResolverPort, Depends(get_artifact_resolver)]


def get_delivery_streams(request: Request) -> DeliveryStreams:
    """投递 facade 的出站命令 Stream（API-06）。"""
    streams: DeliveryStreams = request.app.state.delivery_streams
    return streams


def get_delivery_stream_wait_sec(request: Request) -> float:
    wait: float = request.app.state.delivery_stream_wait_sec
    return wait


def get_inbound_publisher(request: Request) -> InboundPublisher:
    """Webhook 入站持久化（先 XADD 再 ACK）。"""
    publisher: InboundPublisher = request.app.state.inbound_publisher
    return publisher


DeliveryStreamsDep = Annotated[DeliveryStreams, Depends(get_delivery_streams)]
DeliveryStreamWaitDep = Annotated[float, Depends(get_delivery_stream_wait_sec)]
InboundPublisherDep = Annotated[InboundPublisher, Depends(get_inbound_publisher)]

# ---------------------------------------------------------------------------
# CHAT 主体门控与长流复核（设计 §3.4 API-12/API-19；TASK-044）
#
# 读写、SSE 与附件下载共用同一条主体依赖：主体只经 Console 核验导出，跨部署租户与上下文
# 不匹配在**本层再拒一次**（Console 已判过，两层都不放行）。
# ---------------------------------------------------------------------------


def get_chat_principal_gate(
    request: Request, console: ConsoleClientDep, tenant: GatewayTenantDep
) -> ChatPrincipalGate:
    """CHAT 主体门控：Redis 会话存储 + Console 客户端 + 本部署租户（网关按单租户部署）。"""
    return ChatPrincipalGate(
        console,
        tenant_id=tenant,
        session_store=getattr(request.app.state, "session_store", None),
    )


def get_chat_auth_recheck_sec(request: Request) -> float:
    """长流复核周期（启动资源预算 `chat_auth_recheck_sec`，设计 §3.7）。"""
    recheck_sec: float = request.app.state.chat_auth_recheck_sec
    return recheck_sec


async def get_chat_credentials(
    request: Request,
    chat_context: Annotated[uuid.UUID, Header(alias=CHAT_CONTEXT_HEADER)],
) -> ChatSessionCredentials:
    """CHAT 请求凭据：Cookie 原文 + `X-Chat-Context`。

    缺失/空 Cookie 一律 401（没有凭据就没有主体）；上下文头缺失/非法由 FastAPI 校验拒为 422
    ——与 Console 内部端点同一口径（两处都不接受"没有上下文的聊天请求"）。
    """
    session_token = request.cookies.get(CHAT_SESSION_COOKIE, "")
    if not session_token.strip():
        raise AppError(ErrorCode.UNAUTHORIZED)
    return ChatSessionCredentials(
        session_token=SecretStr(session_token), chat_context=chat_context
    )


async def get_chat_identity(
    gate: ChatPrincipalGateDep, credentials: ChatCredentialsDep
) -> ChatIdentity:
    """受信 CHAT 主体：读写/SSE/下载共用（跨部署租户 403、上下文不匹配 409）。"""
    return await gate.resolve(credentials)


def get_chat_stream_guard(
    gate: ChatPrincipalGateDep,
    credentials: ChatCredentialsDep,
    identity: ChatIdentityDep,
    recheck_sec: ChatAuthRecheckDep,
) -> ChatStreamGuard:
    """长流复核器：SSE 路由用它包裹事件流（空闲也复核、到 `expires_at` 即停）。"""
    return ChatStreamGuard(gate, credentials, identity, recheck_sec=recheck_sec)


ChatPrincipalGateDep = Annotated[ChatPrincipalGate, Depends(get_chat_principal_gate)]
ChatAuthRecheckDep = Annotated[float, Depends(get_chat_auth_recheck_sec)]
ChatCredentialsDep = Annotated[ChatSessionCredentials, Depends(get_chat_credentials)]
ChatIdentityDep = Annotated[ChatIdentity, Depends(get_chat_identity)]
ChatStreamGuardDep = Annotated[ChatStreamGuard, Depends(get_chat_stream_guard)]
