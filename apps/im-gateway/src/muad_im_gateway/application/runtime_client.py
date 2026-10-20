from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol
from uuid import UUID

import httpx
from muad_api import AppError
from muad_api.context import current_request_id
from muad_api.error_codes import ErrorCode
from muad_api.security import INTERNAL_SERVICE_HEADER
from muad_common import SharedSettings
from muad_contracts import RunRequest
from muad_contracts.channel_delivery import (
    DeliveryAck,
    DeliveryAckReceipt,
    DeliveryLeaseGrant,
    DeliveryReservationRequest,
)
from muad_contracts.webchat import WebchatUploadRegistration

from .envelope import (
    decode_error_payload,
    decode_json,
    error_code_from_payload,
    error_data_from_payload,
    require_data_dict,
)

# SSE 类型的规范归属是 `.sse`；此处显式再导出，既有消费者（inbound/测试）导入路径不变
from .sse import SseEvent as SseEvent
from .sse import iter_sse_events as iter_sse_events

if TYPE_CHECKING:  # 目录模块 import 了本模块：只在类型检查期导入，运行时按需导入（见 _transport_failure）
    from ..metrics import GatewayPool

logger = logging.getLogger(__name__)

RUNS_PATH = "/v1/runs"
RUN_PATH = "/v1/runs/{run_id}"
RUN_CANCEL_PATH = "/v1/runs/{run_id}/cancel"
RUN_RESUME_PATH = "/v1/runs/{run_id}/resume"
RUN_EVENTS_PATH = "/v1/runs/{run_id}/events"
#: 打开公开流的一次归属解析（Run + 会话入口/Agent）：与 `RUN_PATH` + `INTERNAL_SCOPE_PATH` 同义。
RUN_SCOPE_PATH = "/v1/runs/{run_id}/scope"
CONVERSATIONS_PATH = "/v1/conversations"
#: 受信内部历史面（TASK-050）：入口显式的列表/详情/消息 + 公共路径的归属解析（TASK-052）。
INTERNAL_CONVERSATIONS_PATH = "/internal/webchat/entries/{entry_id}/conversations"
INTERNAL_CONVERSATION_PATH = "/internal/webchat/conversations/{conversation_id}"
INTERNAL_MESSAGES_PATH = "/internal/webchat/conversations/{conversation_id}/messages"
INTERNAL_SCOPE_PATH = "/internal/webchat/conversations/{conversation_id}/scope"
#: 受信暂存上传登记端口（TASK-058）：共享字节已写好，只登记权威元信息。
INTERNAL_UPLOADS_PATH = "/internal/webchat/uploads"
#: 受信附件解析端口（TASK-055）：`artifact_id` → 本主体名下会话的归属 + 渠道中立引用。
INTERNAL_ARTIFACT_PATH = "/internal/webchat/artifacts/{artifact_id}"
#: 受信活动收件箱端口（TASK-064/070）：按用户 feed seq 增量读取 + 显式已读。
INTERNAL_NOTIFICATIONS_PATH = "/internal/webchat/notifications"
INTERNAL_NOTIFICATION_READ_PATH = "/internal/webchat/notifications/{notification_id}/read"
CANCEL_ACTIVE_PATH = "/v1/runs/cancel-active"
#: 受信 API-18 协调端口（TASK-067）：Run 终态交付按稳定 delivery_key 的 reserve/ack。
CHANNEL_DELIVERY_RESERVE_PATH = "/internal/channel-notifications/reserve"
CHANNEL_DELIVERY_ACK_PATH = "/internal/channel-notifications/{notification_id}/ack"
REQUEST_TIMEOUT_SEC = 10.0
STREAM_TIMEOUT_SEC = 300.0
CALLER_SERVICE = "muad-im-gateway"


