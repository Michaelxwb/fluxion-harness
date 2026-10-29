"""Gateway 的 Redis 会话门控：命中放行、miss/故障重新认证、上下文/租户不变。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_common.session_store import (
    SessionAgent,
    SessionRecord,
    SessionStoreUnavailable,
)
from muad_im_gateway.application.chat_principal import (
    ChatPrincipalGate,
    ChatSessionCredentials,
)
from pydantic import SecretStr


class _Store:
    def __init__(self, record: SessionRecord | None, *, touch: bool = True) -> None:
        self.record = record
        self.touch_result = touch
        self.touched = False
        self.fail = False

    async def read(self, token_hash: str) -> SessionRecord | None:
        if self.fail:
            raise SessionStoreUnavailable("down")
        return self.record

    async def touch(
        self, token_hash: str, record: SessionRecord, *, now: object = None
    ) -> bool:
        if self.fail:
            raise SessionStoreUnavailable("down")
        self.touched = True
        return self.touch_result


class _Console:
    async def chat_principal(self, *args, **kwargs):  # pragma: no cover - 不应被调用
        raise AssertionError("Redis 命中/miss 都不应回源 Console")


def _record(*, tenant_id: str = "t1", session_id: uuid.UUID | None = None) -> SessionRecord:
    return SessionRecord(
        tenant_id=tenant_id,
        platform_user_id=uuid.uuid4(),
        session_id=session_id or uuid.uuid4(),
        auth_method="USER_TOKEN",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        idle_ttl_sec=600,
        agents=(SessionAgent(agent_id=uuid.uuid4(), key="a", name="A"),),
    )


def _credentials(session_id: uuid.UUID) -> ChatSessionCredentials:
    return ChatSessionCredentials(
        session_token=SecretStr("token"), chat_context=session_id
    )


async def test_hit_returns_identity_and_slides() -> None:
    record = _record()
    store = _Store(record)
    gate = ChatPrincipalGate(_Console(), tenant_id="t1", session_store=store)  # type: ignore[arg-type]
    identity = await gate.resolve(_credentials(record.session_id))
    assert identity.principal.platform_user_id == record.platform_user_id
    assert identity.agents[0].key == "a"
    assert store.touched is True


async def test_miss_requires_reauthentication() -> None:
    gate = ChatPrincipalGate(_Console(), tenant_id="t1", session_store=_Store(None))  # type: ignore[arg-type]
    with pytest.raises(AppError) as error:
        await gate.resolve(_credentials(uuid.uuid4()))
    assert error.value.code == ErrorCode.UNAUTHORIZED


async def test_tenant_mismatch_is_forbidden() -> None:
    record = _record(tenant_id="t2")
    gate = ChatPrincipalGate(_Console(), tenant_id="t1", session_store=_Store(record))  # type: ignore[arg-type]
    with pytest.raises(AppError) as error:
        await gate.resolve(_credentials(record.session_id))
    assert error.value.code == ErrorCode.FORBIDDEN


async def test_context_mismatch_changes_auth_context() -> None:
    record = _record()
    gate = ChatPrincipalGate(_Console(), tenant_id="t1", session_store=_Store(record))  # type: ignore[arg-type]
    with pytest.raises(AppError) as error:
        await gate.resolve(_credentials(uuid.uuid4()))
    assert error.value.code == ErrorCode.AUTH_CONTEXT_CHANGED


async def test_dead_touch_is_session_expired() -> None:
    record = _record()
    gate = ChatPrincipalGate(
        _Console(), tenant_id="t1", session_store=_Store(record, touch=False)
    )  # type: ignore[arg-type]
    with pytest.raises(AppError) as error:
        await gate.resolve(_credentials(record.session_id))
    assert error.value.code == ErrorCode.CHAT_SESSION_EXPIRED


async def test_redis_failure_is_fail_closed() -> None:
    store = _Store(None)
    store.fail = True
    gate = ChatPrincipalGate(_Console(), tenant_id="t1", session_store=store)  # type: ignore[arg-type]
    with pytest.raises(AppError) as error:
        await gate.resolve(_credentials(uuid.uuid4()))
    assert error.value.code == ErrorCode.UNAUTHORIZED
