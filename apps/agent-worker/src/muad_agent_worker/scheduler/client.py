from __future__ import annotations

from typing import Protocol
from uuid import UUID

import httpx
from muad_contracts import ResolveDefinitionRequest, ResolveDefinitionResponse

RESOLVE_DEFINITION_PATH = "/internal/runtime/resolve-definition"
RESOLVE_UNAVAILABLE = "RESOLVE_UNAVAILABLE"


class ResolveTransportError(Exception):
    """resolve-definition 不可用或被业务拒绝。

    `code` 保留 Console 侧的稳定错误码（如 `AGENT_ACCESS_DENIED`），
    供 Schedule 记录失败原因；无法解析时为 `RESOLVE_UNAVAILABLE`。
    """

    def __init__(self, message: str, *, code: str = RESOLVE_UNAVAILABLE) -> None:
        super().__init__(message)
        self.code = code


def _failure_code(response: httpx.Response) -> str:
    try:
        code = response.json().get("code")
    except ValueError:
        return RESOLVE_UNAVAILABLE
    return code if isinstance(code, str) and code else RESOLVE_UNAVAILABLE


class ResolveDefinitionProtocol(Protocol):
    async def resolve(
        self, agent_id: UUID, actor_user_id: UUID, tenant_id: str
    ) -> ResolveDefinitionResponse: ...


class ConsoleResolveClient:
    def __init__(
        self, base_url: str, client: httpx.AsyncClient, *, service_token: str | None = None
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = client
        self._service_token = service_token

    async def resolve(
        self, agent_id: UUID, actor_user_id: UUID, tenant_id: str
    ) -> ResolveDefinitionResponse:
        # 定时触发**不经渠道**：交付通道属于 Schedule 的 `delivery_route`，不是 resolve 的输入
        # （resolve 只用 agent/actor）。为填一个没人读的字段在热路径上多打一次库不划算，
        # 更不能凭空编一个通道名（B-09）⇒ 显式省略。
        request = ResolveDefinitionRequest(
            agent_id=agent_id,
            actor_user_id=actor_user_id,
        )
        headers = {"X-Tenant-Id": tenant_id}
        if self._service_token:
            headers["X-Internal-Service"] = self._service_token
        try:
            response = await self._client.post(
                f"{self._base_url}{RESOLVE_DEFINITION_PATH}",
                json=request.model_dump(mode="json"),
                headers=headers,
            )
        except httpx.HTTPError as exc:
            raise ResolveTransportError(str(exc)) from exc
        if response.status_code >= 400:
            raise ResolveTransportError(
                f"resolve-definition returned status {response.status_code}",
                code=_failure_code(response),
            )
        body = response.json()
        return ResolveDefinitionResponse.model_validate(body["data"])
