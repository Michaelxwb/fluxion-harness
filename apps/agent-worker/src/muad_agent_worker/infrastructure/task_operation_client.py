"""API-30：Task-owned 异步工具操作的提交 / 查询 / 取消（Worker 侧客户端）。

- 调用身份 `(tenant, owner=task_execution_id, source_tool_call_id)` 由 Runtime 唯一化；重试返回同一
  operation，`input_hash` 只用于"同一 call 换了参数"的显式失败。
- 结果读取可重放：Runtime 没有"消费确认"端点，消费水位（checkpoint / wait_payload / task_event）
  由 Worker 自己的事务持有。本模块只读，从不推进水位。
- 归属只来自服务身份 + 租户/操作者头；请求体不是信任来源。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any
from uuid import UUID

import httpx
from muad_agent_core.tools.pipeline import compute_args_hash
from muad_contracts import (
    CompletionMode,
    OperationStatus,
    TaskToolOperationCancelRequest,
    TaskToolOperationCancelResponse,
    TaskToolOperationList,
    TaskToolOperationOwner,
    TaskToolOperationRecoveryView,
    TaskToolOperationResult,
    TaskToolOperationSubmitRequest,
    TaskToolOperationView,
)

from ..metrics import TASK_ASYNC_OPERATION_METRIC, record_outcome

TOOL_OPERATIONS_PATH = "/internal/tool-operations"
DEFAULT_TIMEOUT_SEC = 5.0
PAGE_SIZE = 100


class TaskOperationError(Exception):
    """提交/读取没有得出结论：`code` 是 Runtime 的稳定错误码。"""

    def __init__(self, message: str, *, code: str = "TOOL_OPERATION_UNAVAILABLE") -> None:
        super().__init__(message)
        self.code = code


class TaskOperationClient:
    def __init__(
        self,
        base_url: str,
        client: httpx.AsyncClient,
        *,
        service_token: str | None = None,
        timeout_sec: float = DEFAULT_TIMEOUT_SEC,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = client
        self._service_token = service_token
        self._timeout_sec = timeout_sec

    async def submit(
        self,
        *,
        tenant_id: str,
        actor_user_id: UUID,
        execution_id: UUID,
        call_id: str,
        tool_name: str,
        arguments: Mapping[str, Any],
        completion_mode: CompletionMode = CompletionMode.JOIN,
        input_hash: str | None = None,
    ) -> TaskToolOperationView:
        request = TaskToolOperationSubmitRequest(
            owner=TaskToolOperationOwner(task_execution_id=execution_id),
            source_tool_call_id=call_id,
            tool_name=tool_name,
            input_hash=input_hash or compute_args_hash(arguments),
            arguments=dict(arguments),
            completion_mode=completion_mode,
        )
        try:
            data = await self._request(
                "POST",
                tenant_id,
                actor_user_id,
                TOOL_OPERATIONS_PATH,
                json=request.model_dump(mode="json"),
            )
        except TaskOperationError:
            record_outcome(TASK_ASYNC_OPERATION_METRIC, "SUBMIT_FAILED")
            raise
        record_outcome(TASK_ASYNC_OPERATION_METRIC, "SUBMITTED")
        return TaskToolOperationView.model_validate(data)

    async def get(
        self, *, tenant_id: str, actor_user_id: UUID, operation_id: UUID
    ) -> TaskToolOperationView:
        data = await self._request(
            "GET",
            tenant_id,
            actor_user_id,
            f"{TOOL_OPERATIONS_PATH}/{operation_id}",
        )
        return TaskToolOperationView.model_validate(data)

    async def get_recovery(
        self,
        *,
        tenant_id: str,
        actor_user_id: UUID,
        execution_id: UUID,
        call_id: str,
    ) -> TaskToolOperationRecoveryView:
        data = await self._request(
            "GET",
            tenant_id,
            actor_user_id,
            f"{TOOL_OPERATIONS_PATH}/recovery",
            params={"task_execution_id": str(execution_id), "call_id": call_id},
        )
        return TaskToolOperationRecoveryView.model_validate(data)

    async def list_operations(
        self,
        *,
        tenant_id: str,
        actor_user_id: UUID,
        execution_id: UUID,
        ready: bool | None = None,
        page: int = 1,
        page_size: int = PAGE_SIZE,
    ) -> TaskToolOperationList:
        params: dict[str, Any] = {
            "task_execution_id": str(execution_id),
            "page": page,
            "page_size": page_size,
        }
        if ready is not None:
            params["ready"] = "true" if ready else "false"
        data = await self._request(
            "GET", tenant_id, actor_user_id, TOOL_OPERATIONS_PATH, params=params
        )
        return TaskToolOperationList.model_validate(data)

    async def cancel(
        self,
        *,
        tenant_id: str,
        actor_user_id: UUID,
        execution_id: UUID,
        call_id: str,
    ) -> TaskToolOperationCancelResponse:
        request = TaskToolOperationCancelRequest(
            owner=TaskToolOperationOwner(task_execution_id=execution_id),
            source_tool_call_id=call_id,
        )
        data = await self._request(
            "POST",
            tenant_id,
            actor_user_id,
            f"{TOOL_OPERATIONS_PATH}/cancel",
            json=request.model_dump(mode="json"),
        )
        return TaskToolOperationCancelResponse.model_validate(data)

    async def _request(
        self,
        method: str,
        tenant_id: str,
        actor_user_id: UUID,
        path: str,
        *,
        json: Any = None,
        params: Mapping[str, Any] | None = None,
    ) -> Any:
        headers = {"X-Tenant-Id": tenant_id, "X-Actor-User-Id": str(actor_user_id)}
        if self._service_token:
            headers["X-Internal-Service"] = self._service_token
        try:
            response = await self._client.request(
                method,
                f"{self._base_url}{path}",
                json=json,
                params=dict(params or {}),
                headers=headers,
                timeout=self._timeout_sec,
            )
        except httpx.HTTPError as exc:
            raise TaskOperationError(type(exc).__name__) from exc
        try:
            body = response.json()
        except ValueError as exc:
            raise TaskOperationError(f"http_{response.status_code}") from exc
        if not isinstance(body, dict):
            raise TaskOperationError(f"http_{response.status_code}")
        code = body.get("code")
        if code != "0":
            raise TaskOperationError(str(code), code=str(code))
        data = body.get("data")
        if not isinstance(data, dict):
            raise TaskOperationError("malformed_operation_response")
        return data


_TERMINAL = frozenset(
    (
        OperationStatus.COMPLETED,
        OperationStatus.FAILED,
        OperationStatus.CANCELLED,
        OperationStatus.LATE,
    )
)


def operation_result_content(result: TaskToolOperationResult) -> str:
    """结果 → 模型可见的 tool 消息正文（与工具自己"返回错误体"同形）。"""
    if result.error_code is not None:
        return json.dumps(
            {
                "error": {
                    "code": result.error_code,
                    "message": result.error_message or "",
                }
            },
            ensure_ascii=False,
        )
    return json.dumps(result.result or {}, ensure_ascii=False)


__all__ = [
    "TaskOperationClient",
    "TaskOperationError",
    "operation_result_content",
]
