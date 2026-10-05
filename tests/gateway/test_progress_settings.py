"""B-03：IM 展示节拍由平台设置提供，回复生命周期内固定（ADR-04）。

不得 Mock 的真实边界：**真实 Gateway 回复生命周期取值函数**（`resolve_reply_settings`，
走真实 `parse_platform_settings`，下界 `ge=1.0` 由 schema 单一来源把住）与**真实内部约定**
（client 打到 `GET /internal/v1/platform-settings`，带服务身份 + `X-Caller-Service: gateway`）。
不 mock 设置源本身：快照一律经真实解析缝取值。

三句断言（design §2.5.2 B-03）：

① 下界 `1.0` 接受、更小被拒（取值边界）；
② 一条回复内多次 tick 节拍不变——每一条回复只在**生命周期开始**取一次快照，
   整个回复期间 `iter_with_ticks` 拿到的节拍是同一个值，绝不每 tick 重取；
③ 下一条消息用新值——新快照立即对下一条入站消息生效，回复渲染的 locale 也随之切换。
"""

from __future__ import annotations

import httpx
import pytest
from fakes import FakeConsoleClient, FakeRuntimeClient, make_envelope, resolved_response
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_api.metrics import render_metrics
from muad_im_gateway.application import inbound
from muad_im_gateway.application.inbound import InboundPipeline
from muad_im_gateway.application.platform_settings import resolve_reply_settings
from muad_im_gateway.application.ports import PlatformSettingsSnapshot
from muad_im_gateway.application.sse import SseEvent
from muad_im_gateway.channels.fake import FakeChannelAdapter
from muad_im_gateway.infrastructure.dedupe import NullDedupeStore
from muad_im_gateway.infrastructure.platform_settings_client import (
    PLATFORM_SETTINGS_PATH,
    ConsolePlatformSettingsClient,
)


def _snapshot(settings: dict[str, object], *, revision: int = 1) -> PlatformSettingsSnapshot:
    return PlatformSettingsSnapshot(revision=revision, settings=settings)


class FakePlatformSettingsClient:
    """按调用次数返回预设快照序列（最后一条重复使用）；记录取了几次。"""

    def __init__(self, snapshots: list[PlatformSettingsSnapshot]) -> None:
        self._snapshots = snapshots
        self.calls: list[str] = []

    async def fetch_snapshot(self, *, tenant_id: str, trace_id: str = "") -> PlatformSettingsSnapshot:
        index = min(len(self.calls), len(self._snapshots) - 1)
        self.calls.append(tenant_id)
        return self._snapshots[index]


