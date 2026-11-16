"""MSSWWEB 本地流式对话面：多挂载、surface 解析、适配器注册（TASK-007 / RULE-im-006）。

验证同一套 webchat router 按 surface 多挂载后：

- `/api/v1/webchat` 解析为 WEBCHAT / USER_TOKEN；
- `/api/v1/msswwweb` 解析为 MSSWWEB / EXTERNAL_ASSERTION；
- 两条前缀下都存在同一批聊天路由（含通知）；
- 注册表同时登记 WEBCHAT 与 MSSWWEB，且二者都 `delivers_locally`。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from starlette.requests import Request

from muad_api import AppError
from muad_contracts import LOCAL_STREAM_CHANNELS
from muad_contracts.webchat import ChatPrincipal
from muad_im_gateway.api.webchat import get_webchat_surface
from muad_im_gateway.application.chat_principal import ChatIdentity
from muad_im_gateway.application.webchat_core import ChatSurface
from muad_im_gateway.application.webchat_service import WebchatResourceService
from muad_im_gateway.channels.registry import build_channel_registry
from muad_im_gateway.channels.webchat import WebchatChannelAdapter


def _make_request(path: str, surfaces: dict[str, ChatSurface]) -> Request:
    app = FastAPI()
    app.state.webchat_surfaces = surfaces
    scope = {"type": "http", "path": path, "app": app, "method": "GET"}
    return Request(scope)


SURFACES = {
    "/api/v1/webchat": ChatSurface(channel="WEBCHAT", auth_kind="USER_TOKEN"),
    "/api/v1/msswwweb": ChatSurface(channel="MSSWWEB", auth_kind="EXTERNAL_ASSERTION"),
}


def test_surface_resolves_webchat() -> None:
    surface = get_webchat_surface(_make_request("/api/v1/webchat/conversations", SURFACES))
    assert surface.channel == "WEBCHAT"
    assert surface.auth_kind == "USER_TOKEN"


def test_surface_resolves_msswwweb() -> None:
    surface = get_webchat_surface(
        _make_request("/api/v1/msswwweb/conversations/abc/messages", SURFACES)
    )
    assert surface.channel == "MSSWWEB"
    assert surface.auth_kind == "EXTERNAL_ASSERTION"


def test_surface_defaults_to_webchat_for_unknown_prefix() -> None:
    surface = get_webchat_surface(_make_request("/api/v1/other", SURFACES))
    assert surface.channel == "WEBCHAT"


def test_surface_identity_matches_channel() -> None:
    surface = ChatSurface(channel="MSSWWEB", auth_kind="EXTERNAL_ASSERTION")
    assert surface.identity.channel == "MSSWWEB"


def test_registry_registers_all_local_stream_channels() -> None:
    from muad_common import SharedSettings

    registry = build_channel_registry(
        SharedSettings(
            gateway_webhook_channels="",
            gateway_routed_channels="",
            external_assertion_platforms_json='{"mssw": {"platform_key": "mssw"}}',
        )
    )
    names = set(registry.names())
    for channel in LOCAL_STREAM_CHANNELS:
        assert channel in names


def test_registry_has_only_webchat_when_external_platforms_disabled() -> None:
    from muad_common import SharedSettings

    registry = build_channel_registry(
        SharedSettings(
            gateway_webhook_channels="",
            gateway_routed_channels="",
            external_assertion_platforms_json="",
        )
    )
    assert set(registry.names()) == {"WEBCHAT"}


def test_webchat_adapter_is_name_parameterized() -> None:
    assert WebchatChannelAdapter(name="WEBCHAT").channel == "WEBCHAT"
    assert WebchatChannelAdapter(name="MSSWWEB").channel == "MSSWWEB"
    assert WebchatChannelAdapter(name="MSSWWEB").delivers_locally is True


def _route_paths(*, msswwweb_enabled: bool) -> set[str]:
    """在独立 FastAPI 上按配置挂 surface 后的有效路由路径（含惰性展开）。"""
    from muad_common import SharedSettings
    from muad_im_gateway.main import mount_webchat_surfaces

    settings = SharedSettings(
        external_assertion_platforms_json=(
            '{"mssw": {"platform_key": "mssw"}}' if msswwweb_enabled else ""
        )
    )
    probe = FastAPI()
    mount_webchat_surfaces(probe, settings)

    paths: set[str] = set()

    def visit(routes) -> None:
        for route in routes:
            expand = getattr(route, "effective_route_contexts", None)
            if expand is not None:
                visit(expand())
                continue
            path = getattr(route, "path", "")
            if path:
                paths.add(path)

    visit(probe.routes)
    return paths


def test_both_surfaces_mount_chat_routes() -> None:
    paths = _route_paths(msswwweb_enabled=True)
    for prefix in ("/api/v1/webchat", "/api/v1/msswwweb"):
        assert f"{prefix}/entries" in paths
        assert f"{prefix}/entries/{{entry_id}}/messages" in paths
        assert f"{prefix}/entries/{{entry_id}}/conversations" in paths
        assert f"{prefix}/runs/{{run_id}}/cancel" in paths
        assert f"{prefix}/runs/{{run_id}}/resume" in paths


def test_notifications_only_on_webchat_surface() -> None:
    paths = _route_paths(msswwweb_enabled=True)
    assert "/api/v1/webchat/notifications" in paths
    assert "/api/v1/msswwweb/notifications" not in paths
    assert "/api/v1/msswwweb/notifications/stream" not in paths


def test_run_request_carries_surface_channel() -> None:
    """`_run_request` 的 `RunRequest.channel.type` 取自传入的 channel，而非写死 WEBCHAT。"""
    from muad_contracts import InboundEvent
    from muad_im_core import SyncPrincipal
    from muad_im_gateway.application.webchat_core import _run_request

    event = InboundEvent(
        event_id=uuid.uuid4(),
        channel="MSSWWEB",
        tenant_id="t1",
        bot_id="b1",
        external_message_id="m1",
        external_conversation_id=str(uuid.uuid4()),
        external_user_id="u1",
        text="hi",
        received_at=datetime.now(UTC),
    )
    principal = SyncPrincipal(
        tenant_id="t1",
        platform_user_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        idempotency_key="k1",
        entry_id=uuid.uuid4(),
    )
    request = _run_request(event, principal, channel="MSSWWEB")
    assert request.channel.type == "MSSWWEB"

    webchat_request = _run_request(event, principal, channel="WEBCHAT")
    assert webchat_request.channel.type == "WEBCHAT"


def test_msswwweb_surface_absent_when_disabled() -> None:
    paths = _route_paths(msswwweb_enabled=False)
    assert "/api/v1/webchat/entries" in paths
    assert not any(p.startswith("/api/v1/msswwweb") for p in paths)


# --- S-04：MSSWWEB 上停止生成沿用同一取消语义，核属按 user 而非通道/入口/Agent ---------------


class _CancelRuntime:
    """只实现取消路径要的动词面：`get_run`（核属）+ `cancel_run`（转终态）。"""

    def __init__(self, *, owner_id: uuid.UUID) -> None:
        self.owner_id = owner_id
        self.cancelled: list[uuid.UUID] = []
        self.get_calls: list[uuid.UUID] = []

    async def get_run(self, run_id: uuid.UUID, *, tenant_id: str) -> dict[str, Any]:
        self.get_calls.append(run_id)
        return {"run_id": str(run_id), "user_id": str(self.owner_id), "status": "RUNNING"}

    async def cancel_run(self, run_id: uuid.UUID, *, tenant_id: str) -> dict[str, Any]:
        self.cancelled.append(run_id)
        return {"run_id": str(run_id), "status": "CANCELLING", "cancel_requested": True}


def _identity(platform_user_id: uuid.UUID) -> ChatIdentity:
    return ChatIdentity(
        principal=ChatPrincipal(
            tenant_id="t1",
            platform_user_id=platform_user_id,
            session_id=uuid.uuid4(),
            auth_method="EXTERNAL_ASSERTION",
        ),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        agents=(),
    )


class _NoEntries:
    """取消不查入口；用到即失败，证明取消路径不依赖 entry。"""

    async def list_entries(self, *args, **kwargs):  # pragma: no cover - 触发即证明越界
        raise AssertionError("取消路径不得解析入口")


async def test_msswwweb_cancel_targets_own_run_by_user_not_channel() -> None:
    """MSSWWEB surface 下停止生成：本人 Run 转 CANCELLING，runtime.cancel_run 被调用且不带通道。"""
    platform_user_id = uuid.uuid4()
    run_id = uuid.uuid4()
    runtime = _CancelRuntime(owner_id=platform_user_id)
    service = WebchatResourceService(
        runtime=runtime,
        entries=_NoEntries(),
        tenant_id="t1",
        surface=ChatSurface(channel="MSSWWEB", auth_kind="EXTERNAL_ASSERTION"),
    )

    result = await service.cancel_run(_identity(platform_user_id), run_id)

    assert runtime.cancelled == [run_id], "本人 Run 必须被取消"
    assert result.status == "CANCELLING" and result.cancel_requested is True


async def test_msswwweb_cancel_foreign_run_is_hidden_as_not_found() -> None:
    """他人 Run 视同不存在（404），runtime.cancel_run 零触达——归属先于取消。"""
    platform_user_id = uuid.uuid4()
    run_id = uuid.uuid4()
    runtime = _CancelRuntime(owner_id=uuid.uuid4())
    service = WebchatResourceService(
        runtime=runtime,
        entries=_NoEntries(),
        tenant_id="t1",
        surface=ChatSurface(channel="MSSWWEB", auth_kind="EXTERNAL_ASSERTION"),
    )

    with pytest.raises(AppError):
        await service.cancel_run(_identity(platform_user_id), run_id)

    assert runtime.cancelled == [], "他人 Run 不得被取消"


async def test_cancel_is_channel_independent_across_surfaces() -> None:
    """同一 user 在 WEBCHAT 与 MSSWWEB 两个 surface 上都能取消同一 Run（入口不是边界）。"""
    platform_user_id = uuid.uuid4()
    run_id = uuid.uuid4()
    identity = _identity(platform_user_id)

    for channel in ("WEBCHAT", "MSSWWEB"):
        runtime = _CancelRuntime(owner_id=platform_user_id)
        service = WebchatResourceService(
            runtime=runtime,
            entries=_NoEntries(),
            tenant_id="t1",
            surface=ChatSurface(channel=channel),
        )
        await service.cancel_run(identity, run_id)
        assert runtime.cancelled == [run_id], f"{channel} 上取消同一 Run 生效"