@dataclass(frozen=True, slots=True)
class RunAdmission:
    """一次提交的**受理裁决**（Owner 首帧）：真实 Run 身份 + 会话 + 是否重放/恢复。

    长流不在这里等待终态（RULE-01/02）：拿到它就是"已持久受理"，网关据此返回 202/回执。
    """

    run_id: UUID
    conversation_id: UUID | None
    resumed: bool


def _transport_failure(exc: httpx.HTTPError, pool: GatewayPool) -> AppError:
    """传输失败的统一分类：等池超时单独计数（连接预算压力可见），错误语义不变。

    `record_pool_wait` 在函数内导入：目录模块 import 了 `api.delivery` → `api.deps` →
    本模块，模块级导入会形成环（真实运行路径与静态类型不受影响）。
    """
    if isinstance(exc, httpx.PoolTimeout):
        from ..metrics import record_pool_wait

        record_pool_wait(pool)
    # 传输失败只计数不记日志会让 5xx 无法归因（等池超时之外还有读超时/连接失败）。
    logger.warning(
        "gateway_transport_failure",
        extra={"pool": pool, "error": type(exc).__name__},
    )
    return AppError(ErrorCode.COMMON_INTERNAL_ERROR)


class RuntimeClientPort(Protocol):
    def create_run(
        self,
        request: RunRequest,
        *,
        tenant_id: str,
        trace_id: str = "",
        idempotency_key: str | None = None,
    ) -> AsyncIterator[SseEvent]:
        """提交 Run 并订阅它的 SSE。

        **幂等身份**：`idempotency_key` 缺省就是 `request.message.id`——Gateway 的重投沿用**同一条
        消息的身份**（不另造键），Runtime 才能按幂等键回放原 Run（RULE-02）。
        """
        ...

    def open_events(
        self,
        run_id: str,
        *,
        tenant_id: str,
        after_seq: int = 0,
        trace_id: str = "",
    ) -> AsyncIterator[SseEvent]:
        """API-04 断流重连：按已确认 canonical seq tail 现有 Run，不重复 POST 创建。"""
        ...

    async def create_conversation(
        self,
        agent_id: UUID,
        platform_user_id: UUID,
        *,
        tenant_id: str = "",
        trace_id: str = "",
        idempotency_key: str | None = None,
    ) -> dict[str, Any]: ...

    async def cancel_active(
        self,
        agent_id: UUID,
        platform_user_id: UUID,
        *,
        tenant_id: str = "",
        trace_id: str = "",
    ) -> dict[str, Any]: ...


def build_headers(
    tenant_id: str,
    trace_id: str,
    *,
    idempotency_key: str | None = None,
) -> dict[str, str]:
    """链路头 + 稳定幂等键（设计 API-06：Gateway 传 channel message id）。"""
    headers = {"X-Caller-Service": CALLER_SERVICE}
    if tenant_id:
        headers["X-Tenant-Id"] = tenant_id
    if trace_id:
        headers["X-Trace-Id"] = trace_id
    request_id = current_request_id()
    if request_id:
        headers["X-Request-Id"] = request_id
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    return headers


def _read_scope(user_id: UUID, agent_id: UUID, entry_id: UUID) -> dict[str, str]:
    """入口显式读取的核属维度（与 TASK-050 内部端点的查询参数同形）。"""
    return {
        "user_id": str(user_id),
        "agent_id": str(agent_id),
        "entry_id": str(entry_id),
    }


def _admission_of(event: SseEvent) -> RunAdmission:
    """受理帧 → 强类型受理身份；缺 Run 身份的帧是坏封套，显式失败（不编造 run_id）。"""
    if event.run_id is None:
        raise AppError(ErrorCode.COMMON_INTERNAL_ERROR)
    try:
        run_id = UUID(event.run_id)
        raw_conversation = event.data.get("conversation_id")
        conversation_id = UUID(str(raw_conversation)) if raw_conversation else None
    except (ValueError, TypeError) as exc:
        raise AppError(ErrorCode.COMMON_INTERNAL_ERROR) from exc
    return RunAdmission(
        run_id=run_id,
        conversation_id=conversation_id,
        resumed=bool(event.data.get("resumed")),
    )



class RuntimeClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout_sec: float = REQUEST_TIMEOUT_SEC,
        transport: httpx.AsyncBaseTransport | None = None,
        service_token: str | None = None,
        client: httpx.AsyncClient | None = None,
        stream_client: httpx.AsyncClient | None = None,
        upload_client: httpx.AsyncClient | None = None,
    ) -> None:
        # 请求用绝对 URL 组装：注入的共享池客户端没有 base_url（一个池服务多个目标）。
        self._base_url = base_url.rstrip("/")
        # 装配根注入的分池由**装配根拥有并关闭**（本实例只借用）：业务出站（历史/资源/交付/
        # 产物解析）走 egress；SSE 长流独立池，不挤占控制/业务请求；上传登记走上传池。
        # 未注入时三类调用共用本实例自建自关的一个客户端（直构调用点语义不变）。
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout_sec, transport=transport)
        self._stream_client = stream_client or self._client
        self._upload_client = upload_client or self._client
        # 内部服务身份：受信内部历史面（`/internal/webchat/...`）要求携带；缺省取启动设置
        # （与 `ConsoleClient` 同一口径），调用方也可显式注入。
        self._service_token = (
            service_token if service_token is not None else SharedSettings().internal_service_token
        )

    def _url(self, path: str) -> str:
        return f"{self._base_url}{path}"

    async def create_run(
        self,
        request: RunRequest,
        *,
        tenant_id: str,
        trace_id: str = "",
        idempotency_key: str | None = None,
    ) -> AsyncIterator[SseEvent]:
        # 稳定幂等键：默认使用原 channel message id（可重试提交不重复建 Run）；
        # 调用方显式传键时以它为准（`RuntimeClientPort.create_run`）。
        headers = build_headers(
            tenant_id, trace_id, idempotency_key=idempotency_key or request.message.id
        )
        headers["Accept"] = "text/event-stream"
        try:
            async with self._stream_client.stream(
                "POST",
                self._url(RUNS_PATH),
                json=request.model_dump(mode="json"),
                headers=headers,
                timeout=httpx.Timeout(STREAM_TIMEOUT_SEC, connect=REQUEST_TIMEOUT_SEC),
            ) as response:
                if response.status_code >= 400:
                    payload = await decode_error_payload(response)
                    raise AppError(error_code_from_payload(payload))
                # 分片文本（非行原子）：解析器自行切行，跨 chunk 的帧不丢
                async for event in iter_sse_events(response.aiter_text()):
                    yield event
        except httpx.HTTPError as exc:
            raise _transport_failure(exc, "egress") from exc

    async def open_events(
        self,
        run_id: str,
        *,
        tenant_id: str,
        after_seq: int = 0,
        trace_id: str = "",
    ) -> AsyncIterator[SseEvent]:
        """按已确认 seq 重连既有 Run 的持久事件流；不创建 Run、不重复执行。"""
        headers = build_headers(tenant_id, trace_id)
        headers["Accept"] = "text/event-stream"
        try:
            async with self._stream_client.stream(
                "GET",
                self._url(RUN_EVENTS_PATH.format(run_id=run_id)),
                params={"after_seq": str(max(0, after_seq))},
                headers=headers,
                timeout=httpx.Timeout(STREAM_TIMEOUT_SEC, connect=REQUEST_TIMEOUT_SEC),
            ) as response:
                if response.status_code >= 400:
                    payload = await decode_error_payload(response)
                    raise AppError(error_code_from_payload(payload))
                async for event in iter_sse_events(response.aiter_text()):
                    yield event
        except httpx.HTTPError as exc:
            raise _transport_failure(exc, "egress") from exc

    @asynccontextmanager
    async def open_event_stream(
        self,
        run_id: UUID,
        *,
        tenant_id: str,
        after_seq: int = 0,
        trace_id: str = "",
    ) -> AsyncIterator[AsyncIterator[SseEvent]]:
        """**急切打开**的持久事件流：进入上下文时就完成请求并裁决状态码。

        公开 SSE（API-07）必须在响应头下发**之前**拿到游标分型（`CURSOR_EXPIRED`/`CURSOR_AHEAD`
        是 HTTP 错误封套，不能塞进已开始的流）；因此这里在 `__aenter__` 阶段就完成请求与状态码
        裁决，错误码连同 Owner 的游标窗口数据原样上抛，绝不夹取、不补造窗口字段。
        """
        headers = build_headers(tenant_id, trace_id)
        headers["Accept"] = "text/event-stream"
        try:
            async with self._stream_client.stream(
                "GET",
                self._url(RUN_EVENTS_PATH.format(run_id=run_id)),
                params={"after_seq": str(max(0, after_seq))},
                headers=headers,
                timeout=httpx.Timeout(STREAM_TIMEOUT_SEC, connect=REQUEST_TIMEOUT_SEC),
            ) as response:
                if response.status_code >= 400:
                    payload = await decode_error_payload(response)
                    raise AppError(
                        error_code_from_payload(payload), data=error_data_from_payload(payload)
                    )
                yield iter_sse_events(response.aiter_text())
        except httpx.HTTPError as exc:
            raise _transport_failure(exc, "egress") from exc

    async def create_conversation(
        self,
        agent_id: UUID,
        platform_user_id: UUID,
        *,
        tenant_id: str = "",
        trace_id: str = "",
        idempotency_key: str | None = None,
        entry_id: UUID | None = None,
        external_thread_id: str = "",
    ) -> dict[str, Any]:
        """新建会话（API-08）：可归属入口（`entry_id`）随会话冻结入口归属并登记当前指针。

        入口/线程字段**只在显式给出时上送**：既有 IM 调用点（无入口事实）的请求体保持原形，
        不把"不可归属"编造成一个入口身份。
        """
        payload: dict[str, Any] = {
            "agent_id": str(agent_id),
            "platform_user_id": str(platform_user_id),
        }
        if entry_id is not None:
            payload["entry_id"] = str(entry_id)
        if external_thread_id:
            payload["external_thread_id"] = external_thread_id
        return await self._post_json(
            CONVERSATIONS_PATH, payload, tenant_id, trace_id, idempotency_key=idempotency_key
        )

    async def submit_run(
        self,
        request: RunRequest,
        *,
        tenant_id: str,
        idempotency_key: str,
    ) -> RunAdmission:
        """API-06 提交：等到**受理裁决**（Owner 首帧）即返回，不持有长流（RULE-01/02）。

        执行由 Runtime 的监督槽驱动（HTTP 断开不取消执行）；后续正文/阶段由公开 SSE
        （API-07）按 Run 身份另订，网关不在这里订阅整段流。
        """
        return await self._admit_stream(
            "POST",
            RUNS_PATH,
            request.model_dump(mode="json"),
            tenant_id=tenant_id,
            idempotency_key=idempotency_key,
        )

    async def resume_run(
        self,
        run_id: UUID,
        payload: dict[str, Any],
        *,
        tenant_id: str,
        idempotency_key: str,
    ) -> RunAdmission:
        """API-08 恢复：显式绑定原 Run/submission/source；同样只等到受理裁决。"""
        return await self._admit_stream(
            "POST",
            RUN_RESUME_PATH.format(run_id=run_id),
            payload,
            tenant_id=tenant_id,
            idempotency_key=idempotency_key,
        )

    async def get_run(self, run_id: UUID, *, tenant_id: str) -> dict[str, Any]:
        """Run 详情（状态 + 持久阶段投影 + 归属）：取消/恢复的归属核验与受理回执共用。"""
        return await self._get_json(RUN_PATH.format(run_id=run_id), tenant_id)

    async def get_run_scope(
        self, run_id: UUID, *, tenant_id: str, user_id: UUID
    ) -> dict[str, Any]:
        """打开公开流的一次归属解析：Run + 会话入口/Agent（**流生命周期**，走 stream 池）。

        `user_id` 必须与 Run 的归属主体一致；会话入口/Agent 由 Owner 从 Run 行的会话导出，
        网关不能拿任意 `conversation_id` 做入口解析。
        """
        return await self._get_json(
            RUN_SCOPE_PATH.format(run_id=run_id),
            tenant_id,
            params={"user_id": str(user_id)},
            client=self._stream_client,
            internal=True,
        )

    async def cancel_run(self, run_id: UUID, *, tenant_id: str) -> dict[str, Any]:
        """API-08 精确取消：按原 Run 定位（可重放），不猜"最新 Run"。"""
        return await self._post_json(
            RUN_CANCEL_PATH.format(run_id=run_id), {}, tenant_id, ""
        )

    async def list_conversations(
        self,
        entry_id: UUID,
        *,
        tenant_id: str,
        user_id: UUID,
        agent_id: UUID,
        page: int,
        page_size: int | None,
    ) -> dict[str, Any]:
        """API-14 入口会话列表（内部受信面；入口显式）。"""
        params = {
            "user_id": str(user_id),
            "agent_id": str(agent_id),
            "page": str(page),
        }
        if page_size is not None:
            params["page_size"] = str(page_size)
        return await self._get_internal_json(
            INTERNAL_CONVERSATIONS_PATH.format(entry_id=entry_id), tenant_id, params
        )

    async def get_conversation(
        self,
        conversation_id: UUID,
        *,
        tenant_id: str,
        user_id: UUID,
        agent_id: UUID,
        entry_id: UUID,
    ) -> dict[str, Any]:
        """API-15 会话详情（入口显式核属后的公开投影）。"""
        return await self._get_internal_json(
            INTERNAL_CONVERSATION_PATH.format(conversation_id=conversation_id),
            tenant_id,
            _read_scope(user_id, agent_id, entry_id),
        )

    async def list_messages(
        self,
        conversation_id: UUID,
        *,
        tenant_id: str,
        user_id: UUID,
        agent_id: UUID,
        entry_id: UUID,
        page: int,
        page_size: int | None,
        through_seq: int | None,
    ) -> dict[str, Any]:
        """API-09 固定水位分页（首屏冻结、后页沿用；游标语义由 Owner 裁决）。"""
        params = {**_read_scope(user_id, agent_id, entry_id), "page": str(page)}
        if page_size is not None:
            params["page_size"] = str(page_size)
        if through_seq is not None:
            params["through_seq"] = str(through_seq)
        return await self._get_internal_json(
            INTERNAL_MESSAGES_PATH.format(conversation_id=conversation_id), tenant_id, params
        )

    async def resolve_conversation_scope(
        self, conversation_id: UUID, *, tenant_id: str, user_id: UUID
    ) -> dict[str, Any]:
        """公共路径的归属解析（API-09/15）：只带受信主体，入口/Agent 由 Owner 从行导出。"""
        return await self._get_internal_json(
            INTERNAL_SCOPE_PATH.format(conversation_id=conversation_id),
            tenant_id,
            {"user_id": str(user_id)},
        )

    async def register_upload(
        self, registration: WebchatUploadRegistration, *, tenant_id: str
    ) -> dict[str, Any]:
        """API-10/API-12 暂存上传登记：共享字节已写好，受信端口只登记权威元信息。

        身份三件套（user/agent/entry）由受验证主体与入口解析填入，浏览器自报字段没有位置；
        响应只回 `upload_id` 与权威元信息（无存储键、无预造制品引用）。
        """
        return await self._post_internal_json(
            INTERNAL_UPLOADS_PATH,
            registration.model_dump(mode="json"),
            tenant_id,
            client=self._upload_client,
        )

    async def resolve_webchat_artifact(
        self, artifact_id: UUID, *, tenant_id: str, user_id: UUID
    ) -> dict[str, Any]:
        """API-10 下载/预览的归属解析（TASK-055）：只带受信主体，入口/Agent 由 Owner 从会话行导出。

        响应含渠道中立引用（存储键只在受信面传递）；跨租户/他主体/已失效由 Owner 判为 404。
        """
        return await self._get_internal_json(
            INTERNAL_ARTIFACT_PATH.format(artifact_id=artifact_id),
            tenant_id,
            {"user_id": str(user_id)},
        )

    async def list_notifications(
        self,
        user_id: UUID,
        *,
        tenant_id: str,
        after_seq: int,
        page: int,
        page_size: int | None,
    ) -> dict[str, Any]:
        """API-11 增量读取（TASK-070）：主体由已验证 CHAT 身份显式携带，游标原样交 Owner 裁决。

        `CURSOR_AHEAD` 连同 Owner 的真实 feed 水位原样上抛（不静默夹取成空页）；页大小缺省时
        由 Owner 从平台设置派生，网关不硬编码第二份默认。
        """
        params = {
            "user_id": str(user_id),
            "after_seq": str(after_seq),
            "page": str(page),
        }
        if page_size is not None:
            params["page_size"] = str(page_size)
        return await self._get_internal_json(
            INTERNAL_NOTIFICATIONS_PATH, tenant_id, params, with_error_data=True
        )

    async def mark_notification_read(
        self, notification_id: UUID, *, tenant_id: str, user_id: UUID
    ) -> dict[str, Any]:
        """API-11 显式已读（TASK-070）：幂等；只标记明确读过且属于受信主体的行（他主体 404）。"""
        return await self._post_internal_json(
            INTERNAL_NOTIFICATION_READ_PATH.format(notification_id=notification_id),
            {},
            tenant_id,
            params={"user_id": str(user_id)},
        )

    async def reserve_delivery(
        self, request: DeliveryReservationRequest, *, tenant_id: str
    ) -> DeliveryLeaseGrant:
        """API-18 预占（TASK-067）：按稳定 `delivery_key` 取租约；已送达重放冻结的首次结果。

        网关不持库：Run 终态交付的同键协调只经这条受信 HTTP 端口（服务身份 + 部署租户）。
        """
        data = await self._post_internal_json(
            CHANNEL_DELIVERY_RESERVE_PATH, request.model_dump(mode="json"), tenant_id
        )
        return DeliveryLeaseGrant.model_validate(data)

    async def ack_delivery(
        self, notification_id: UUID, body: DeliveryAck, *, tenant_id: str
    ) -> DeliveryAckReceipt:
        """API-18 确认：实际发送成功才写 `DELIVERED`；失约/响应丢失显式失败（可追溯重试）。"""
        data = await self._post_internal_json(
            CHANNEL_DELIVERY_ACK_PATH.format(notification_id=notification_id),
            body.model_dump(mode="json"),
            tenant_id,
        )
        return DeliveryAckReceipt.model_validate(data)

    async def _admit_stream(
        self,
        method: str,
        path: str,
        payload: dict[str, Any],
        *,
        tenant_id: str,
        idempotency_key: str,
    ) -> RunAdmission:
        """受理流：读到**首帧**（受理裁决）即返回并关闭读取流；非 2xx 按 Owner 错误码上抛。"""
        headers = build_headers(tenant_id, "", idempotency_key=idempotency_key)
        headers["Accept"] = "text/event-stream"
        try:
            async with self._stream_client.stream(
                method,
                self._url(path),
                json=payload,
                headers=headers,
                timeout=httpx.Timeout(STREAM_TIMEOUT_SEC, connect=REQUEST_TIMEOUT_SEC),
            ) as response:
                if response.status_code >= 400:
                    error_payload = await decode_error_payload(response)
                    raise AppError(error_code_from_payload(error_payload))
                async for event in iter_sse_events(response.aiter_text()):
                    return _admission_of(event)
        except httpx.HTTPError as exc:
            raise _transport_failure(exc, "stream") from exc
        # 流已结束却没有受理帧：没有可证实的受理事实，显式失败（不编造 run_id）。
        raise AppError(ErrorCode.COMMON_INTERNAL_ERROR)

    async def _get_json(
        self,
        path: str,
        tenant_id: str,
        *,
        params: dict[str, str] | None = None,
        client: httpx.AsyncClient | None = None,
        internal: bool = False,
    ) -> dict[str, Any]:
        headers = build_headers(tenant_id, "")
        if internal and self._service_token:
            headers[INTERNAL_SERVICE_HEADER] = self._service_token
        pool: GatewayPool = "stream" if client is self._stream_client else "egress"
        try:
            response = await (client or self._client).get(
                self._url(path), params=params, headers=headers
            )
        except httpx.HTTPError as exc:
            raise _transport_failure(exc, pool) from exc
        body = decode_json(response)
        if response.status_code >= 400:
            code = error_code_from_payload(body)
            logger.warning(
                "gateway_owner_http_error",
                extra={"path": path, "status": response.status_code, "code": code},
            )
            raise AppError(code)
        return require_data_dict(body)

    async def _get_internal_json(
        self,
        path: str,
        tenant_id: str,
        params: dict[str, str],
        *,
        with_error_data: bool = False,
    ) -> dict[str, Any]:
        """受信内部面：服务身份 + 部署租户（与 Console 内部端点同一门控口径）。

        `with_error_data`：游标类错误（`CURSOR_AHEAD` 等）必须携带 Owner 的真实窗口/水位数据
        原样上抛（公开面据此显式返回，不补造、不夹取）；普通读取错误不带数据。
        """
        headers = build_headers(tenant_id, "")
        if self._service_token:
            headers[INTERNAL_SERVICE_HEADER] = self._service_token
        try:
            response = await self._client.get(
                self._url(path), params=params, headers=headers
            )
        except httpx.HTTPError as exc:
            raise _transport_failure(exc, "egress") from exc
        body = decode_json(response)
        if response.status_code >= 400:
            data = error_data_from_payload(body) if with_error_data else None
            raise AppError(error_code_from_payload(body), data=data)
        return require_data_dict(body)

    async def _post_internal_json(
        self,
        path: str,
        payload: dict[str, Any],
        tenant_id: str,
        *,
        client: httpx.AsyncClient | None = None,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """受信内部面写调用：与读取面同一门控（服务身份 + 部署租户）。"""
        headers = build_headers(tenant_id, "")
        if self._service_token:
            headers[INTERNAL_SERVICE_HEADER] = self._service_token
        try:
            response = await (client or self._client).post(
                self._url(path), json=payload, params=params, headers=headers
            )
        except httpx.HTTPError as exc:
            raise _transport_failure(exc, "egress") from exc
        body = decode_json(response)
        if response.status_code >= 400:
            raise AppError(error_code_from_payload(body))
        return require_data_dict(body)

    async def cancel_active(
        self,
        agent_id: UUID,
        platform_user_id: UUID,
        *,
        tenant_id: str = "",
        trace_id: str = "",
    ) -> dict[str, Any]:
        payload = {"agent_id": str(agent_id), "platform_user_id": str(platform_user_id)}
        return await self._post_json(CANCEL_ACTIVE_PATH, payload, tenant_id, trace_id)

    async def _post_json(
        self,
        path: str,
        payload: dict[str, Any],
        tenant_id: str,
        trace_id: str,
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        headers = build_headers(tenant_id, trace_id, idempotency_key=idempotency_key)
        try:
            response = await self._client.post(self._url(path), json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise _transport_failure(exc, "egress") from exc
        body = decode_json(response)
        if response.status_code >= 400:
            raise AppError(error_code_from_payload(body))
        return require_data_dict(body)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
