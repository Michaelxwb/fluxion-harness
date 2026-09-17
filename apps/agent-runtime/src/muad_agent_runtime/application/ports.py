from __future__ import annotations

from typing import Protocol

from muad_contracts import ResolveDefinitionRequest, ResolveDefinitionResponse


class ResolveClient(Protocol):
    async def resolve(
        self,
        request: ResolveDefinitionRequest,
        *,
        tenant_id: str,
        trace_id: str = "",
    ) -> ResolveDefinitionResponse: ...
