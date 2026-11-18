"""外部断言读路径缓存：凭证层命中跳过验签、权限层独立决定授权、故障回源。

设计约束（`backend.design.md` §3.1.2）在这里逐条钉住：

- 凭证键是断言原文的 SHA-256：断言任何改动（含一位）都会 miss → 回源完整验签；
- **命中凭证不等于放行**：授权只从权限层读，`allowed=false` 的结论同样生效；
- 权限层 miss（撤权/解绑已删键）→ 回源 Console，不沿用旧结论；
- Redis 故障 → 回源，不 fail closed（可用性优先）。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fakes import FakeConsoleClient
from muad_common.auth_cache import credential_key, permission_key
from muad_common.session_store import hash_session_token
from muad_contracts.webchat import (
    ChatPrincipal,
    ExternalIdentityGrant,
    ExternalPrincipalResult,
)
from muad_im_gateway.application.chat_principal import (
    ChatPrincipalGate,
    ChatSessionCredentials,
)
from pydantic import SecretStr

TENANT = "tenant-1"
PLATFORM = "mssw"
ASSERTION = "assertion-abc-123"


class FakeRedis:
    """覆盖精简读路径用到的命令；`fail=True` 模拟 Redis 不可用。"""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int] = {}
        self.sets: dict[str, set[str]] = {}
        self.fail = False

    def _guard(self) -> None:
        if self.fail:
            raise OSError("redis down")

    async def get(self, key: str) -> str | None:
        self._guard()
        return self.values.get(key)

    async def set(self, key: str, value: str, *, ex: int) -> None:  # noqa: A003
        self._guard()
        self.values[key] = value
        self.ttls[key] = ex

    async def expire(self, key: str, ttl: int) -> bool:
        self._guard()
        if key not in self.values and key not in self.sets:
            return False
        self.ttls[key] = ttl
        return True

    async def delete(self, *keys: str) -> None:
        self._guard()
        for key in keys:
            self.values.pop(key, None)
            self.ttls.pop(key, None)

    async def sadd(self, key: str, member: str) -> None:
        self._guard()
        self.sets.setdefault(key, set()).add(member)

    async def smembers(self, key: str) -> set[str]:
        self._guard()
        return set(self.sets.get(key, set()))


def _stores() -> tuple[FakeRedis, object]:
    from muad_common.auth_cache import AuthCacheStore

    client = FakeRedis()
    return client, AuthCacheStore(client, ttl_sec=3600)


def _identity(platform_user_id: uuid.UUID, expires_in: timedelta = timedelta(hours=1)):
    return ChatPrincipal(
        tenant_id=TENANT,
        platform_user_id=platform_user_id,
        session_id=uuid.uuid4(),
        auth_method="EXTERNAL_ASSERTION",
    )


def _allowed_result(
    platform_user_id: uuid.UUID, expires_in: timedelta = timedelta(hours=1)
) -> ExternalPrincipalResult:
    return ExternalPrincipalResult(
        allowed=True,
        principal=_identity(platform_user_id, expires_in),
        agents=[],
        grants=[
            ExternalIdentityGrant(
                entry_id=uuid.uuid4(),
                entry_name="entry-a",
                agent_id=uuid.uuid4(),
                channel="MSSWWEB",
                allowed=True,
            )
        ],
        expires_at=datetime.now(UTC) + expires_in,
    )


async def _prime(cache, result: ExternalPrincipalResult) -> None:
    """模拟 Console 写入凭证层 + 权限层（与 `resolve()` 的写入同形）。"""
    import json

    expires_at = result.expires_at
    assert expires_at is not None
    principal = result.principal
    assert principal is not None
    now = datetime.now(UTC)
    await cache.write_credential_plain(
        TENANT,
        PLATFORM,
        hash_session_token(ASSERTION),
        principal.platform_user_id,
        json.dumps(
            {
                "platform_user_id": str(principal.platform_user_id),
                "session_id": str(principal.session_id),
                "expires_at_epoch": expires_at.timestamp(),
            },
            separators=(",", ":"),
            sort_keys=True,
        ),
        expires_at_epoch=expires_at.timestamp(),
        now_epoch=now.timestamp(),
    )
    await cache.write_permission_plain(
        TENANT,
        principal.platform_user_id,
        json.dumps(
            {
                "allowed": result.allowed,
                "reason": result.reason,
                "agents": [a.model_dump(mode="json") for a in result.agents],
                "grants": [g.model_dump(mode="json") for g in result.grants],
                "expires_at": expires_at.isoformat(),
            },
            separators=(",", ":"),
            sort_keys=True,
        ),
    )


async def test_cache_hit_skips_console() -> None:
    _, cache = _stores()
    console = FakeConsoleClient()  # 未配 external_principal_response，一旦调用就失败
    gate = ChatPrincipalGate(console, tenant_id=TENANT, auth_cache=cache)
    uid = uuid.uuid4()
    result = _allowed_result(uid)
    await _prime(cache, result)

    identity = await gate.resolve_credentials(
        ChatSessionCredentials(assertion=SecretStr(ASSERTION)),
        platform_key=PLATFORM,
        channel="MSSWWEB",
    )
    assert identity.principal.platform_user_id == uid
    assert console.external_principal_calls == []  # 一次 Console 都没调


async def test_tampered_assertion_misses_and_reaches_console() -> None:
    _, cache = _stores()
    console = FakeConsoleClient()
    gate = ChatPrincipalGate(console, tenant_id=TENANT, auth_cache=cache)
    uid = uuid.uuid4()
    await _prime(cache, _allowed_result(uid))

    # 改断言一位 → 哈希不同 → 凭证 miss → 回源（Console 被调用并重新验签）。
    console.external_principal_response = _allowed_result(uid)
    identity = await gate.resolve_credentials(
        ChatSessionCredentials(assertion=SecretStr(ASSERTION + "x")),
        platform_key=PLATFORM,
        channel="MSSWWEB",
    )
    assert identity.principal.platform_user_id == uid
    assert len(console.external_principal_calls) == 1


async def test_permission_miss_falls_back_to_console() -> None:
    _, cache = _stores()
    console = FakeConsoleClient()
    gate = ChatPrincipalGate(console, tenant_id=TENANT, auth_cache=cache)
    uid = uuid.uuid4()
    await _prime(cache, _allowed_result(uid))
    # 模拟撤权：Console 删掉权限层键，但凭证还在。
    await cache._client.delete(permission_key(TENANT, uid))

    console.external_principal_response = _allowed_result(uid)
    identity = await gate.resolve_credentials(
        ChatSessionCredentials(assertion=SecretStr(ASSERTION)),
        platform_key=PLATFORM,
        channel="MSSWWEB",
    )
    assert identity.principal.platform_user_id == uid
    assert len(console.external_principal_calls) == 1  # 回源，而不是沿用缓存


async def test_cached_denial_is_not_a_free_pass() -> None:
    """权限层缓存了 `allowed=false`（如用户停用）时，命中同样要拒绝。"""
    from muad_api import AppError

    _, cache = _stores()
    console = FakeConsoleClient()
    gate = ChatPrincipalGate(console, tenant_id=TENANT, auth_cache=cache)
    uid = uuid.uuid4()
    denied = ExternalPrincipalResult(
        allowed=False,
        reason="USER_DISABLED",
        principal=_identity(uid),
        agents=[],
        grants=[],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    await _prime(cache, denied)

    with pytest.raises(AppError):
        await gate.resolve_credentials(
            ChatSessionCredentials(assertion=SecretStr(ASSERTION)),
            platform_key=PLATFORM,
            channel="MSSWWEB",
        )
    assert console.external_principal_calls == []


async def test_redis_failure_falls_back_to_console() -> None:
    client, cache = _stores()
    console = FakeConsoleClient()
    gate = ChatPrincipalGate(console, tenant_id=TENANT, auth_cache=cache)
    uid = uuid.uuid4()
    await _prime(cache, _allowed_result(uid))
    client.fail = True  # Redis 整体不可用

    console.external_principal_response = _allowed_result(uid)
    identity = await gate.resolve_credentials(
        ChatSessionCredentials(assertion=SecretStr(ASSERTION)),
        platform_key=PLATFORM,
        channel="MSSWWEB",
    )
    assert identity.principal.platform_user_id == uid
    assert len(console.external_principal_calls) == 1


async def test_expired_credential_falls_back() -> None:
    _, cache = _stores()
    console = FakeConsoleClient()
    gate = ChatPrincipalGate(console, tenant_id=TENANT, auth_cache=cache)
    uid = uuid.uuid4()
    # 写入一个已到期的绝对到期时间：写入本身会跳过（TTL<=0），故直接塞键模拟边界。
    import json

    await cache._client.set(
        credential_key(TENANT, PLATFORM, hash_session_token(ASSERTION)),
        json.dumps(
            {
                "platform_user_id": str(uid),
                "session_id": str(uuid.uuid4()),
                "expires_at_epoch": (datetime.now(UTC) - timedelta(seconds=1)).timestamp(),
            }
        ),
        ex=60,
    )
    console.external_principal_response = _allowed_result(uid)
    identity = await gate.resolve_credentials(
        ChatSessionCredentials(assertion=SecretStr(ASSERTION)),
        platform_key=PLATFORM,
        channel="MSSWWEB",
    )
    assert identity.principal.platform_user_id == uid
    assert len(console.external_principal_calls) == 1


async def test_no_cache_keeps_old_behaviour() -> None:
    console = FakeConsoleClient()
    gate = ChatPrincipalGate(console, tenant_id=TENANT)
    uid = uuid.uuid4()
    console.external_principal_response = _allowed_result(uid)
    identity = await gate.resolve_credentials(
        ChatSessionCredentials(assertion=SecretStr(ASSERTION)),
        platform_key=PLATFORM,
        channel="MSSWWEB",
    )
    assert identity.principal.platform_user_id == uid
    assert len(console.external_principal_calls) == 1


async def test_platform_segment_isolates_credentials() -> None:
    """同一断言换平台 context 不命中：凭证键含平台段，避免跨平台互相借用结论。"""
    _, cache = _stores()
    console = FakeConsoleClient()
    gate = ChatPrincipalGate(console, tenant_id=TENANT, auth_cache=cache)
    uid = uuid.uuid4()
    await _prime(cache, _allowed_result(uid))  # 只写了 mssw 平台

    console.external_principal_response = _allowed_result(uid)
    identity = await gate.resolve_credentials(
        ChatSessionCredentials(assertion=SecretStr(ASSERTION)),
        platform_key="other",  # 换平台
        channel="MSSWWEB",
    )
    assert identity.principal.platform_user_id == uid
    assert len(console.external_principal_calls) == 1  # 平台段不同 → miss → 回源
