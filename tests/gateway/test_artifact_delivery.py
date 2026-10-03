"""[TASK-006] 出站产物交付（E-06）：网关交付端点的**产物形态**与渠道侧失败注入。

网关不持库、也不认渠道的发送体：它只问适配器「你能不能把这个产物发出去」（**可选出站能力**），
由适配器决定直发还是降级为取件链接。本文件先钉住**分发与失败语义**这一层；
「失败 → 重试成功 → 审计同一行转 DELIVERED → 用户恰好收到一次」的完整链路在 E-06 收口时补齐。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fakes import FakeConsoleClient
from httpx import ASGITransport, AsyncClient
from muad_contracts import AttachmentRef
from muad_im_gateway.api.deps import get_console_client, get_dedupe_store, get_registry
from muad_im_gateway.channels.base import (
    ARTIFACT_DEGRADED,
    ARTIFACT_DELIVERED,
    ArtifactDeliveryError,
    ArtifactDeliveryOutcome,
    ChannelRegistry,
)
from muad_im_gateway.channels.fake import FakeChannelAdapter
from muad_im_gateway.infrastructure.dedupe import InMemoryDedupeStore
from muad_im_gateway.main import app

DELIVERIES_URL = "/internal/deliveries"
ROUTE = {"channel": "WECOM", "bot_id": "bot-1", "external_user_id": "ext-1"}


class _ArtifactAdapter(FakeChannelAdapter):
    """实现了**可选出站能力**的适配器：记录收到的引用，按脚本返回结局。"""

    def __init__(self, outcome: ArtifactDeliveryOutcome) -> None:
        super().__init__()
        self.outcome = outcome
        self.received: list[tuple[Any, AttachmentRef]] = []

    def route_key(self, route: Any) -> str:
        return f"{route.bot_id}:{route.external_user_id}"

    async def deliver_artifact(self, route: Any, artifact: AttachmentRef) -> ArtifactDeliveryOutcome:
        self.received.append((route, artifact))
        return self.outcome


class _FailingArtifactAdapter(FakeChannelAdapter):
    """渠道侧发不出去（含上传失败）：**必须翻译成渠道中立的 `ArtifactDeliveryError`**。"""

    def route_key(self, route: Any) -> str:
        return f"{route.bot_id}:{route.external_user_id}"

    async def deliver_artifact(self, route: Any, artifact: AttachmentRef) -> ArtifactDeliveryOutcome:
        raise ArtifactDeliveryError("ARTIFACT_DELIVERY_FAILED")


@asynccontextmanager
async def _client(
    adapter: FakeChannelAdapter,
    dedupe: InMemoryDedupeStore | None = None,
    console: FakeConsoleClient | None = None,
) -> AsyncIterator[AsyncClient]:
    registry = ChannelRegistry()
    registry.register(adapter)
    app.dependency_overrides[get_registry] = lambda: registry
    app.dependency_overrides[get_dedupe_store] = lambda: dedupe or InMemoryDedupeStore()
    app.dependency_overrides[get_console_client] = lambda: console or FakeConsoleClient()
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
    finally:
        app.dependency_overrides.clear()


def _artifact(artifact_id: uuid.UUID) -> AttachmentRef:
    return AttachmentRef(
        storage_key=f"outbound/run-1/{artifact_id}/v1",
        kind="DOCUMENT",
        media_type="text/markdown",
        size=12,
        filename="汇总.md",
        checksum="sha256:" + "0" * 64,
        artifact_id=artifact_id,
    )


def _body(artifact_id: uuid.UUID) -> dict[str, Any]:
    return {
        "tenant_id": "tenant-1",
        "delivery_key": f"run:{uuid.uuid4()}:{artifact_id}",
        "route": ROUTE,
        "message": {"type": "artifact", "artifact": _artifact(artifact_id).model_dump(mode="json")},
    }


async def test_artifact_message_goes_through_the_optional_capability() -> None:
    """产物形态**不走文本 send**，而是交给适配器的可选出站能力，并把结局回传调用方。"""
    artifact_id = uuid.uuid4()
    adapter = _ArtifactAdapter(ArtifactDeliveryOutcome(outcome=ARTIFACT_DELIVERED))

    async with _client(adapter) as client:
        response = await client.post(DELIVERIES_URL, json=_body(artifact_id))

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["outcome"] == ARTIFACT_DELIVERED
    assert data["fallback_url"] is None
    assert not adapter.sent, "产物形态不得退化成一段文本"

    assert len(adapter.received) == 1
    _route, reference = adapter.received[0]
    assert reference.artifact_id == artifact_id, "适配器必须拿到 artifact_id：降级直链要它来拼"
    assert reference.source_channel is None, "出站方向没有「来源渠道」这个概念"


async def test_degraded_delivery_is_reported_as_delivered_with_the_link() -> None:
    """降级**不是失败**：用户确实收到了东西（取件链接）。当失败报会让模型对用户说谎。"""
    artifact_id = uuid.uuid4()
    link = "https://console.invalid/api/v1/artifacts/x/content?token=t"
    adapter = _ArtifactAdapter(
        ArtifactDeliveryOutcome(
            outcome=ARTIFACT_DEGRADED, fallback_url=link, reason_code="UNSUPPORTED_MEDIA"
        )
    )

    async with _client(adapter) as client:
        response = await client.post(DELIVERIES_URL, json=_body(artifact_id))

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["delivered"] is True, "降级时用户确实收到了取件链接，不得当失败"
    assert data["outcome"] == ARTIFACT_DEGRADED
    assert data["fallback_url"] == link, "链接要回传给模型，它得转达给用户"


async def test_adapter_without_the_capability_fails_explicitly() -> None:
    """没实现可选出站能力 = 本通道不会发产物 ⇒ **显式失败**，绝不悄悄退化成一段文字。"""
    adapter = FakeChannelAdapter()

    async with _client(adapter) as client:
        response = await client.post(DELIVERIES_URL, json=_body(uuid.uuid4()))

    assert response.status_code == 502, response.text
    assert response.json()["code"] == "ARTIFACT_DELIVERY_FAILED"
    assert not adapter.sent, "不得把产物当文本发出去 —— 那会让用户以为文件发了"


async def test_channel_side_failure_releases_the_placeholder_so_a_retry_can_send() -> None:
    """渠道失败必须**释放占位**，否则重试被占位挡住 —— 那是 E-06 重试链的前提。"""
    artifact_id = uuid.uuid4()
    body = _body(artifact_id)
    dedupe = InMemoryDedupeStore()

    async with _client(_FailingArtifactAdapter(), dedupe) as client:
        first = await client.post(DELIVERIES_URL, json=body)
    assert first.status_code == 502, first.text
    assert first.json()["code"] == "ARTIFACT_DELIVERY_FAILED"

    # 同一个 delivery_key 重试：占位若没释放，这里会被判成"在处理中"而拿不到 200
    ok_adapter = _ArtifactAdapter(ArtifactDeliveryOutcome(outcome=ARTIFACT_DELIVERED))
    async with _client(ok_adapter, dedupe) as client:
        second = await client.post(DELIVERIES_URL, json=body)

    assert second.status_code == 200, second.text
    assert len(ok_adapter.received) == 1, "重试必须真的再发一次"


# --------------------------------------------------------------------- 交付审计


async def test_successful_delivery_writes_one_audit_row() -> None:
    """交付成功 → 审计落一行 `DELIVERED`，且带上**适配器产出的 route_key**。

    `route_key` 必须是适配器给的：应用层自己拼 `{bot_id}:{user}` 就是把渠道形状写进了
    应用层（RULE-im-002），接 web chat 时那一列会当场填不出真值。
    """
    artifact_id = uuid.uuid4()
    adapter = _ArtifactAdapter(ArtifactDeliveryOutcome(outcome=ARTIFACT_DELIVERED))
    console = FakeConsoleClient()

    async with _client(adapter, console=console) as client:
        response = await client.post(DELIVERIES_URL, json=_body(artifact_id))

    assert response.status_code == 200, response.text
    assert len(console.delivery_audit_calls) == 1
    request, tenant_id = console.delivery_audit_calls[0]
    assert request.artifact_id == artifact_id
    assert request.outcome == "DELIVERED"
    assert request.channel == "WECOM"
    assert request.route_key == "bot-1:ext-1", "route_key 由适配器产出"
    assert tenant_id == "tenant-1", "租户随请求走，不靠请求头"


async def test_failed_delivery_is_audited_as_failed_with_a_reason_code() -> None:
    """交付失败 → 审计落 `FAILED` + 原因码。**这正是 E-06 要的那一行。**"""
    artifact_id = uuid.uuid4()
    console = FakeConsoleClient()

    async with _client(_FailingArtifactAdapter(), console=console) as client:
        response = await client.post(DELIVERIES_URL, json=_body(artifact_id))

    assert response.status_code == 502, response.text
    assert len(console.delivery_audit_calls) == 1
    request, _tenant = console.delivery_audit_calls[0]
    assert request.outcome == "FAILED"
    assert request.reason_code == "ARTIFACT_DELIVERY_FAILED"


async def test_audit_write_failure_does_not_undo_a_completed_delivery() -> None:
    """审计写不进去**不阻断**已完成的交付：东西已经到用户手里了，为一条记录去回滚送达是把事做反了。

    但调用方仍拿到真实结局（200 + DELIVERED）——审计是**留痕**，不是交付的前置条件。
    """
    from muad_api import AppError, ErrorCode

    artifact_id = uuid.uuid4()
    console = FakeConsoleClient()
    console.delivery_audit_error = AppError(ErrorCode.COMMON_INTERNAL_ERROR)
    adapter = _ArtifactAdapter(ArtifactDeliveryOutcome(outcome=ARTIFACT_DELIVERED))

    async with _client(adapter, console=console) as client:
        response = await client.post(DELIVERIES_URL, json=_body(artifact_id))

    assert response.status_code == 200, response.text
    assert response.json()["data"]["outcome"] == ARTIFACT_DELIVERED
    assert len(adapter.received) == 1, "交付本身真的发生了"


# --------------------------------------------------------------------- E-06 收口


async def test_e06_failed_delivery_then_retry_succeeds_and_the_user_gets_it_once() -> None:
    """E-06 主链：**首次失败 → 显式报失败 + 审计 FAILED → 同幂等键重试 → 用户恰好收到一次**。

    真实边界：真实 HTTP（网关交付端点）+ **渠道侧失败注入**（同一个假适配器先抛后成）。
    审计那条线的「同一行」由 partial unique `(tenant_id, artifact_id, route_key)` 在
    console 侧承载——**那半条在 E-04 里打真实 PG 验过**（`tests/console_channel/
    test_artifact_delivery_audit.py`），这里断言的是**网关确实对同一个目标发了两次审计**
    （两次的 `artifact_id` + `route_key` 完全相同 ⇒ 落到同一行的前提成立），且第二次是 DELIVERED。
    """
    artifact_id = uuid.uuid4()
    body = _body(artifact_id)
    dedupe = InMemoryDedupeStore()

    # ① 渠道侧失败：显式失败 + 审计 FAILED
    console = FakeConsoleClient()
    async with _client(_FailingArtifactAdapter(), dedupe, console) as client:
        first = await client.post(DELIVERIES_URL, json=body)
    assert first.status_code == 502, first.text
    assert first.json()["code"] == "ARTIFACT_DELIVERY_FAILED"
    (failed_audit, tenant_id) = console.delivery_audit_calls[0]
    assert failed_audit.outcome == "FAILED"
    assert failed_audit.artifact_id == artifact_id
    assert len(console.delivery_audit_calls) == 1, "失败恰好一条审计"

    # ② 同一 delivery_key 重试：换成能发出去的适配器 ⇒ 成功 + 审计 DELIVERED
    retry_console = FakeConsoleClient()
    adapter = _ArtifactAdapter(ArtifactDeliveryOutcome(outcome=ARTIFACT_DELIVERED))
    async with _client(adapter, dedupe, retry_console) as client:
        second = await client.post(DELIVERIES_URL, json=body)
    assert second.status_code == 200, second.text
    assert second.json()["data"]["outcome"] == ARTIFACT_DELIVERED
    (ok_audit, _tenant) = retry_console.delivery_audit_calls[0]
    assert ok_audit.outcome == "DELIVERED"
    assert ok_audit.artifact_id == failed_audit.artifact_id, "必须是同一个目标"
    assert ok_audit.route_key == failed_audit.route_key, "路由也必须相同，否则会落成两行"
    assert tenant_id == _tenant

    # ③ 用户恰好收到一次：失败那次**没有**发出去，重试发了且只发了一次
    assert len(adapter.received) == 1
