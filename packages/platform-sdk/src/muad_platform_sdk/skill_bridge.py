"""Execution-scoped local IPC for trusted Skill scripts, never a public service."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from redis.exceptions import RedisError

from .adapter.mssp_config import PlatformAuthError
from .skill_access import SkillPlatformAccess, SkillPlatformContext


class PlatformCall(BaseModel):
    model_config = ConfigDict(extra="forbid")
    platform: str = Field(min_length=1)
    method: str
    path: str
    payload: dict[str, Any] = Field(default_factory=dict)


@asynccontextmanager
async def skill_platform_environment(context: SkillPlatformContext | None) -> AsyncIterator[dict[str, str]]:
    if context is None or not context.platform_keys:
        yield {}
        return
    access = SkillPlatformAccess(context)
    active: set[asyncio.Task[None]] = set()

    async def dispatch(reader: asyncio.StreamReader) -> dict[str, Any]:
        try:
            raw = await asyncio.wait_for(reader.readline(), 10)
            call = PlatformCall.model_validate_json(raw)
            return {"data": await access.request(call.platform, call.method, call.path, call.payload)}
        except PlatformAuthError as exc:
            return {"error": exc.code}
        except (ValidationError, ValueError, KeyError, TypeError):
            return {"error": "PLATFORM_REQUEST_INVALID"}
        except RedisError:
            return {"error": "PLATFORM_SESSION_STORE_UNAVAILABLE"}
        except TimeoutError:
            return {"error": "PLATFORM_REQUEST_TIMEOUT"}

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        if task:
            active.add(task)
        try:
            result = await dispatch(reader)
            writer.write(json.dumps(result).encode() + b"\n")
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()
            if task:
                active.discard(task)

    try:
        await access.start()
        with tempfile.TemporaryDirectory(prefix="fx-skill-", dir="/tmp") as directory:
            path = directory + "/platform.sock"
            server = await asyncio.start_unix_server(handle, path, limit=1024 * 1024)
            os.chmod(path, 0o600)
            try:
                yield {"MUAD_PLATFORM_SOCKET": path}
            finally:
                server.close()
                await server.wait_closed()
                for task in list(active):
                    task.cancel()
                await asyncio.gather(*active, return_exceptions=True)
    finally:
        await access.close()
