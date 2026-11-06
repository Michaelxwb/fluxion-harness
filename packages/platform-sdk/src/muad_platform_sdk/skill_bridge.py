"""Execution-bound legacy session-manager endpoint; no business request proxy."""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import sys
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from redis.exceptions import RedisClusterException, RedisError

from .adapter.mss_config import PlatformAuthError, settings
from .skill_access import SkillPlatformAccess, SkillPlatformContext


def _legacy_platform(platform: Any, state: Any) -> dict[str, Any]:
    config = settings(platform)
    try:
        cookies = json.loads(state.cookie or "[]")
    except (TypeError, ValueError):
        raise PlatformAuthError("PLATFORM_SESSION_INVALID") from None
    if not isinstance(cookies, list) or not all(isinstance(cookie, dict) for cookie in cookies):
        raise PlatformAuthError("PLATFORM_SESSION_INVALID")
    profile: dict[str, Any] = {
        "baseUrl": str(platform.resolver_config.get("base_url", "")),
        "hostHeader": {"Host": config.host_header} if config.host_header else {},
        "csrf": [{"cookie": "csrf_token", "header": config.csrf_header}],
        "staticHeaders": {**config.business_headers, "Timezone": config.timezone},
        "traceHeader": None,
        "extraAllowed": False,
    }
    if config.referer:
        profile["referer"] = config.referer
    return {
        "platform": platform.key,
        "expiresAt": state.expires_at.isoformat() if state.expires_at else None,
        "cookies": cookies,
        "storageState": {"cookies": cookies, "origins": []},
        "requestProfile": profile,
    }


async def _write_state(
    access: SkillPlatformAccess, directory: str, skill_name: str, open_files: list[int]
) -> dict[str, Any]:
    platforms = {}
    for key in access.context.platform_keys:
        platform, request, manager = await access.resolve(key, "GET", "/")
        state = await manager.acquire(request)
        if state is None:
            raise PlatformAuthError("PLATFORM_SESSION_REQUIRED")
        platforms[key] = _legacy_platform(platform, state)
    if hasattr(os, "memfd_create") and Path("/proc/self/fd").is_dir():
        fd = os.memfd_create("skill-session")
        os.fchmod(fd, 0o600)
        filename = f"/proc/{os.getpid()}/fd/{fd}"
        open_files.append(fd)
    else:
        fd, filename = tempfile.mkstemp(prefix="state-", suffix=".json", dir=directory)
    try:
        with os.fdopen(os.dup(fd), "w", encoding="utf-8") as stream:
            json.dump({"version": 1, "skillName": skill_name, "platforms": platforms}, stream)
    except BaseException:
        if fd in open_files:
            open_files.remove(fd)
        os.close(fd)
        if not filename.startswith("/proc/"):
            Path(filename).unlink(missing_ok=True)
        raise
    if fd not in open_files:
        os.close(fd)
    return {"version": 1, "status": "ready", "skillName": skill_name, "sessionStateFile": filename}


@asynccontextmanager
async def skill_platform_environment(
    context: SkillPlatformContext | None, skill_name: str = ""
) -> AsyncIterator[dict[str, str]]:
    if context is None or not context.platform_keys:
        yield {}
        return
    access = SkillPlatformAccess(context)
    active: set[asyncio.Task[None]] = set()
    open_files: list[int] = []
    with tempfile.TemporaryDirectory(prefix="fx-session-", dir="/tmp") as directory:

        async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            task = asyncio.current_task()
            if task:
                active.add(task)
            try:
                try:
                    request = json.loads(await asyncio.wait_for(reader.readline(), 10))
                    if request != {"skillName": skill_name} or not skill_name:
                        result = {"error": "invalid_context"}
                    else:
                        result = await _write_state(access, directory, skill_name, open_files)
                except PlatformAuthError as exc:
                    result = {"error": exc.code}
                except (ValueError, TypeError, TimeoutError):
                    result = {"error": "invalid_arguments"}
                except (RedisError, RedisClusterException):
                    result = {"error": "PLATFORM_SESSION_STORE_UNAVAILABLE"}
                writer.write(json.dumps(result).encode() + b"\n")
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()
                if task:
                    active.discard(task)

        try:
            await access.start()
            socket_path = os.path.join(directory, "session.sock")
            server = await asyncio.start_unix_server(handle, socket_path, limit=65536)
            os.chmod(socket_path, 0o600)
            launcher = Path(directory) / "session-manager"
            launcher.write_text(
                f'#!/bin/sh\nexec {shlex.quote(sys.executable)} -I -m muad_platform_sdk.session_cli "$@"\n'
            )
            launcher.chmod(0o700)
            try:
                yield {
                    "MUAD_SESSION_SOCKET": socket_path,
                    "PATH": directory + os.pathsep + os.environ.get("PATH", os.defpath),
                }
            finally:
                server.close()
                await server.wait_closed()
                for task in list(active):
                    task.cancel()
                await asyncio.gather(*active, return_exceptions=True)
        finally:
            for fd in open_files:
                os.close(fd)
            await access.close()
