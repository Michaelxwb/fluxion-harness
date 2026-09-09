from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from fluxion.registry.execution_control import ExecutionRecord
from fluxion.registry.store import RegistryStore
from fluxion.registry.user_store import UserDomainStore


class BindCodeRejected(RuntimeError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"bind code rejected: {reason}")


@dataclass(frozen=True, slots=True)
class PlatformUserRecord:
    tenant_id: str
    platform_user_id: str
    display_name: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ChatAccessRecord:
    access_id: str
    tenant_id: str
    platform_user_id: str
    agent_id: str
    token_hash: str
    created_at: datetime
    revoked_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class BindCodeRecord:
    bind_code_id: str
    tenant_id: str
    platform_user_id: str
    code_hash: str
    expires_at: datetime
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ChannelIdentityRecord:
    tenant_id: str
    channel_type: str
    channel_user_id: str
    platform_user_id: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ChatSessionHead:
    """外部会话 → Runtime 逻辑 Session 映射头（ADR-A017 §5）。"""

    tenant_id: str
    channel_type: str
    external_conversation_id: str
    platform_user_id: str
    agent_id: str
    active_session_id: str
    revision: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class BindRedemption:
    tenant_id: str
    channel_type: str
    channel_user_id: str
    code_hash: str
    request_id: str
    audit_id: str
    now: datetime


@runtime_checkable
class ChannelStore(Protocol):
    async def create_platform_user(self, record: PlatformUserRecord) -> PlatformUserRecord: ...

    async def get_platform_user(
        self, *, tenant_id: str, platform_user_id: str
    ) -> PlatformUserRecord | None: ...

    async def list_platform_users(
        self, *, tenant_id: str, offset: int, limit: int, keyword: str | None = None
    ) -> tuple[list[PlatformUserRecord], int]: ...

    async def create_chat_access(self, record: ChatAccessRecord) -> ChatAccessRecord: ...

    async def resolve_chat_access(self, *, token_hash: str) -> ChatAccessRecord | None: ...

    async def list_chat_access(
        self,
        *,
        tenant_id: str,
        platform_user_id: str | None = None,
        agent_id: str | None = None,
    ) -> list[ChatAccessRecord]: ...

    async def revoke_chat_access(
        self, *, tenant_id: str, access_id: str, revoked_at: datetime
    ) -> ChatAccessRecord: ...

    async def create_bind_code(self, record: BindCodeRecord) -> BindCodeRecord: ...

    async def resolve_channel_identity(
        self, *, tenant_id: str, channel_type: str, channel_user_id: str
    ) -> ChannelIdentityRecord | None: ...

    async def resolve_platform_user_by_channel_id(
        self, *, tenant_id: str, channel_user_id: str
    ) -> str | None: ...

    async def redeem_bind_code(self, redemption: BindRedemption) -> ChannelIdentityRecord: ...

    async def get_session_head(
        self,
        *,
        tenant_id: str,
        channel_type: str,
        external_conversation_id: str,
        platform_user_id: str,
        agent_id: str,
    ) -> ChatSessionHead | None: ...

    async def create_session_head(self, head: ChatSessionHead) -> ChatSessionHead: ...

    async def rotate_session_head(
        self, head: ChatSessionHead, *, expected_revision: int
    ) -> ChatSessionHead: ...

    async def get_execution(
        self, *, tenant_id: str, execution_id: str
    ) -> ExecutionRecord | None: ...

    async def get_active_execution_for_session(
        self,
        *,
        tenant_id: str,
        platform_user_id: str,
        agent_id: str,
        session_id: str,
    ) -> ExecutionRecord | None: ...

    async def create_execution(self, record: ExecutionRecord) -> ExecutionRecord: ...

    async def mark_execution_running(
        self, *, tenant_id: str, execution_id: str
    ) -> ExecutionRecord | None: ...

    async def request_execution_cancel(
        self,
        *,
        tenant_id: str,
        platform_user_id: str,
        agent_id: str,
        session_id: str,
        reason: str,
        now: datetime,
    ) -> ExecutionRecord | None: ...

    async def finish_execution(
        self,
        *,
        tenant_id: str,
        execution_id: str,
        state: str,
        now: datetime,
        error_code: str | None = None,
    ) -> ExecutionRecord | None: ...

    async def list_stale_cancelling(
        self, *, before: datetime, limit: int = 100
    ) -> list[ExecutionRecord]: ...


@runtime_checkable
class ChannelRegistryStore(RegistryStore, ChannelStore, UserDomainStore, Protocol):
    pass