@pytest.fixture
def recorded_intervals(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """记录每条回复实际交给 `iter_with_ticks` 的节拍（真实函数照常运行，不改写行为）。"""
    recorded: list[float] = []
    real = inbound.iter_with_ticks

    async def spy(events, *, interval):  # type: ignore[no-untyped-def]
        recorded.append(interval)
        async for item in real(events, interval=interval):
            yield item

    monkeypatch.setattr(inbound, "iter_with_ticks", spy)
    return recorded


def _pipeline(catalog, console, runtime, settings_client) -> InboundPipeline:  # type: ignore[no-untyped-def]
    return InboundPipeline(
        dedupe=NullDedupeStore(),
        console=console,
        runtime=runtime,
        catalog=catalog,
        settings_client=settings_client,
        tenant_id="tenant-1",
    )


def _run_events() -> list[SseEvent]:
    return [
        SseEvent("run.created", {"run_id": "run-1"}),
        SseEvent("run.completed", {"status": "COMPLETED", "final_text": "答案"}),
    ]


def test_b03_interval_floor_accepts_one_and_rejects_below() -> None:
    """① 取值边界：`ge=1.0` 由真实 schema 把住——`1.0` 接受，更小被拒。"""
    accepted = resolve_reply_settings(_snapshot({"im": {"progress_interval_sec": 1.0}}))
    assert accepted.progress_interval_sec == 1.0

    with pytest.raises(AppError):
        resolve_reply_settings(_snapshot({"im": {"progress_interval_sec": 0.5}}))
    with pytest.raises(AppError):
        resolve_reply_settings(_snapshot({"im": {"progress_interval_sec": 0.0}}))


def test_b03_defaults_when_tenant_has_no_record() -> None:
    """无记录（revision 0）等价于 schema 默认：5.0 秒 / zh-CN。"""
    reply = resolve_reply_settings(PlatformSettingsSnapshot(revision=0))
    assert (reply.locale, reply.progress_interval_sec) == ("zh-CN", 5.0)


async def test_b03_one_fetch_per_reply_and_next_message_uses_new_value(
    catalog, recorded_intervals
) -> None:
    """②③ 每条回复只在生命周期开始取一次快照；下一条消息用新快照的节拍。"""
    console = FakeConsoleClient()
    console.resolve_response = resolved_response()
    runtime = FakeRuntimeClient(_run_events())
    settings_client = FakePlatformSettingsClient(
        [
            _snapshot({"im": {"progress_interval_sec": 1.0}}),
            _snapshot({"im": {"progress_interval_sec": 5.0}}),
        ]
    )
    adapter = FakeChannelAdapter()
    pipeline = _pipeline(catalog, console, runtime, settings_client)

    await pipeline.handle(adapter, make_envelope("第一条", message_id="msg-a"))
    await pipeline.handle(adapter, make_envelope("第二条", message_id="msg-b"))

    # 回复期间不重取（两条消息 ⇒ 两次取；tick 不会触发第三次取）
    assert settings_client.calls == ["tenant-1", "tenant-1"]
    # 一条回复内 `iter_with_ticks` 只被调用一次、节拍取自该回复的快照；下一条用新值
    assert recorded_intervals == [1.0, 5.0]


async def test_b03_reply_render_locale_comes_from_snapshot(catalog) -> None:
    """③ 回复渲染的 locale 取自平台设置快照，且逐条消息切换（不再读启动 settings）。"""
    console = FakeConsoleClient()
    console.resolve_error = AppError(str(ErrorCode.COMMON_INTERNAL_ERROR))
    runtime = FakeRuntimeClient()
    settings_client = FakePlatformSettingsClient(
        [
            _snapshot({"locale": {"default_locale": "en-US"}}),
            _snapshot({"locale": {"default_locale": "zh-CN"}}),
        ]
    )
    adapter = FakeChannelAdapter()
    pipeline = _pipeline(catalog, console, runtime, settings_client)

    await pipeline.handle(adapter, make_envelope("hi", message_id="msg-a"))
    await pipeline.handle(adapter, make_envelope("你好", message_id="msg-b"))

    expected = catalog.message(str(ErrorCode.COMMON_INTERNAL_ERROR), "en-US")
    assert adapter.sent[-2][1].text == expected
    assert adapter.sent[-1][1].text == catalog.message(str(ErrorCode.COMMON_INTERNAL_ERROR), "zh-CN")
    assert expected != adapter.sent[-1][1].text, "两条消息的 locale 必须真的不同"


def _gateway_failed_total() -> float:
    line_prefix = 'platform_settings_fetch_total{caller="gateway",result="failed"}'
    for line in render_metrics().splitlines():
        if line.startswith(line_prefix):
            return float(line.rsplit(" ", 1)[1])
    return 0.0


async def test_b03_client_uses_internal_contract_and_records_failed_metric() -> None:
    """client 打真实内部契约：路径、服务身份与 `X-Caller-Service: gateway`；失败记在调用方侧。"""
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["headers"] = dict(request.headers)
        return httpx.Response(500, json={"error": {"code": str(ErrorCode.COMMON_INTERNAL_ERROR)}})

    before = _gateway_failed_total()
    client = ConsolePlatformSettingsClient(
        "http://console", service_token="tok", transport=httpx.MockTransport(handler)
    )
    try:
        with pytest.raises(AppError):
            await client.fetch_snapshot(tenant_id="tenant-1")
    finally:
        await client.aclose()

    headers = seen["headers"]
    assert seen["path"] == PLATFORM_SETTINGS_PATH == "/internal/v1/platform-settings"
    assert headers["x-internal-service"] == "tok"
    assert headers["x-tenant-id"] == "tenant-1"
    assert headers["x-caller-service"] == "gateway"
    assert _gateway_failed_total() == before + 1, "取快照失败必须由调用方记 failed 计数"
