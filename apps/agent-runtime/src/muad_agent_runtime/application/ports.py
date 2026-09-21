from __future__ import annotations

from typing import Any, Protocol

from muad_contracts import ResolveDefinitionRequest, ResolveDefinitionResponse


class ResolveClient(Protocol):
    async def resolve(
        self,
        request: ResolveDefinitionRequest,
        *,
        tenant_id: str,
        trace_id: str = "",
    ) -> ResolveDefinitionResponse: ...


class CredentialsClient(Protocol):
    """API-09：按冻结主键读取当前认证值（只进内存，不落盘）。"""

    async def resolve_credentials(
        self,
        *,
        tenant_id: str,
        payload: dict[str, Any],
        trace_id: str = "",
    ) -> dict[str, Any]: ...
