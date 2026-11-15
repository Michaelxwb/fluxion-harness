"""S-12 任务中心契约预留：一个 service，多个 surface；本需求不挂 MSSWWEB 路由。

本文件是**契约测试**（S-12 要求用 Fake MSSWWEB principal 调用同一 service）：

- 同一 `TaskCenterService` + 同一组 handler 分别挂到 WebChat 与一个测试用的 Fake MSSWWEB
  surface 上，同一输入必须得到逐字节相同的 `data`（items/detail/pending/badges 全对齐）。
- 两个 surface 的唯一差别是前缀与确认来源（`ConfirmationSurface`）；数据范围是 user+agent，
  entry/channel 只是来源元数据（响应里没有这些字段的位置）。
- 生产 `muad_im_gateway.main.app` 上**没有** `/api/v1/msswwweb/task-center/*`：MSSWWEB 任务中心
  属于另立需求，落地时只加路由，不改 service/contracts（本文件把它钉成事实）。

Fake principal 是本文件唯一的"假"边界（S-12 明确要求）；scoping 语义仍由真实 service 执行。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from muad_api import install_api_foundation
from muad_artifact_store import NfsArtifactStore
from muad_contracts import ConfirmationSurface
from muad_contracts.webchat import ChatAuthorizedAgent, ChatPrincipal
from muad_im_gateway.api.deps import get_chat_identity
from muad_im_gateway.api.task_center import build_task_center_router, get_task_center_service
from muad_im_gateway.application.chat_principal import ChatIdentity
from muad_im_gateway.application.task_center import (
    WEBCHAT_SURFACE,
    TaskCenterService,
    TaskCenterSurface,
)
from muad_im_gateway.main import app as production_app

GATEWAY_TENANT = "tenant-sched024-contract"
USER_A = UUID("c1c1c1c1-c1c1-41c1-81c1-c1c1c1c1c1c1")
SESSION_WEBCHAT = UUID("d1d1d1d1-d1d1-41d1-81d1-d1d1d1d1d1d1")
SESSION_MSSWWEB = UUID("d2d2d2d2-d2d2-42d2-82d2-d2d2d2d2d2d2")
AGENT_A1 = UUID("e1e1e1e1-e1e1-41e1-81e1-e1e1e1e1e1e1")
AGENT_A2 = UUID("e2e2e2e2-e2e2-42e2-82e2-e2e2e2e2e2e2")
AGENT_REVOKED = UUID("e3e3e3e3-e3e3-43e3-83e3-e3e3e3e3e3e3")
S1 = UUID("f1f1f1f1-f1f1-41f1-81f1-f1f1f1f1f1f1")
S2 = UUID("f2f2f2f2-f2f2-42f2-82f2-f2f2f2f2f2f2")
S3 = UUID("f3f3f3f3-f3f3-43f3-83f3-f3f3f3f3f3f3")
E1 = UUID("a1a1a1a1-a1a1-41a1-81a1-a1a1a1a1a1a1")
I1 = UUID("b1b1b1b1-b1b1-41b1-81b1-b1b1b1b1b1b1")
DRAFT_ID = UUID("c2c2c2c2-c2c2-42c2-82c2-c2c2c2c2c2c2")
FIRE_ID = UUID("c4c4c4c4-c4c4-44c4-84c4-c4c4c4c4c4c4")
CONFIRMED_SCHEDULE = UUID("c3c3c3c3-c3c3-43c3-83c3-c3c3c3c3c3c3")

#: Fake MSSWWEB surface：本需求**只存在于测试**，用于证明同一 service 可以直接复用。
MSSWWEB_SURFACE = TaskCenterSurface(
    name="msswwweb", confirmation_surface=ConfirmationSurface.MSSWWEB
)

FORBIDDEN_PAYLOAD_KEYS = frozenset(
    {
        "channel",
        "channels",
        "channel_type",
        "source_channel",
        "entry_id",
        "entry_channel",
        "tenant_id",
        "platform_user_id",
        "session_id",
    }
)


def _at(minutes: int) -> datetime:
    return datetime(2026, 11, 15, 9, 0, tzinfo=UTC) + timedelta(minutes=minutes)


# --- 内存 Owner（契约测试的输入；不是业务路径的替身） ---------------------------------------


@dataclass
class _FakeOwner:
    """固定事实的 Owner 面：只验证**投影与范围**，不涉及 HTTP/DB。"""

    confirmations: list[tuple[ConfirmationSurface, str, UUID]] = field(default_factory=list)
    responses: list[tuple[UUID, dict[str, Any]]] = field(default_factory=list)
    actions: list[tuple[UUID, str]] = field(default_factory=list)

    def _item(self, schedule_id: UUID) -> dict[str, Any]:
        rows = {
            S1: (AGENT_A1, "A 客户周报", "SKILL", "ACTIVE"),
            S2: (AGENT_A2, "线索汇总", "AGENTIC", "PAUSED"),
            S3: (AGENT_REVOKED, "已撤销助手的提醒", "REMINDER", "ACTIVE"),
        }
        agent_id, name, mode, status = rows[schedule_id]
        return {
            "schedule_id": str(schedule_id),
            "agent_id": str(agent_id),
            "name": name,
            "mode": mode,
            "status": status,
            "timezone": "Asia/Shanghai",
            "config_version": 1,
            "next_fire_at": _at(30).isoformat(),
            "last_fire": None,
            "needs_attention": False,
            "pending_human": schedule_id == S1,
        }

    async def list_schedules(
        self,
        *,
        tenant_id: str,
        user_id: UUID,
        agent_id: UUID | None,
        status: Any,
        page: int,
        page_size: int,
    ) -> dict[str, Any]:
        rows = [self._item(schedule_id) for schedule_id in (S1, S2, S3)]
        if agent_id is not None:
            rows = [row for row in rows if row["agent_id"] == str(agent_id)]
        if status is not None:
            rows = [row for row in rows if row["status"] == str(status)]
        window = rows[(page - 1) * page_size : page * page_size]
        return {"items": window, "page": page, "page_size": page_size, "total": len(rows)}

    async def get_schedule(
        self, *, tenant_id: str, user_id: UUID, schedule_id: UUID
    ) -> dict[str, Any]:
        item = self._item(schedule_id)
        item.update(
            {
                "revision_no": 1,
                "instruction": item["name"],
                "normalized_goal": item["name"],
                "context": {"parameters": {}, "output": {}},
                "dependencies": [],
                "delivery_intent": "NONE",
                "policy": {},
                "budget": None,
                "memory_mode": None,
                "end_reason": None,
                "attention_reason": None,
            }
        )
        return item

    async def list_fires(
        self,
        *,
        tenant_id: str,
        user_id: UUID,
        schedule_id: UUID,
        page: int,
        page_size: int,
    ) -> dict[str, Any]:
        return {
            "items": [
                {
                    "fire_id": str(FIRE_ID),
                    "scheduled_at": _at(0).isoformat(),
                    "status": "EXECUTION_CREATED",
                    "skip_reason": None,
                    "missed_count": 0,
                    "dst_resolution": None,
                    "admission_error_code": None,
                    "execution_id": str(E1),
                    "execution_status": "COMPLETED",
                }
            ],
            "dst_events": [],
            "page": page,
            "page_size": page_size,
            "total": 1,
        }

    async def get_execution(
        self, *, tenant_id: str, user_id: UUID, execution_id: UUID
    ) -> dict[str, Any]:
        return {
            "execution_id": str(E1),
            "status": "COMPLETED",
            "wait_kind": None,
            "wait_payload": None,
            "result": {"text": "done"},
            "result_artifacts": [],
            "error_code": None,
            "error_message": None,
            "failure_class": None,
            "started_at": _at(0).isoformat(),
            "finished_at": _at(1).isoformat(),
            "snapshot_schema_version": 4,
            "snapshot_bytes": 512,
        }

    async def list_pending(
        self,
        *,
        tenant_id: str,
        user_id: UUID,
        agent_id: UUID,
        kind: str | None,
        page: int,
        page_size: int,
    ) -> dict[str, Any]:
        rows: list[dict[str, Any]] = []
        if agent_id == AGENT_A1 and kind in (None, "HUMAN"):
            rows.append(
                {
                    "kind": "HUMAN",
                    "interaction_id": str(I1),
                    "schedule_id": str(S1),
                    "execution_id": str(E1),
                    "schedule_name": "A 客户周报",
                    "question": "请选择客户",
                    "deadline_at": _at(30).isoformat(),
                    "input_schema": {"type": "object"},
                }
            )
        window = rows[(page - 1) * page_size : page * page_size]
        return {"items": window, "page": page, "page_size": page_size, "total": len(rows)}

    async def badges(
        self, *, tenant_id: str, user_id: UUID, agent_id: UUID
    ) -> dict[str, Any]:
        return {
            "pending_human": 1 if agent_id == AGENT_A1 else 0,
            "needs_attention": 1 if agent_id == AGENT_A2 else 0,
        }

    async def respond_human(
        self,
        *,
        tenant_id: str,
        user_id: UUID,
        agent_id: UUID | None,
        interaction_id: UUID,
        payload: dict[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        self.responses.append((interaction_id, payload))
        return {
            "interaction_id": str(interaction_id),
            "status": "RESOLVED",
            "execution_id": str(E1),
            "execution_status": "QUEUED",
        }

    async def schedule_action(
        self,
        *,
        tenant_id: str,
        user_id: UUID,
        schedule_id: UUID,
        action: str,
        payload: dict[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        self.actions.append((schedule_id, action))
        return {"schedule_id": str(schedule_id)}

    async def confirm_draft(
        self,
        *,
        tenant_id: str,
        user_id: UUID,
        draft_id: UUID,
        payload: dict[str, Any],
        surface: ConfirmationSurface,
        ref: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        self.confirmations.append((surface, ref, draft_id))
        return {
            "schedule_id": str(CONFIRMED_SCHEDULE),
            "status": "ACTIVE",
            "revision_no": 1,
            "config_version": 1,
            "next_fire_at": _at(60).isoformat(),
        }


@dataclass
class _FakeArtifacts:
    async def resolve_task_artifact_version(self, artifact_id: UUID, **_: Any) -> dict[str, Any]:
        raise AssertionError("contract test does not exercise artifact bytes")


# --- 两个 surface 的应用 -------------------------------------------------------------------


def _identity(session_id: UUID, agents: tuple[UUID, ...]) -> ChatIdentity:
    return ChatIdentity(
        principal=ChatPrincipal(
            tenant_id=GATEWAY_TENANT,
            platform_user_id=USER_A,
            session_id=session_id,
            auth_method="USER_TOKEN",
        ),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        agents=tuple(
            ChatAuthorizedAgent(agent_id=agent_id, key=f"agent-{agent_id.hex[:4]}", name="助手")
            for agent_id in agents
        ),
    )


def _provider[ValueT](value: ValueT) -> Callable[[], ValueT]:
    """零参依赖替身：**不要**用默认参数捕获（FastAPI 会深拷贝默认值，替身就不再是同一个对象）。"""

    def provide() -> ValueT:
        return value

    return provide


@asynccontextmanager
async def _surface_app(
    owner: _FakeOwner, *, agents: tuple[UUID, ...], tmp_path: Path
) -> AsyncIterator[dict[str, AsyncClient]]:
    """同一个 service + 同一组 handler 分别挂 WebChat 与 Fake MSSWWEB（仅测试存在）。

    两个 surface 各起一个应用实例，只差路由前缀与 `ConfirmationSurface`；Fake principal
    的 session 不同，用来证明数据范围来自 user+agent 而不是 surface/入口。
    """
    service = TaskCenterService(
        owner=owner,
        artifacts=_FakeArtifacts(),
        store=NfsArtifactStore(tmp_path / "artifacts"),
        tenant_id=GATEWAY_TENANT,
    )
    sessions = {
        WEBCHAT_SURFACE.name: (SESSION_WEBCHAT, _identity(SESSION_WEBCHAT, agents)),
        MSSWWEB_SURFACE.name: (SESSION_MSSWWEB, _identity(SESSION_MSSWWEB, agents)),
    }
    clients: dict[str, AsyncClient] = {}
    try:
        for surface in (WEBCHAT_SURFACE, MSSWWEB_SURFACE):
            session, identity = sessions[surface.name]
            test_app = FastAPI()
            install_api_foundation(test_app)
            test_app.include_router(build_task_center_router(surface))
            test_app.dependency_overrides[get_chat_identity] = _provider(identity)
            test_app.dependency_overrides[get_task_center_service] = _provider(service)
            clients[surface.name] = AsyncClient(
                transport=ASGITransport(app=test_app), base_url="http://surface.test"
            )
        yield clients
    finally:
        for client in clients.values():
            await client.aclose()


def _url(surface: TaskCenterSurface, path: str) -> str:
    return f"/api/v1/{surface.name}{path}"


def _forbidden_keys(payload: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(payload, dict):
        found.update(FORBIDDEN_PAYLOAD_KEYS & set(payload))
        for value in payload.values():
            found |= _forbidden_keys(value)
    elif isinstance(payload, list):
        for value in payload:
            found |= _forbidden_keys(value)
    return found


# --- 断言 ---------------------------------------------------------------------------------


async def test_both_surfaces_return_identical_data(tmp_path: Path) -> None:
    owner = _FakeOwner()
    async with _surface_app(owner, agents=(AGENT_A1, AGENT_A2), tmp_path=tmp_path) as clients:
        for path in (
            "/task-center/schedules",
            "/task-center/schedules?mode=AGENTIC",
            f"/task-center/schedules/{S1}",
            f"/task-center/schedules/{S1}/fires",
            f"/task-center/executions/{E1}",
            "/task-center/pending",
            "/task-center/badges",
        ):
            webchat = await clients[WEBCHAT_SURFACE.name].get(
                _url(WEBCHAT_SURFACE, path), headers={"X-Chat-Context": str(SESSION_WEBCHAT)}
            )
            msswwweb = await clients[MSSWWEB_SURFACE.name].get(
                _url(MSSWWEB_SURFACE, path), headers={"X-Chat-Context": str(SESSION_MSSWWEB)}
            )
            assert webchat.status_code == 200, (path, webchat.text)
            assert msswwweb.status_code == 200, (path, msswwweb.text)
            assert webchat.json()["data"] == msswwweb.json()["data"], path
            assert _forbidden_keys(webchat.json()["data"]) == set(), path


async def test_both_surfaces_share_agent_scope(tmp_path: Path) -> None:
    owner = _FakeOwner()
    async with _surface_app(owner, agents=(AGENT_A1, AGENT_A2), tmp_path=tmp_path) as clients:
        for surface, session in (
            (WEBCHAT_SURFACE, SESSION_WEBCHAT),
            (MSSWWEB_SURFACE, SESSION_MSSWWEB),
        ):
            client = clients[surface.name]
            listing = await client.get(
                _url(surface, "/task-center/schedules"),
                headers={"X-Chat-Context": str(session)},
            )
            assert [item["schedule_id"] for item in listing.json()["data"]["items"]] == [
                str(S1),
                str(S2),
            ], "撤销授权 Agent 的任务在两个 surface 上都不出现"

            revoked = await client.get(
                _url(surface, f"/task-center/schedules/{S3}"),
                headers={"X-Chat-Context": str(session)},
            )
            assert revoked.status_code == 404
            assert revoked.json()["code"] == "SCHEDULE_NOT_FOUND"


async def test_confirm_uses_each_surface_confirmation_source(tmp_path: Path) -> None:
    owner = _FakeOwner()
    async with _surface_app(owner, agents=(AGENT_A1,), tmp_path=tmp_path) as clients:
        for surface, session in (
            (WEBCHAT_SURFACE, SESSION_WEBCHAT),
            (MSSWWEB_SURFACE, SESSION_MSSWWEB),
        ):
            response = await clients[surface.name].post(
                _url(surface, f"/schedule-drafts/{DRAFT_ID}/confirm"),
                headers={
                    "X-Chat-Context": str(session),
                    "Idempotency-Key": f"confirm-{surface.name}",
                },
                json={"draft_hash": "sha256:" + "b" * 64},
            )
            assert response.status_code == 200, response.text
            assert response.json()["data"]["schedule_id"] == str(CONFIRMED_SCHEDULE)

    assert owner.confirmations == [
        (ConfirmationSurface.WEBCHAT, "confirm-webchat", DRAFT_ID),
        (ConfirmationSurface.MSSWWEB, "confirm-msswwweb", DRAFT_ID),
    ], "同一 service 只按 surface 写确认来源，不改契约"


async def test_handler_sets_differ_only_by_prefix(tmp_path: Path) -> None:
    def paths(surface: TaskCenterSurface) -> set[tuple[str, frozenset[str]]]:
        router = build_task_center_router(surface)
        prefix = f"/api/v1/{surface.name}"
        return {
            (route.path.removeprefix(prefix), frozenset(route.methods or ()))
            for route in router.routes
        }

    assert paths(WEBCHAT_SURFACE) == paths(MSSWWEB_SURFACE)
    assert len(paths(WEBCHAT_SURFACE)) == 10, "API-12..19 + API-25 + API-31 同一组 handler"


def test_production_app_mounts_no_msswwweb_task_center() -> None:
    routes: list[Any] = []
    for route in production_app.routes:
        expand = getattr(route, "effective_route_contexts", None)
        if expand is not None:
            routes.extend(expand())
        else:
            routes.append(route)
    mounted = {
        getattr(route, "path", "")
        for route in routes
        if str(getattr(route, "path", "")).startswith("/api/v1/")
    }
    assert not [path for path in mounted if path.startswith("/api/v1/msswwweb")], (
        "MSSWWEB 任务中心另立需求：本需求不得挂载它的路由"
    )
    assert "/api/v1/webchat/task-center/schedules" in mounted
    assert "/api/v1/webchat/schedule-drafts/{draft_id}/confirm" in mounted
