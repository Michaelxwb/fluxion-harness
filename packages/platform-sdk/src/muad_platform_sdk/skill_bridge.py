"""Execution-bound legacy session-manager endpoint; no business request proxy.

执行期 broker：只服务本次 ``Run``/``Task`` 的一个 Skill 名，通过 Unix socket 返回
**普通只读 state 文件**（不使用 memfd/``/proc``），并在 stdout 同时保留旧 Skill 需要的
``platforms`` list。连接/请求/state 文件数按执行级预算收敛；Cookie 不写日志。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
import shutil
import tempfile
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from redis.exceptions import RedisClusterException, RedisError

from .adapter.mss_config import PlatformAuthError, settings
from .skill_access import SkillPlatformAccess, SkillPlatformContext

BROKER_BUDGET_EXCEEDED = "PLATFORM_SESSION_BUDGET_EXCEEDED"
BROKER_INTERNAL_ERROR = "broker_internal_error"
MAX_BROKER_CONNECTIONS = 8
MAX_BROKER_REQUESTS = 32
MAX_BROKER_STATE_FILES = 8
STATE_FILE_MODE = 0o600
_DIRECTORY_MODE = 0o700
logger = logging.getLogger("muad_platform_sdk.skill_bridge")


@dataclass(frozen=True, slots=True)
class SessionBrokerBudget:
    """一次执行内 broker 的硬预算；超限返回可见错误码，不静默降级。"""

    connections: int = MAX_BROKER_CONNECTIONS
    requests: int = MAX_BROKER_REQUESTS
    state_files: int = MAX_BROKER_STATE_FILES

    def __post_init__(self) -> None:
        for name in ("connections", "requests", "state_files"):
            if getattr(self, name) <= 0:
                raise ValueError(f"broker budget {name} must be positive")


@dataclass(frozen=True, slots=True)
class SessionMount:
    """broker 目录到沙箱的绑定；state 目录只读，运行目录可写（socket/launcher）。"""

    source: Path
    target: str
    readonly: bool


@dataclass(frozen=True, slots=True)
class PlatformSessionEnvironment:
    env: dict[str, str] = field(default_factory=dict)
    mounts: tuple[SessionMount, ...] = ()


@dataclass(slots=True)
class _BudgetLedger:
    limits: SessionBrokerBudget
    connections: int = 0
    requests: int = 0
    state_files: int = 0

    def enter(self) -> bool:
        if self.connections >= self.limits.connections:
            return False
        self.connections += 1
        return True

    def leave(self) -> None:
        self.connections = max(0, self.connections - 1)

    def take_request(self) -> bool:
        if self.requests >= self.limits.requests:
            return False
        self.requests += 1
        return True

    def take_state_file(self) -> bool:
        if self.state_files >= self.limits.state_files:
            return False
        self.state_files += 1
        return True


class _BudgetExceeded(Exception):
    code = BROKER_BUDGET_EXCEEDED


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


def _validate_sandbox_root(value: str) -> str:
    normalized = value.rstrip("/")
    parts = PurePosixPath(normalized).parts
    if not normalized.startswith("/") or ".." in parts or normalized == "":
        raise ValueError(f"invalid session sandbox root: {value!r}")
    if normalized == "/":
        raise ValueError("session sandbox root must not be /")
    return normalized


def _write_launcher(run_dir: Path, cli_path: str) -> None:
    launcher = run_dir / "session-manager"
    launcher.write_text(
        "#!/bin/sh\n"
        "if command -v python3 >/dev/null 2>&1; then\n"
        f'  exec python3 -I {shlex.quote(cli_path)} "$@"\n'
        "fi\n"
        f'exec python -I {shlex.quote(cli_path)} "$@"\n',
        encoding="utf-8",
    )
    launcher.chmod(0o700)


def _prepare_layout(
    root: Path, sandbox_root: str | None
) -> tuple[Path, Path, str, str, tuple[SessionMount, ...]]:
    """返回 ``(run_dir, state_dir, command_dir, state_path_prefix, mounts)``。"""
    run_dir, state_dir = root / "run", root / "state"
    run_dir.mkdir(mode=_DIRECTORY_MODE)
    state_dir.mkdir(mode=_DIRECTORY_MODE)
    cli_source = Path(__file__).with_name("session_cli.py")
    shutil.copyfile(cli_source, run_dir / "session_cli.py")
    if sandbox_root is None:
        command_dir = str(run_dir)
        state_prefix = str(state_dir)
        mounts: tuple[SessionMount, ...] = ()
    else:
        command_dir = _validate_sandbox_root(sandbox_root)
        state_prefix = f"{command_dir}/state"
        mounts = (
            SessionMount(source=run_dir, target=command_dir, readonly=False),
            SessionMount(source=state_dir, target=state_prefix, readonly=True),
        )
    _write_launcher(run_dir, f"{command_dir}/session_cli.py")
    return run_dir, state_dir, command_dir, state_prefix, mounts


async def _write_state(
    access: SkillPlatformAccess,
    state_dir: Path,
    state_prefix: str,
    skill_name: str,
    ledger: _BudgetLedger,
) -> dict[str, Any]:
    platforms: dict[str, dict[str, Any]] = {}
    for key in await access.allowed_keys():
        platform, request, manager = await access.resolve_platform(key)
        state = await manager.acquire(request)
        if state is None:
            raise PlatformAuthError("PLATFORM_SESSION_REQUIRED")
        legacy = _legacy_platform(platform, state)
        if legacy["expiresAt"] is None:
            # stdout 的旧契约要求 expiresAt 必须存在；没有有效期的登录态不能交付。
            raise PlatformAuthError("PLATFORM_SESSION_INVALID")
        platforms[key] = legacy
    if not ledger.take_state_file():
        raise _BudgetExceeded()
    filename = f"{state_prefix}/state-{uuid.uuid4().hex}.json"
    target = state_dir / Path(filename).name
    descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, STATE_FILE_MODE)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump({"version": 1, "skillName": skill_name, "platforms": platforms}, stream)
    return {
        "version": 1,
        "status": "ready",
        "skillName": skill_name,
        "sessionStateFile": filename,
        # 旧 Skill 从 stdout 读 list；state 文件内部仍是 dict。
        "platforms": [
            {"platform": key, "source": "session", "expiresAt": value["expiresAt"]}
            for key, value in platforms.items()
        ],
    }


def _broker_error(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, (_BudgetExceeded, PlatformAuthError)):
        return {"error": exc.code}
    return {"error": BROKER_INTERNAL_ERROR}


@asynccontextmanager
async def skill_platform_environment(
    context: SkillPlatformContext | None,
    skill_name: str = "",
    *,
    sandbox_root: str | None = None,
    budget: SessionBrokerBudget | None = None,
) -> AsyncIterator[PlatformSessionEnvironment]:
    if context is None or not context.platform_keys:
        yield PlatformSessionEnvironment()
        return
    access = SkillPlatformAccess(context)
    ledger = _BudgetLedger(budget or SessionBrokerBudget())
    active: set[asyncio.Task[None]] = set()
    root = Path(tempfile.mkdtemp(prefix="fx-session-", dir="/tmp"))
    try:
        run_dir, state_dir, command_dir, state_prefix, mounts = _prepare_layout(root, sandbox_root)

        async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            task = asyncio.current_task()
            if task:
                active.add(task)
            entered = ledger.enter()
            try:
                if not entered:
                    result = {"error": BROKER_BUDGET_EXCEEDED}
                else:
                    try:
                        request = json.loads(await asyncio.wait_for(reader.readline(), 10))
                        if request != {"skillName": skill_name} or not skill_name:
                            result = {"error": "invalid_context"}
                        elif not ledger.take_request():
                            result = {"error": BROKER_BUDGET_EXCEEDED}
                        else:
                            result = await _write_state(access, state_dir, state_prefix, skill_name, ledger)
                    except (ValueError, TypeError, TimeoutError):
                        result = {"error": "invalid_arguments"}
                    except (RedisError, RedisClusterException):
                        result = {"error": "PLATFORM_SESSION_STORE_UNAVAILABLE"}
                    except Exception as exc:  # noqa: BLE001 - 内部错误必须可见但不泄密
                        logger.warning("session_broker_internal_error type=%s", type(exc).__name__)
                        result = _broker_error(exc)
                writer.write(json.dumps(result).encode() + b"\n")
                await writer.drain()
            finally:
                if entered:
                    ledger.leave()
                writer.close()
                await writer.wait_closed()
                if task:
                    active.discard(task)

        await access.start()
        socket_path = str(run_dir / "session.sock")
        server = await asyncio.start_unix_server(handle, socket_path, limit=65536)
        os.chmod(socket_path, 0o600)
        try:
            yield PlatformSessionEnvironment(
                env={
                    "MUAD_SESSION_SOCKET": f"{command_dir}/session.sock",
                    "PATH": command_dir + os.pathsep + os.environ.get("PATH", os.defpath),
                },
                mounts=mounts,
            )
        finally:
            server.close()
            await server.wait_closed()
            for task in list(active):
                task.cancel()
            await asyncio.gather(*active, return_exceptions=True)
    finally:
        await access.close()
        shutil.rmtree(root, ignore_errors=True)


__all__ = [
    "BROKER_BUDGET_EXCEEDED",
    "PlatformSessionEnvironment",
    "SessionBrokerBudget",
    "SessionMount",
    "skill_platform_environment",
]
