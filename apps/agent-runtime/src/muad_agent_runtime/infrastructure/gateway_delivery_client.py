"""运行期把产物交给用户的客户端（设计 §3.6 跳 2）。

**为什么是同步调用而不是发 SSE 事件**（设计 §3.1 ADR）：网关是 SSE 的**纯消费方**——
runtime 只有 `/runs`、`/resume`、`/cancel`、`GET /runs/{id}`，**没有回执通道**。异步化会让
模型拿不到交付结论，"失败可重试 / 降级为链接"都无法诚实表达（RULE-03）。

**超时按失败（未知）处理，绝不拆成成功**：超时只说明**没拿到结论**，不说明没发出去。
重试的安全由 `(tenant_id, artifact_id, route_key)` 唯一键兜底（同一产物对同一路由只交付一次）。

**一条交付契约两个调用方**：本客户端与既有 worker 投递打同一个 `/internal/deliveries`，
差别只在 `delivery_key` 的形态（`run:{run_id}:{artifact_id}` vs `task:{task_id}:final`）。

> 该端点目前**没有鉴权**（既有缺口，本期不修）——所以这里不带内部服务令牌；
> 这是记录在案的现状，不是本模块的选择。
"""

from __future__ import annotations

import httpx
from muad_contracts import DeliveryRequest, DeliveryResponse

DELIVERIES_PATH = "/internal/deliveries"
#: 交付调用的**独立超时**。比 resolve（5s）长，因为网关那边要真的把文件传上去；
#: 但仍然有界 —— 无界等待会把 Run 挂死，而"等不到"必须能变成"失败可重试"。
DELIVERY_TIMEOUT_SEC = 30.0


class DeliveryUnavailableError(RuntimeError):
    """交付**没拿到结论**：超时、传输错误，或没配网关地址。

    调用方**必须按失败处理**：`不确定`不等于`没发出去`，更不等于`发出去了`。
    把它当成功就是 RULE-03 禁的谎报；当"确定失败"也不对——但重试是安全的（幂等键兜底），
    所以按"失败可重试"报是唯一诚实且可用的选择。
    """


class GatewayDeliveryClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout_sec: float = DELIVERY_TIMEOUT_SEC,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout_sec, transport=transport)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def deliver(self, request: DeliveryRequest, *, trace_id: str = "") -> DeliveryResponse:
        """同步投递并拿回**真实结论**。任何没拿到结论的情形都抛 `DeliveryUnavailableError`。"""
        headers = {"X-Trace-Id": trace_id} if trace_id else {}
        try:
            response = await self._client.post(
                DELIVERIES_PATH, json=request.model_dump(mode="json"), headers=headers
            )
        except httpx.HTTPError as exc:
            # 超时与传输错误同路：都没拿到结论 ⇒ 未知 ⇒ 失败可重试
            raise DeliveryUnavailableError(type(exc).__name__) from exc
        if response.status_code != 200:
            # 网关显式拒绝（如 BOT_NOT_FOUND / ARTIFACT_DELIVERY_FAILED）：这是**确定的失败**
            raise _error_from(response)
        payload = response.json()
        data = payload.get("data")
        if not isinstance(data, dict):
            raise DeliveryUnavailableError("malformed_delivery_response")
        return DeliveryResponse.model_validate(data)


def _error_from(response: httpx.Response) -> DeliveryUnavailableError:
    """把网关的封套错误映射成调用方能用的原因码（**不带正文**，正文可能含渠道细节）。"""
    code = "ARTIFACT_DELIVERY_FAILED"
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    candidate = payload.get("code") if isinstance(payload, dict) else None
    if isinstance(candidate, str) and candidate:
        code = candidate
    return DeliveryUnavailableError(code)
