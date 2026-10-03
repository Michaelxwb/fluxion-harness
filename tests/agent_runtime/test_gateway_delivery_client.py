"""[TASK-006] runtime 交付客户端：**没拿到结论 ≠ 没发出去**。

打真实 HTTP（`httpx.MockTransport` 提供协议层，断言落在**真实响应/异常**上，不 mock 请求）。

这条边界是 RULE-03 的要害：超时只说明没拿到结论，不说明没发出去。把它当成功是谎报，
把它当"确定失败"也不对——但重试是安全的（幂等键 `(tenant_id, artifact_id, route_key)` 兜底），
所以按"失败可重试"报是唯一诚实且可用的选择。
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from muad_agent_runtime.infrastructure.gateway_delivery_client import (
    DELIVERIES_PATH,
    DeliveryUnavailableError,
    GatewayDeliveryClient,
)
from muad_contracts import DeliveryMessage, DeliveryRequest, DeliveryRouteInput

ROUTE = DeliveryRouteInput(channel="WECOM", bot_id="bot-1", external_user_id="ext-1")


def _request() -> DeliveryRequest:
    artifact_id = uuid.uuid4()
    return DeliveryRequest(
        delivery_key=f"run:{uuid.uuid4()}:{artifact_id}",
        route=ROUTE,
        message=DeliveryMessage(type="text", text="x"),
    )


def _client(handler: Any) -> GatewayDeliveryClient:
    return GatewayDeliveryClient(
        "http://gateway.test", transport=httpx.MockTransport(handler)
    )


async def test_successful_delivery_returns_the_real_outcome() -> None:
    """200 且封套齐全 ⇒ 回传真实结局（含降级链接），模型据此改口径。"""
    link = "https://console.invalid/api/v1/artifacts/x/content?token=t"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == DELIVERIES_PATH
        return httpx.Response(
            200,
            json={
                "code": "0",
                "data": {
                    "accepted": True,
                    "delivered": True,
                    "deduplicated": False,
                    "outcome": "DEGRADED",
                    "fallback_url": link,
                },
            },
        )

    response = await _client(handler).deliver(_request())

    assert response.outcome == "DEGRADED"
    assert response.fallback_url == link


async def test_gateway_rejection_becomes_a_delivery_failure() -> None:
    """网关显式拒绝（502 + 封套码）⇒ 抛出**带原因码**的失败，不外泄响应正文。"""
    client = _client(
        lambda _request: httpx.Response(502, json={"code": "ARTIFACT_DELIVERY_FAILED", "msg": "x"})
    )

    with pytest.raises(DeliveryUnavailableError) as exc:
        await client.deliver(_request())

    assert str(exc.value) == "ARTIFACT_DELIVERY_FAILED"


async def test_transport_error_is_a_failure_not_a_success() -> None:
    """传输层炸了（连不上/超时）⇒ **必须**按失败报，绝不能当成功。"""

    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow")

    with pytest.raises(DeliveryUnavailableError):
        await _client(handler).deliver(_request())


async def test_malformed_success_payload_is_a_failure() -> None:
    """200 但封套缺 `data` ⇒ 当失败。**宁可重试**，也不拿一个残缺响应编出"已交付"。"""
    client = _client(lambda _request: httpx.Response(200, json={"code": "0"}))

    with pytest.raises(DeliveryUnavailableError):
        await client.deliver(_request())
