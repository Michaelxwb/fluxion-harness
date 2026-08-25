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
                runtime_profile_id=access.runtime_profile_id,
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
            runtime_profile_id=access.runtime_profile_id,
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
                runtime_profile_id=message.runtime_profile_id,
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


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.strip().encode("utf-8")).hexdigest()


def _hash_access_token(token: str) -> str:
    return hashlib.sha256(token.strip().encode("utf-8")).hexdigest()


def _bind_command_code(content: str) -> str | None:
    command, separator, code = content.strip().partition(" ")
    if command != "/bind" or not separator or not code.strip():
        return None
    return code.strip()
