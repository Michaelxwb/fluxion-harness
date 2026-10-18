from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
from muad_api.security import INTERNAL_SERVICE_HEADER
from muad_contracts import DeliveryRequest
from muad_contracts.webchat_events import WebchatTaskResultRegistration

DELIVERY_PATH = "/internal/deliveries"
#: Runtime 受信 Task 结果入箱端口（TASK-064 提供，TASK-068 接入）。
WEBCHAT_TASK_RESULTS_PATH = "/internal/webchat/task-results"


class DeliveryTransportError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    """Gateway 的投递结果。

    `delivered=False` 表示 Gateway 只占位、尚未确认送达（in-flight 重复）：
    Worker 不得据此置 SENT，应按可重试失败处理。
    """

    status_code: int
    delivered: bool = True


class DeliveryClientProtocol(Protocol):
    async def deliver(self, request: DeliveryRequest) -> DeliveryResult: ...


class HttpDeliveryClient:
    """网关投递客户端。

    `service_token` 是**内部服务身份**：`/internal/deliveries` 与 Console 的
    `resolve-definition`/`resolve-credentials` 同一门控（2026-10-06 起），不带这个头一律
    `FORBIDDEN` —— 该端点此前无鉴权，任何能连上内网的人都能借它把任意存储键指向的文件发给用户。
    """

    def __init__(
        self, base_url: str, client: httpx.AsyncClient, *, service_token: str | None = None
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = client
        self._service_token = service_token

    async def deliver(self, request: DeliveryRequest) -> DeliveryResult:
        headers = {INTERNAL_SERVICE_HEADER: self._service_token} if self._service_token else {}
        try:
            response = await self._client.post(
                f"{self._base_url}{DELIVERY_PATH}",
                json=request.model_dump(mode="json"),
                headers=headers,
            )
        except httpx.HTTPError as exc:
            raise DeliveryTransportError(str(exc)) from exc
        return DeliveryResult(status_code=response.status_code, delivered=_delivered(response))


def _delivered(response: httpx.Response) -> bool:
    """只有**明确** `data.delivered == true` 才算送达（2026-10-06 评审 #11）。

    HTTP 200 只说明请求被受理，不说明渠道真的发出去了。此前非法 JSON、缺 `delivered`
    字段、字段类型不对一律按「已送达」处理——于是 Worker 把行置成 `SENT`，却**没有任何
    可信回执**：任务结果再也不会重投，用户就是收不到。现在解析不了一律按未送达处理，
    走既有的可重试失败 + 退避；`delivery_key` 稳定，重投仍由网关去重。
    """
    try:
        payload = response.json()
    except ValueError:
        return False
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return False
    return data.get("delivered") is True


@dataclass(frozen=True, slots=True)
class WebchatInboxReceipt:
    """Runtime 持久用户收件箱的入箱回执。

    `persisted=True` 只在 Runtime 已提交通知行之后返回；`duplicate=True` 表示重投命中
    原回执（不新占 seq、不新增未读）——两者都说明**结果已经进箱**。
    """

    status_code: int
    persisted: bool
    duplicate: bool
    error_code: str | None = None


class WebchatInboxClientProtocol(Protocol):
    """网页投递的受信入箱端口：把 Worker 真实持有的身份事实提交给 Runtime Owner。"""

    async def register_task_result(
        self, *, tenant_id: str, registration: WebchatTaskResultRegistration
    ) -> WebchatInboxReceipt: ...


class HttpWebchatInboxClient:
    """Runtime 受信入箱端口的 HTTP 客户端（TASK-068）。

    与 Gateway 投递同一门控（`X-Internal-Service` 服务身份 + `X-Tenant-Id` 租户）和同一
    纪律：只有**明确** `data.persisted == true` 且回执 `task_id` 与本次提交一致才算入箱；
    非法 JSON / 缺字段一律按未入箱处理（可重试），绝不把 HTTP 2xx 当送达——网页用户离线
    时 socket 写入什么都留不下，只有 Runtime 提交后的收件箱行才是"结果送到了"。
    """

    def __init__(
        self, base_url: str, client: httpx.AsyncClient, *, service_token: str | None = None
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = client
        self._service_token = service_token

    async def register_task_result(
        self, *, tenant_id: str, registration: WebchatTaskResultRegistration
    ) -> WebchatInboxReceipt:
        headers = {"X-Tenant-Id": tenant_id}
        if self._service_token:
            headers[INTERNAL_SERVICE_HEADER] = self._service_token
        try:
            response = await self._client.post(
                f"{self._base_url}{WEBCHAT_TASK_RESULTS_PATH}",
                json=registration.model_dump(mode="json"),
                headers=headers,
            )
        except httpx.HTTPError as exc:
            raise DeliveryTransportError(str(exc)) from exc
        return _inbox_receipt(response, task_id=registration.task_id)


def _inbox_receipt(response: httpx.Response, *, task_id: uuid.UUID) -> WebchatInboxReceipt:
    """解析入箱回执；任何不完整/不匹配的形状一律按未入箱返回（调用方据此重试）。"""
    payload = _json_object(response)
    data = payload.get("data") if payload is not None else None
    code = payload.get("code") if payload is not None else None
    if not isinstance(data, dict):
        return WebchatInboxReceipt(
            status_code=response.status_code, persisted=False, duplicate=False, error_code=None
        )
    return WebchatInboxReceipt(
        status_code=response.status_code,
        persisted=data.get("persisted") is True and data.get("task_id") == str(task_id),
        duplicate=data.get("duplicate") is True,
        error_code=code if isinstance(code, str) else None,
    )


def _json_object(response: httpx.Response) -> dict[str, Any] | None:
    try:
        payload = response.json()
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None
