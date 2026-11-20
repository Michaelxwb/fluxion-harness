"""MSSWWEB 本地流式对话面：多挂载、surface 解析、适配器注册（TASK-007 / RULE-im-006）。

验证同一套 webchat router 按 surface 多挂载后：

- `/api/v1/webchat` 解析为 WEBCHAT / USER_TOKEN；
- `/api/v1/msswweb` 解析为 MSSWWEB / EXTERNAL_ASSERTION；
- 两条前缀下都存在同一批聊天路由（含通知）；
- 注册表同时登记 WEBCHAT 与 MSSWWEB，且二者都 `delivers_locally`。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from muad_api import AppError
from muad_contracts import LOCAL_STREAM_CHANNELS
from muad_contracts.webchat import ChatPrincipal
from muad_im_gateway.api.webchat import get_webchat_surface
from muad_im_gateway.application.chat_principal import ChatIdentity
from muad_im_gateway.application.webchat_core import ChatSurface
from muad_im_gateway.application.webchat_service import WebchatResourceService
from muad_im_gateway.channels.registry import build_channel_registry
from muad_im_gateway.channels.webchat import WebchatChannelAdapter
from starlette.requests import Request


def _make_request(path: str, surfaces: dict[str, ChatSurface]) -> Request:
    app = FastAPI()
    app.state.webchat_surfaces = surfaces
    scope = {"type": "http", "path": path, "app": app, "method": "GET"}
    return Request(scope)


SURFACES = {
    "/api/v1/webchat": ChatSurface(channel="WEBCHAT", auth_kind="USER_TOKEN"),
    "/api/v1/msswweb": ChatSurface(channel="MSSWWEB", auth_kind="EXTERNAL_ASSERTION"),
}


def test_surface_resolves_webchat() -> None:
    surface = get_webchat_surface(_make_request("/api/v1/webchat/conversations", SURFACES))
    assert surface.channel == "WEBCHAT"
    assert surface.auth_kind == "USER_TOKEN"


def test_surface_resolves_msswweb() -> None:
    surface = get_webchat_surface(
        _make_request("/api/v1/msswweb/conversations/abc/messages", SURFACES)
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


def _route_paths(*, msswweb_enabled: bool) -> set[str]:
    """在独立 FastAPI 上按配置挂 surface 后的有效路由路径（含惰性展开）。"""
    from muad_common import SharedSettings
    from muad_im_gateway.main import mount_webchat_surfaces

    settings = SharedSettings(
        external_assertion_platforms_json=(
            '{"mssw": {"platform_key": "mssw"}}' if msswweb_enabled else ""
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
    paths = _route_paths(msswweb_enabled=True)
    for prefix in ("/api/v1/webchat", "/api/v1/msswweb"):
        assert f"{prefix}/entries" in paths
        assert f"{prefix}/entries/{{entry_id}}/messages" in paths
        assert f"{prefix}/entries/{{entry_id}}/conversations" in paths
        assert f"{prefix}/runs/{{run_id}}/cancel" in paths
        assert f"{prefix}/runs/{{run_id}}/resume" in paths


def test_external_prefix_keeps_msswweb_quirk_and_derives_others() -> None:
    """`MSSWWEB` 前缀是历史固定值 `/api/v1/msswweb`（≠通道名小写 `msswweb`）；

    其它外部通道按 `/api/v1/<channel 小写>` 生成，保证新增平台路由自然出现。
    """
    from muad_im_gateway.main import _external_prefix_for

    assert _external_prefix_for("MSSWWEB") == "/api/v1/msswweb"
    assert _external_prefix_for("ACMEWEB") == "/api/v1/acmeweb"


def test_notifications_only_on_webchat_surface() -> None:
    paths = _route_paths(msswweb_enabled=True)
    assert "/api/v1/webchat/notifications" in paths
    assert "/api/v1/msswweb/notifications" not in paths
    assert "/api/v1/msswweb/notifications/stream" not in paths


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


def test_msswweb_surface_absent_when_disabled() -> None:
    paths = _route_paths(msswweb_enabled=False)
    assert "/api/v1/webchat/entries" in paths
    assert not any(p.startswith("/api/v1/msswweb") for p in paths)


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


async def test_msswweb_cancel_targets_own_run_by_user_not_channel() -> None:
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


async def test_msswweb_cancel_foreign_run_is_hidden_as_not_found() -> None:
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


# --- S-06：上传归属按本次发送入口；产物下载按 user+Agent，不做通道断言 ------------------------


def _entry_source(agent_id: uuid.UUID):
    from muad_contracts import BotSnapshotItem

    webchat_entry = uuid.uuid4()
    msswweb_entry = uuid.uuid4()

    class _Source:
        @property
        def items(self):
            return (
                BotSnapshotItem(
                    bot_account_id=webchat_entry,
                    bot_id="web-1",
                    channel="WEBCHAT",
                    agent_id=agent_id,
                ),
                BotSnapshotItem(
                    bot_account_id=msswweb_entry,
                    bot_id="mssw-1",
                    channel="MSSWWEB",
                    agent_id=agent_id,
                ),
            )

    return _Source(), webchat_entry, msswweb_entry


def _identity_with_agent(platform_user_id: uuid.UUID, agent_id: uuid.UUID) -> ChatIdentity:
    from muad_contracts.webchat import ChatAuthorizedAgent

    base = _identity(platform_user_id)
    return ChatIdentity(
        principal=base.principal,
        expires_at=base.expires_at,
        agents=(ChatAuthorizedAgent(agent_id=agent_id, key="a", name="a"),),
    )


def test_upload_entry_resolution_follows_this_send_surface() -> None:
    """上传归属按**本次发送入口**：MSSWWEB surface 只认 MSSWWEB 入口，不能借用 WEBCHAT 入口。"""
    from muad_im_gateway.application.webchat_service import resolve_authorized_entry

    agent_id = uuid.uuid4()
    entries, webchat_entry, msswweb_entry = _entry_source(agent_id)
    identity = _identity_with_agent(uuid.uuid4(), agent_id)

    resolved = resolve_authorized_entry(
        entries, identity, msswweb_entry, channel="MSSWWEB"
    )
    assert resolved.entry_id == msswweb_entry
    assert resolved.agent_id == agent_id

    with pytest.raises(AppError):
        resolve_authorized_entry(entries, identity, webchat_entry, channel="MSSWWEB")


def test_artifact_download_resolution_has_no_channel_assertion() -> None:
    """产物下载按 user+Agent（设计 API-15）：`channel=None` 时跨通道入口仍可解析。"""
    from muad_im_gateway.application.webchat_service import resolve_authorized_entry

    agent_id = uuid.uuid4()
    entries, _webchat_entry, msswweb_entry = _entry_source(agent_id)
    identity = _identity_with_agent(uuid.uuid4(), agent_id)

    resolved = resolve_authorized_entry(entries, identity, msswweb_entry, channel=None)
    assert resolved.entry_id == msswweb_entry


async def test_artifact_service_downloads_artifact_from_other_surface_entry(tmp_path) -> None:
    """同用户同 Agent 的产物跨入口可下载：入口通道与调用方 surface 不同也不拒绝。"""
    from muad_artifact_store import NfsArtifactStore
    from muad_im_gateway.application.webchat_artifacts import WebchatArtifactService

    agent_id = uuid.uuid4()
    platform_user_id = uuid.uuid4()
    conversation_id = uuid.uuid4()
    artifact_id = uuid.uuid4()
    entries, _webchat_entry, msswweb_entry = _entry_source(agent_id)
    identity = _identity_with_agent(platform_user_id, agent_id)

    store_root = tmp_path / "artifacts"
    store_root.mkdir()
    payload = b"report-bytes"
    (store_root / "t1" / f"{artifact_id}.bin").parent.mkdir(parents=True)
    (store_root / "t1" / f"{artifact_id}.bin").write_bytes(payload)
    store = NfsArtifactStore(store_root)

    class _Runtime:
        async def resolve_webchat_artifact(self, artifact_id, **kwargs):
            return {
                "scope": {
                    "entry_id": str(msswweb_entry),
                    "agent_id": str(agent_id),
                    "conversation_id": str(conversation_id),
                },
                "reference": {
                    "storage_key": f"t1/{artifact_id}.bin",
                    "kind": "OTHER",
                    "media_type": "application/octet-stream",
                    "size": len(payload),
                    "filename": "report.bin",
                    "checksum": "sha256:deadbeef",
                },
            }

    service = WebchatArtifactService(
        runtime=_Runtime(), entries=entries, store=store, tenant_id="t1"
    )
    download = await service.open_download(
        identity,
        artifact_id,
        conversation_id=conversation_id,
        checksum="sha256:deadbeef",
    )
    assert download.path.read_bytes() == payload


# --- TASK-010：跨入口共享会话的读取面透传 entry_agnostic ---------------------------------------


def test_read_scope_omits_entry_agnostic_by_default() -> None:
    """默认读取面不带 `entry_agnostic`（保持既有严格入口语义）。"""
    from muad_im_gateway.application.runtime_client import _read_scope

    params = _read_scope(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
    assert "entry_agnostic" not in params


def test_read_scope_flags_entry_agnostic_when_requested() -> None:
    """跨入口读取面显式声明 `entry_agnostic=true` 透传给 Owner。"""
    from muad_im_gateway.application.runtime_client import _read_scope

    params = _read_scope(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), entry_agnostic=True)
    assert params["entry_agnostic"] == "true"
