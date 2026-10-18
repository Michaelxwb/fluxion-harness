"""运行期把产物交给用户的客户端（设计 §3.6 跳 2）。

**为什么是同步调用而不是发 SSE 事件**（设计 §3.1 ADR）：网关是 SSE 的**纯消费方**——
runtime 只有 `/runs`、`/resume`、`/cancel`、`GET /runs/{id}`，**没有回执通道**。异步化会让
模型拿不到交付结论，"失败可重试 / 降级为链接"都无法诚实表达（RULE-03）。

**超时按失败（未知）处理，绝不拆成成功**：超时只说明**没拿到结论**，不说明没发出去。
重试的安全由 `(tenant_id, artifact_id, route_key)` 唯一键兜底（同一产物对同一路由只交付一次）。

**"没拿到结论"与"确定没发出去"分开**（2026-10-18，TASK-066）：连接从未建立或网关显式拒绝时
抛 `DeliveryNotSentError`（确定没送达 ⇒ 可安全重试）；连接之后的超时/传输错误抛
`DeliveryUnavailableError`（可能已经发出去 ⇒ 只能保留核实）。投递泵按这个区分决定
`NOT_SENT`（退避重投）还是 `UNKNOWN`（保留核实），不再把两者混成一个"失败"。

**一条交付契约两个调用方**：本客户端与既有 worker 投递打同一个 `/internal/deliveries`，
差别只在 `delivery_key` 的形态（`run:{run_id}:{artifact_id}` vs `task:{task_id}:final`）。

> 2026-10-06：该端点**已加服务身份门控**（`X-Internal-Service`），本客户端随之带上内部服务令牌
> —— 与 Console 的 `resolve-definition`/`resolve-credentials` 同一口径。此前它无鉴权、且完全
> 相信请求体里自带的 `storage_key`（可被用来转发别的租户的产物）。
"""

from __future__ import annotations

import httpx
from muad_api.security import INTERNAL_SERVICE_HEADER
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


class DeliveryNotSentError(DeliveryUnavailableError):
    """**确定没发出去**：连接没建立（拒绝/DNS/池超时）或网关明确拒绝（非 200）。

    与"没拿到结论"分开是**投递事实**的要求（design §3.3/§3.6）：确定没发出去的可以安全重试
    （`NOT_SENT` 退避重投），而"可能已经发出去"的只能保留核实（`UNKNOWN`，不自动重发）。
    子类关系保证既有调用方（按 `DeliveryUnavailableError` 处理"失败可重试"）行为不变。
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class GatewayDeliveryClient:
    def __init__(
        self,
        base_url: str,
        *,
        service_token: str | None = None,
        timeout_sec: float = DELIVERY_TIMEOUT_SEC,
        transport: httpx.AsyncBaseTransport | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._service_token = service_token
        # 请求用绝对 URL 组装：注入的共享池客户端没有 base_url（一个池服务多个目标）。
        self._base_url = base_url.rstrip("/")
        # 装配根注入的业务池由**装配根拥有并关闭**（本实例只借用）；未注入时自建自关。
        self._owns_client = client is None
        # 内部服务调用**不**走环境代理：`trust_env` 会把 `HTTP_PROXY` 一类的部署环境变量
        # 变成内部投递的隐形路由（实测：本机代理把 127.0.0.1 的交付变成读超时），
        # 与 `main.py` 的控制面客户端同一口径。
        self._client = client or httpx.AsyncClient(
            base_url=base_url, timeout=timeout_sec, transport=transport, trust_env=False
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def deliver(self, request: DeliveryRequest, *, trace_id: str = "") -> DeliveryResponse:
        """同步投递并拿回**真实结论**。任何没拿到结论的情形都抛 `DeliveryUnavailableError`。"""
        headers = {"X-Trace-Id": trace_id} if trace_id else {}
        if self._service_token:
            headers[INTERNAL_SERVICE_HEADER] = self._service_token
        try:
            response = await self._client.post(
                f"{self._base_url}{DELIVERIES_PATH}",
                json=request.model_dump(mode="json"),
                headers=headers,
            )
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
            # 连接从未建立（拒绝/DNS/池超时）：请求没有发出去 ⇒ 确定没送达，可安全重试。
            raise DeliveryNotSentError(type(exc).__name__) from exc
        except httpx.HTTPError as exc:
            # 连接之后的超时/传输错误：都没拿到结论 ⇒ 未知 ⇒ 失败可重试（但不得声称未发送）。
            raise DeliveryUnavailableError(type(exc).__name__) from exc
        if response.status_code != 200:
            # 网关显式拒绝（如 BOT_NOT_FOUND / ARTIFACT_DELIVERY_FAILED）：这是**确定的失败**
            raise _error_from(response)
        payload = response.json()
        data = payload.get("data")
        if not isinstance(data, dict):
            raise DeliveryUnavailableError("malformed_delivery_response")
        return DeliveryResponse.model_validate(data)


def _error_from(response: httpx.Response) -> DeliveryNotSentError:
    """把网关的封套错误映射成调用方能用的原因码（**不带正文**，正文可能含渠道细节）。

    **唯一例外是入参校验错误**：422 的 `data.errors` 里只有**我们自己契约的字段名**（没有渠道
    形状、没有凭据），而"校验失败"这个码本身把排查成本拉满——2026-10-03 实测过一次：
    会话内交付拿到 `COMMON_VALIDATION_ERROR`，工具结果里既没有字段名也没有网关响应体，
    只能靠翻网关日志定位。把字段名留下来，下一次同样的问题一眼可见。
    """
    code = "ARTIFACT_DELIVERY_FAILED"
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    candidate = payload.get("code") if isinstance(payload, dict) else None
    if isinstance(candidate, str) and candidate:
        code = candidate
    if code == "COMMON_VALIDATION_ERROR" and isinstance(payload, dict):
        data = payload.get("data")
        fields = [
            ", ".join(str(part) for part in item.get("loc", ()) if part != "body")
            for item in (data.get("errors") if isinstance(data, dict) else None) or []
            if isinstance(item, dict)
        ]
        if fields:
            return DeliveryNotSentError(f"{code}({', '.join(sorted(set(fields)))})")
    return DeliveryNotSentError(code)
