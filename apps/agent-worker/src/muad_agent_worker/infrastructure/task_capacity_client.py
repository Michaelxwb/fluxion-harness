"""API-29 客户端：Worker 以服务身份为 Task Execution 申请 / 续租 / 结算模型容量。

为什么 Worker 不直写 `runtime.model_call_lease`：表所有权在 Runtime（design §3.1.5）；Worker 只
持有 `task.task_execution`（自己的 schema），经内部 API 用 Task 能力。

归属口径：

- 本客户端只发**服务身份 + 租户头 + 操作者头**；`owner` 是 `task_execution_id`，不是提交产生的
  `task_id`。Runtime 按 Execution 行复核，请求体不是信任来源。
- `workload` 固定 BACKGROUND：定时任务不与交互式 Run 争抢份额（高峰让路、老化后放行）。
- 租约的执行方身份由 Runtime 从 Execution 派生，客户端不声明、也无法覆盖。

失败口径（"失约即停手"）：

- `CapacityLeaseLost`：`MODEL_CALL_LEASE_LOST`（过期/epoch 不符/已终态）——调用方必须**停止本地
  模型请求**，不能续跑；
- `CapacityUnavailable`：`MODEL_CAPACITY_UNAVAILABLE`——让路/退避，不是终态；
- 其它非 0 码（含 `MODEL_CREDENTIAL_CHANGED`，须重新 Admission 冻结引用）与传输失败一律
  `TaskCapacityError`：没有结论就不能继续调用模型。
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
from muad_contracts import (
    TaskCallSettlement,
    TaskCapacityGrant,
    TaskCapacityRelease,
    TaskCapacityReservationRequest,
    TaskLeaseRenewal,
)

TASK_RESERVATIONS_PATH = "/internal/model-capacity/task-reservations"
DEFAULT_TIMEOUT_SEC = 5.0

LEASE_LOST_CODE = "MODEL_CALL_LEASE_LOST"
CAPACITY_UNAVAILABLE_CODE = "MODEL_CAPACITY_UNAVAILABLE"


class TaskCapacityError(Exception):
    """容量调用没有得出结论或未获准：没有结论就不能继续调用模型。"""

    def __init__(self, reason: str, *, code: str | None = None) -> None:
        self.code = code
        super().__init__(reason)


class CapacityLeaseLost(TaskCapacityError):
    """本执行方已失约（过期/epoch 不符/已终态）：停止本地模型请求。"""


class CapacityUnavailable(TaskCapacityError):
    """当前没有可用容量：让路/退避，不是终态。"""


class TaskCapacityClient:
    def __init__(
        self,
        base_url: str,
        *,
        service_token: str | None = None,
        timeout_sec: float = DEFAULT_TIMEOUT_SEC,
        transport: httpx.AsyncBaseTransport | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._service_token = service_token
        # 请求用绝对 URL 组装：注入的共享池客户端没有 base_url（一个池服务多个目标）。
        self._base_url = base_url.rstrip("/")
        # 装配根注入的业务池由**装配根拥有并关闭**（本实例只借用）；未注入时自建自关。
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout_sec, transport=transport)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def reserve(
        self,
        *,
        tenant_id: str,
        actor_user_id: uuid.UUID,
        request: TaskCapacityReservationRequest,
    ) -> TaskCapacityGrant:
        data = await self._post(
            TASK_RESERVATIONS_PATH,
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            payload=request.model_dump(mode="json"),
        )
        return TaskCapacityGrant.model_validate(data)

    async def renew(
        self,
        *,
        tenant_id: str,
        actor_user_id: uuid.UUID,
        reservation_id: uuid.UUID,
        renewal: TaskLeaseRenewal,
    ) -> TaskCapacityGrant:
        data = await self._post(
            f"{TASK_RESERVATIONS_PATH}/{reservation_id}/renew",
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            payload=renewal.model_dump(mode="json"),
        )
        return TaskCapacityGrant.model_validate(data)

    async def settle(
        self,
        *,
        tenant_id: str,
        actor_user_id: uuid.UUID,
        reservation_id: uuid.UUID,
        settlement: TaskCallSettlement,
    ) -> TaskCapacityRelease:
        data = await self._post(
            f"{TASK_RESERVATIONS_PATH}/{reservation_id}/settle",
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            payload=settlement.model_dump(mode="json"),
        )
        return TaskCapacityRelease.model_validate(data)

    async def _post(
        self,
        path: str,
        *,
        tenant_id: str,
        actor_user_id: uuid.UUID,
        payload: dict[str, Any],
    ) -> Any:
        headers = {"X-Tenant-Id": tenant_id, "X-Actor-User-Id": str(actor_user_id)}
        if self._service_token:
            headers["X-Internal-Service"] = self._service_token
        try:
            response = await self._client.post(
                f"{self._base_url}{path}", json=payload, headers=headers
            )
        except httpx.HTTPError as exc:
            raise TaskCapacityError(type(exc).__name__) from exc
        body = self._envelope(response)
        code = body.get("code")
        if code == "0":
            data = body.get("data")
            if not isinstance(data, dict):
                raise TaskCapacityError("malformed_capacity_response")
            return data
        reason = str(code)
        if code == LEASE_LOST_CODE:
            raise CapacityLeaseLost(reason, code=reason)
        if code == CAPACITY_UNAVAILABLE_CODE:
            raise CapacityUnavailable(reason, code=reason)
        raise TaskCapacityError(reason, code=reason)

    @staticmethod
    def _envelope(response: httpx.Response) -> dict[str, Any]:
        """响应封套必须可解析：畸形体按"没有结论"处理，不猜测成功。"""
        try:
            body = response.json()
        except ValueError as exc:
            raise TaskCapacityError(f"http_{response.status_code}") from exc
        if not isinstance(body, dict):
            raise TaskCapacityError(f"http_{response.status_code}")
        return body
