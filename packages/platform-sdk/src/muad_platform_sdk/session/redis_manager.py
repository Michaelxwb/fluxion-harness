"""Credential-scoped platform sessions with a two-hour sliding idle timeout."""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import time
from datetime import UTC, datetime, timedelta
from typing import Protocol, cast

from redis.asyncio import Redis
from redis.asyncio.cluster import RedisCluster

from ..adapter import PlatformAdapterRegistry
from ..adapter.mss_config import PlatformAuthError
from ..types import PlatformConfig, PlatformSession, SessionMode
from .key import platform_session_key, platform_sessions_index_key
from .manager import SessionRequest

IDLE_TIMEOUT_SECONDS = 2 * 60 * 60
_TOUCH = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
redis.call('SET', KEYS[1], ARGV[2], 'EX', ARGV[3])
return 1
"""
_PUBLISH = """
if redis.call('GET', KEYS[3]) ~= ARGV[3] then return 0 end
if (redis.call('GET', KEYS[4]) or '0') ~= ARGV[4] then return 0 end
redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[2])
redis.call('SADD', KEYS[2], KEYS[1])
return 1
"""
_RELEASE = "if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) end return 0"


class AsyncRedisCommands(Protocol):
    # redis-py exposes execute_command without async typing in its command mixin.
    async def execute_command(self, *args: str | bytes | int) -> object: ...


class RedisPlatformSessionManager:
    def __init__(
        self,
        client: Redis | RedisCluster,
        registry: PlatformAdapterRegistry,
        *,
        tenant_id: str,
        platform_id: str,
        idle_timeout_seconds: int = IDLE_TIMEOUT_SECONDS,
    ) -> None:
        self.redis = client
        self.commands = cast(AsyncRedisCommands, client)
        self.registry = registry
        self.tenant_id = tenant_id
        self.platform_id = platform_id
        self.ttl = idle_timeout_seconds
        self.index = platform_sessions_index_key(tenant_id=tenant_id, platform_id=platform_id)
        self.epoch = self.index + ":generation"

    def key(self, request: SessionRequest) -> str:
        config = json.dumps(request.platform.model_dump(mode="json"), sort_keys=True)
        revision = hashlib.sha256(config.encode()).hexdigest()[:24]
        adapter = self.registry.get(request.platform.adapter_key)
        return platform_session_key(
            tenant_id=self.tenant_id,
            platform_id=self.platform_id,
            actor_scope=request.actor_scope,
            credential_version=request.credential_version + "-" + revision,
            adapter_key=adapter.key,
            adapter_version=adapter.version,
        )

    def _extend(self, session: PlatformSession) -> PlatformSession:
        return session.model_copy(update={"expires_at": datetime.now(UTC) + timedelta(seconds=self.ttl)})

    async def _cached(self, request: SessionRequest) -> PlatformSession | None:
        key = self.key(request)
        raw = await self.redis.get(key)
        if raw is None:
            return None
        state = self._extend(PlatformSession.model_validate_json(raw))
        adapter = self.registry.get(request.platform.adapter_key)
        if not await adapter.validate(request.platform, state):
            # Never delete a newer session published by a concurrent caller.
            await self.commands.execute_command(
                "EVAL",
                "if redis.call('GET',KEYS[1]) == ARGV[1] then return redis.call('DEL',KEYS[1]) end return 0",
                1,
                key,
                raw,
            )
            return None
        touched = await self.commands.execute_command(
            "EVAL", _TOUCH, 1, key, raw, state.model_dump_json(), self.ttl
        )
        return state if touched else None

    async def acquire(self, request: SessionRequest) -> PlatformSession | None:
        if not request.platform.enabled:
            raise PlatformAuthError("PLATFORM_UNAVAILABLE")
        adapter = self.registry.get(request.platform.adapter_key)
        if adapter.session_mode != SessionMode.SESSION:
            return None
        deadline = time.monotonic() + 125
        key, owner = self.key(request), secrets.token_hex(16)
        while time.monotonic() < deadline:
            state = await self._cached(request)
            if state is not None:
                return state
            if await self.redis.set(key + ":lock", owner, nx=True, ex=120):
                try:
                    return await self._login(request, key, owner)
                finally:
                    await self.commands.execute_command("EVAL", _RELEASE, 1, key + ":lock", owner)
            await asyncio.sleep(0.05)
        raise PlatformAuthError("PLATFORM_SESSION_BUSY", retryable=True)

    async def _login(self, request: SessionRequest, key: str, owner: str) -> PlatformSession:
        state = await self._cached(request)
        if state is not None:
            return state
        generation = await self.redis.get(self.epoch) or b"0"
        if request.credential is None:
            raise PlatformAuthError("CREDENTIAL_MISSING")
        state = await self.registry.get(request.platform.adapter_key).authenticate(
            request.platform, request.credential
        )
        if state is None:
            raise PlatformAuthError("PLATFORM_LOGIN_NO_SESSION")
        state = self._extend(state)
        saved = await self.commands.execute_command(
            "EVAL",
            _PUBLISH,
            4,
            key,
            self.index,
            key + ":lock",
            self.epoch,
            state.model_dump_json(),
            self.ttl,
            owner,
            generation,
        )
        if not saved:
            raise PlatformAuthError("PLATFORM_SESSION_CHANGED", retryable=True)
        return state

    async def renew(self, request: SessionRequest) -> PlatformSession | None:
        await self.redis.delete(self.key(request))
        return await self.acquire(request)

    async def invalidate(self, *, platform: PlatformConfig, actor_scope: str) -> None:
        # Exact prefix from the platform index; never use KEYS/SCAN.
        prefix = (
            f"muad:platform_session:{{{self.tenant_id}:{self.platform_id}}}:{actor_scope}:"
        )
        keys = [
            k
            for k in cast(list[bytes], await self.commands.execute_command("SMEMBERS", self.index))
            if (k.decode() if isinstance(k, bytes) else k).startswith(prefix)
        ]
        if keys:
            await self.redis.delete(*keys)
            await self.commands.execute_command("SREM", self.index, *keys)
