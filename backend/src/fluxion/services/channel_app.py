from __future__ import annotations

import hashlib
import secrets
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import uuid4

from fluxion.protocols.channel import (
    ChannelAdapter,
    ChannelMessage,
    ChannelResult,
    ExternalChannelMessage,
)
from fluxion.registry import (
    AuditRecord,
    BindCodeRecord,
    BindCodeRejected,
    BindRedemption,
    ChannelIdentityRecord,
    ChannelRegistryStore,
    ChatAccessRecord,
    PlatformUserRecord,
)
from fluxion.services.runtime_app import (
    RunRuntimeRequest,
    RunRuntimeResult,
    RuntimeStreamEvent,
)


class RuntimeGateway(Protocol):
    async def run(self, request: RunRuntimeRequest) -> RunRuntimeResult: ...

    def stream(self, request: RunRuntimeRequest) -> AsyncIterator[RuntimeStreamEvent]: ...


class ChannelBindError(RuntimeError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__("绑定码无效或已过期")


class ChannelAccessError(RuntimeError):
    code = "chat_access_denied"

    def __init__(self) -> None:
        super().__init__("Chat 访问链接无效或已撤销")


@dataclass(frozen=True, slots=True)
class IssuedBindCode:
    code: str
    expires_at: datetime


class ChannelApplicationService:
    def __init__(
        self,
        store: ChannelRegistryStore,
        runtime: RuntimeGateway,
        *,
        code_factory: Callable[[], str] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._runtime = runtime
        self._code_factory = code_factory or (lambda: secrets.token_urlsafe(18))
        self._clock = clock or (lambda: datetime.now(UTC))

    async def create_platform_user(
        self, tenant_id: str, platform_user_id: str, *, display_name: str = ""
    ) -> PlatformUserRecord:
        now = self._clock()
        record = PlatformUserRecord(
            tenant_id=tenant_id,
            platform_user_id=platform_user_id,
            display_name=display_name or platform_user_id,
            created_at=now,
        )
        return await self._store.create_platform_user(record)

    async def issue_bind_code(
        self,
        tenant_id: str,
        platform_user_id: str,
        *,
        expires_at: datetime | None = None,
    ) -> IssuedBindCode:
        code = self._code_factory()
        now = self._clock()
        expiry = expires_at or now + timedelta(minutes=10)
        await self._store.create_bind_code(
            BindCodeRecord(
                bind_code_id=f"bind_code_{uuid4().hex}",
                tenant_id=tenant_id,
                platform_user_id=platform_user_id,
                code_hash=_hash_code(code),
                expires_at=expiry,
                created_at=now,
            )
        )
        return IssuedBindCode(code=code, expires_at=expiry)

    async def resolve_identity(
        self, tenant_id: str, channel_type: str, channel_user_id: str
    ) -> ChannelIdentityRecord | None:
        return await self._store.resolve_channel_identity(
            tenant_id=tenant_id,
            channel_type=channel_type,
            channel_user_id=channel_user_id,
        )

    async def resolve_chat_access(self, token: str) -> ChatAccessRecord:
        if not token.strip():
            raise ChannelAccessError()
        record = await self._store.resolve_chat_access(token_hash=_hash_access_token(token))
        if record is None:
            raise ChannelAccessError()
        return record

    async def handle_chat_access(
        self,
        token: str,
        *,
        conversation_id: str,
        content: str,
        request_id: str,
        trace_id: str,
    ) -> ChannelResult:
        access = await self.resolve_chat_access(token)
        runtime_result = await self._runtime.run(
            RunRuntimeRequest(
                tenant_id=access.tenant_id,
                user_id=access.platform_user_id,
                runtime_profile_id=await self._profile_id_for(access.tenant_id, access.agent_id),
                agent_definition_id=access.agent_id,
                session_id=conversation_id,
                input_message=content,
                request_id=request_id,
                trace_id=trace_id,
            )
        )
        return ChannelResult(
            kind="message",
            output=runtime_result.output,
            platform_user_id=access.platform_user_id,
            request_id=runtime_result.request_id,
            trace_id=runtime_result.trace_id,
            execution_id=runtime_result.execution_id,
        )

    async def stream_chat_access(
        self,
        token: str,
        *,
        conversation_id: str,
        content: str,
        request_id: str,
        trace_id: str,
    ) -> AsyncIterator[RuntimeStreamEvent]:
        """流式转发 Runtime 的 started/token 事件，completed 包装为 ChannelResult 结构。"""
        access = await self.resolve_chat_access(token)
        request = RunRuntimeRequest(
            tenant_id=access.tenant_id,
            user_id=access.platform_user_id,
            runtime_profile_id=await self._profile_id_for(access.tenant_id, access.agent_id),
            agent_definition_id=access.agent_id,
            session_id=conversation_id,
            input_message=content,
            request_id=request_id,
            trace_id=trace_id,
        )
        async for event in self._runtime.stream(request):
            if event.event == "completed":
                yield RuntimeStreamEvent(
                    event="completed",
                    data={
                        "kind": "message",
                        "output": event.data.get("output"),
                        "platform_user_id": access.platform_user_id,
                        "request_id": event.data.get("request_id"),
                        "trace_id": event.data.get("trace_id"),
                        "execution_id": event.data.get("execution_id"),
                    },
                )
            else:
                yield event

    async def handle(
        self, adapter: ChannelAdapter, external: ExternalChannelMessage
    ) -> ChannelResult:
        message = adapter.normalize_inbound(external)
        identity = await self.resolve_identity(
            message.tenant_id, message.channel_type, message.channel_user_id
        )
        if identity is None:
            result = await self._handle_unbound(message)
        else:
            result = await self._run_bound(message, identity.platform_user_id)
        await adapter.push_outbound(result)
        return result

    async def _handle_unbound(self, message: ChannelMessage) -> ChannelResult:
        code = _bind_command_code(message.content)
        if code is None:
            return ChannelResult(
                kind="unbound",
                output="请先使用 /bind <code> 完成绑定",
                request_id=message.request_id,
                trace_id=message.trace_id,
            )
        try:
            identity = await self._store.redeem_bind_code(
                BindRedemption(
                    tenant_id=message.tenant_id,
                    channel_type=message.channel_type,
                    channel_user_id=message.channel_user_id,
                    code_hash=_hash_code(code),
                    request_id=message.request_id,
                    audit_id=f"audit_{uuid4().hex}",
                    now=self._clock(),
                )
            )
        except BindCodeRejected as exc:
            raise ChannelBindError(exc.reason) from exc
        return ChannelResult(
            kind="bound",
            output="身份绑定成功",
            platform_user_id=identity.platform_user_id,
            request_id=message.request_id,
            trace_id=message.trace_id,
        )

    async def _run_bound(self, message: ChannelMessage, platform_user_id: str) -> ChannelResult:
        runtime_result = await self._runtime.run(
            RunRuntimeRequest(
                tenant_id=message.tenant_id,
                user_id=platform_user_id,
                runtime_profile_id=await self._profile_id_for(message.tenant_id, message.agent_id),
                agent_definition_id=message.agent_id,
                session_id=message.conversation_id,
                input_message=message.content,
                request_id=message.request_id,
                trace_id=message.trace_id,
            )
        )
        return ChannelResult(
            kind="message",
            output=runtime_result.output,
            platform_user_id=platform_user_id,
            request_id=runtime_result.request_id,
            trace_id=runtime_result.trace_id,
            execution_id=runtime_result.execution_id,
        )

    async def _profile_id_for(self, tenant_id: str, agent_id: str) -> str:
        """TASK-A104/A105：执行仍需 mechanics profile 键，来源为 Agent 的
        runtime_profile_ref；缺省同名回退（迁移产物与 fixture 同名约定）。"""
        from fluxion.agents.definitions import AgentDefinition
        from fluxion.resources import ResourceKind

        row = await self._store.get(
            ResourceKind.AGENT_DEFINITION, agent_id, tenant_id=tenant_id
        )
        if row is None:
            return agent_id
        spec = AgentDefinition.model_validate(row.spec_json)
        return spec.runtime_profile_ref.id if spec.runtime_profile_ref else agent_id



    async def audit_auth_failure(
        self,
        *,
        tenant_id: str,
        method: str,
        reason: str,
        request_id: str = "",
        trace_id: str = "",
    ) -> None:
        """closure TASK-005：验证失败进 AuditLog；token/签名不入审计载荷。"""
        del trace_id  # 审计按 request 关联；token/签名/trace 不落敏感载荷
        await self._store.append_audit(
            AuditRecord(
                audit_id=f"audit_{uuid4().hex}",
                tenant_id=tenant_id,
                actor_id="unknown",
                request_id=request_id,
                action="channel.auth.rejected",
                target_type="channel_identity",
                target_id=method,
                before=None,
                after={"method": method, "reason": reason},
                created_at=self._clock(),
            )
        )

def _hash_code(code: str) -> str:
    return hashlib.sha256(code.strip().encode("utf-8")).hexdigest()


def _hash_access_token(token: str) -> str:
    return hashlib.sha256(token.strip().encode("utf-8")).hexdigest()


def is_bind_command(content: str) -> bool:
    """closure TASK-005：匿名通道仅放行 /bind 命令（H1 语义保留）。"""
    return _bind_command_code(content) is not None


def _bind_command_code(content: str) -> str | None:
    command, separator, code = content.strip().partition(" ")
    if command != "/bind" or not separator or not code.strip():
        return None
    return code.strip()
