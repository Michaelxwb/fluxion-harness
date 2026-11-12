"""Invocation-private metadata IPC environment for an existing Skill executor."""
from __future__ import annotations

import asyncio
import importlib.util
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

from .egress_ipc import InvocationEgressBroker
from .shared_redis import MeteringUnavailable

SCRIPT_EGRESS_ROOT = "/tmp/fx-egress"


@dataclass(frozen=True, slots=True)
class ScriptEgressMount:
    source: Path
    target: str
    readonly: bool = True


@dataclass(frozen=True, slots=True)
class ScriptEgressEnvironment:
    env: dict[str, str] = field(default_factory=dict)
    mounts: tuple[ScriptEgressMount, ...] = ()
    launcher: Path | None = None
    exhausted: asyncio.Event | None = field(default=None, repr=False)


def _launcher() -> Path:
    spec = importlib.util.find_spec("muad_egress_launch")
    if spec is None or spec.origin is None:
        raise MeteringUnavailable("trusted egress launcher unavailable")
    path = Path(spec.origin).resolve()
    if not path.is_file():
        raise MeteringUnavailable("trusted egress launcher unavailable")
    return path


@asynccontextmanager
async def skill_egress_environment(broker: InvocationEgressBroker | None, *,
                                   dev_unsafe: bool) -> AsyncIterator[ScriptEgressEnvironment]:
    if broker is None:
        yield ScriptEgressEnvironment()
        return
    launcher = _launcher()
    async with broker:
        socket = broker.socket_path if dev_unsafe else Path(SCRIPT_EGRESS_ROOT) / broker.socket_path.name
        mounts = () if dev_unsafe else (ScriptEgressMount(broker.socket_path.parent, SCRIPT_EGRESS_ROOT),)
        yield ScriptEgressEnvironment(env={"MUAD_EGRESS_SOCKET": str(socket)}, mounts=mounts,
                                      launcher=launcher, exhausted=broker.exhausted)
