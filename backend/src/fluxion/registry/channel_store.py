from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

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
    runtime_profile_id: str
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
        self, *, tenant_id: str, offset: int, limit: int
    ) -> tuple[list[PlatformUserRecord], int]: ...

    async def create_chat_access(self, record: ChatAccessRecord) -> ChatAccessRecord: ...

    async def resolve_chat_access(self, *, token_hash: str) -> ChatAccessRecord | None: ...

    async def revoke_chat_access(
        self, *, tenant_id: str, access_id: str, revoked_at: datetime
    ) -> ChatAccessRecord: ...

    async def create_bind_code(self, record: BindCodeRecord) -> BindCodeRecord: ...

    async def resolve_channel_identity(
        self, *, tenant_id: str, channel_type: str, channel_user_id: str
    ) -> ChannelIdentityRecord | None: ...

    async def redeem_bind_code(self, redemption: BindRedemption) -> ChannelIdentityRecord: ...


@runtime_checkable
class ChannelRegistryStore(RegistryStore, ChannelStore, UserDomainStore, Protocol):
    pass
