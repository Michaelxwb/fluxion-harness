from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from muad_platform_sdk import PlatformClient


@dataclass(frozen=True, slots=True)
class SkillUser:
    user_id: str
    tenant_id: str
    display_name: str


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status_code: int
    headers: Mapping[str, str]
    body: bytes


class ArtifactAccess(Protocol):
    async def read(self, storage_key: str) -> bytes: ...

    async def write(self, storage_key: str, data: bytes) -> None: ...


class McpClient(Protocol):
    async def call(self, server: str, tool: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]: ...


class TaskClient(Protocol):
    async def map(
        self,
        *,
        items: Sequence[Mapping[str, Any]],
        script: str,
        max_concurrency: int,
    ) -> Sequence[Mapping[str, Any]]: ...


class HttpClient(Protocol):
    async def get(
        self,
        url: str,
        *,
        timeout_sec: float,
        headers: Mapping[str, str] | None = None,
    ) -> HttpResponse: ...

    async def post(
        self,
        url: str,
        *,
        timeout_sec: float,
        body: str | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> HttpResponse: ...

    async def request(
        self,
        method: str,
        url: str,
        *,
        timeout_sec: float,
        body: str | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> HttpResponse: ...


@dataclass(slots=True)
class SkillContext:
    user: SkillUser
    logger: logging.Logger
    artifact: ArtifactAccess
    platform: PlatformClient
    mcp: McpClient
    task: TaskClient
    http: HttpClient
