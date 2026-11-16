"""B-09x 任务中心（WebChat surface）：列表/详情/Fire/Execution/待处理/徽标/回答/动作/确认。

真实边界（业务路径不 mock）：

- **真实 Gateway 应用与路由**：直连 `muad_im_gateway.main.app`（生产路由表、api-kit 封套/
  错误目录、真实 `api/deps.py` 主体门控），不重建一套临时 FastAPI。
- **真实 Console HTTP（Owner）**：`ConsoleClient` 打真实本地 uvicorn；CHAT 主体与**当前授权
  Agent 集合**每次请求都来自 Console 的核验响应（撤销 = 改下一次核验的集合）。
- **真实 Worker HTTP（Owner）**：`WorkerClient` 打真实本地 uvicorn；读面/动作/回答/确认都按
  真实内部契约形状应答，并强制 `X-Internal-Service` —— 网关漏带服务身份时用例会红。
- **真实封套/错误码**：拒绝沿用 Owner 的错误码（`SCHEDULE_NOT_FOUND`/`SCHEDULE_ADMISSION_FAILED`），
  不另造一套文案。

断言要点：

- `agent_id` 过滤只与本次核验的授权集合取交集；撤销授权后详情/待处理/动作 404，不泄露存在性；
- 列表/待处理/徽标共享同一范围与封套，分页参数只有一种含义（`mode` 过滤后 `total` 仍准确）；
- 写请求带可信 actor 与 `Idempotency-Key`；确认路由只由真实 surface 提供确认来源
  （`X-Confirmation-Surface=WEBCHAT` + 稳定 `X-Confirmation-Ref`），没有 CHAT 会话的调用方到不了；
- 定时任务全程不产生投递：Console 只被问了一次主体，Worker 只收到任务中心读/写路径。
"""

from __future__ import annotations

import json
import socket
import threading
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
import uvicorn
from fakes import StubConsole
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from httpx import ASGITransport, AsyncClient
from muad_api.error_codes import ErrorCode
from muad_artifact_store import NfsArtifactStore
from muad_im_gateway.application.chat_principal import CHAT_SESSION_COOKIE
from muad_im_gateway.application.console_client import CHAT_PRINCIPAL_PATH, ConsoleClient
from muad_im_gateway.application.runtime_client import RuntimeClient
from muad_im_gateway.application.worker_client import WorkerClient
from muad_im_gateway.main import app

from tests.internal_service import TOKEN as INTERNAL_TOKEN
from tests.internal_service import internal_service_token  # noqa: F401  (fixture 注册)

pytestmark = pytest.mark.integration

GATEWAY_TENANT = "tenant-sched024"
USER_A = UUID("11111111-1111-4111-8111-111111111111")
USER_B = UUID("22222222-2222-4222-8222-222222222222")
AGENT_A1 = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1")
AGENT_A2 = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa2")
AGENT_REVOKED = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa3")
SESSION_ID = UUID("33333333-3333-4333-8333-333333333333")
SESSION_TOKEN = f"chat-session-{uuid.uuid4().hex}"

S1 = UUID("10000000-0000-4000-8000-000000000001")
S2 = UUID("10000000-0000-4000-8000-000000000002")
S3 = UUID("10000000-0000-4000-8000-000000000003")
S4 = UUID("10000000-0000-4000-8000-000000000004")
S5 = UUID("10000000-0000-4000-8000-000000000005")
E1 = UUID("20000000-0000-4000-8000-000000000001")
E4 = UUID("20000000-0000-4000-8000-000000000004")
I1 = UUID("30000000-0000-4000-8000-000000000001")
I4 = UUID("30000000-0000-4000-8000-000000000004")
DRAFT_ID = UUID("40000000-0000-4000-8000-000000000001")
S6_CONFIRMED = UUID("10000000-0000-4000-8000-000000000006")

BASE = "/api/v1/webchat/task-center"
CONFIRM_URL = f"/api/v1/webchat/schedule-drafts/{DRAFT_ID}/confirm"
CHAT_PRINCIPAL_FIELDS = {"tenant_id", "platform_user_id", "session_id", "auth_method"}


def _envelope(data: Any = None) -> dict[str, Any]:
    return {
        "code": "0",
        "msg": "ok",
        "data": data,
        "trace_id": "trace-s024",
        "request_id": "request-s024",
        "timestamp": "2026-11-15T08:00:00Z",
    }


def _json(data: Any, status_code: int = 200) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=_envelope(data))


def _error(code: str, status_code: int) -> JSONResponse:
    body = _envelope(None)
    body["code"] = code
    body["msg"] = code
    return JSONResponse(status_code=status_code, content=body)


def _chat_headers(context: UUID | str = SESSION_ID) -> dict[str, str]:
    return {"X-Chat-Context": str(context)}


def _at(minutes: int) -> datetime:
    return datetime(2026, 11, 15, 9, 0, tzinfo=UTC) + timedelta(minutes=minutes)


# --- 真实 Console 主体核验桩 ---------------------------------------------------------------


class _PrincipalProbe:
    """API-12 的真实 HTTP 桩：主体 + **当前授权 Agent 集合**（撤销就是改这一份）。"""

    def __init__(self, stub: StubConsole) -> None:
        self.calls: list[dict[str, str]] = []
        self.agents: list[dict[str, str]] = [
            {"agent_id": str(AGENT_A1), "key": "agent-a1", "name": "助手一"},
            {"agent_id": str(AGENT_A2), "key": "agent-a2", "name": "助手二"},
        ]
        stub.handlers[CHAT_PRINCIPAL_PATH] = self._principal

    async def _principal(self, request: Request) -> Response:
        await request.body()
        self.calls.append(dict(request.headers))
        return _json(
            {
                "principal": {
                    "tenant_id": GATEWAY_TENANT,
                    "platform_user_id": str(USER_A),
                    "session_id": str(SESSION_ID),
                    "auth_method": "USER_TOKEN",
                },
                "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                "agents": list(self.agents),
            }
        )


# --- 真实 Worker 内部面桩 -----------------------------------------------------------------


@dataclass
class _Schedule:
    schedule_id: UUID
    owner: UUID
    agent_id: UUID
    name: str
    mode: str
    status: str
    order: int
    attention_reason: str | None = None


@dataclass
class _Execution:
    execution_id: UUID
    schedule_id: UUID
    owner: UUID
    status: str = "COMPLETED"


@dataclass
class _Interaction:
    interaction_id: UUID
    schedule_id: UUID
    execution_id: UUID
    owner: UUID
    agent_id: UUID
    question: str
    deadline_at: datetime


@dataclass
class _Recorded:
    path: str
    method: str
    headers: dict[str, str]
    query: dict[str, str]
    body: dict[str, Any] | None = None


@dataclass
class _WorkerProbe:
    """Worker 内部面的真实 HTTP 桩：读面按 tenant+actor 核属，Agent 维度按声明过滤。"""

    schedules: dict[UUID, _Schedule] = field(default_factory=dict)
    executions: dict[UUID, _Execution] = field(default_factory=dict)
    interactions: dict[UUID, _Interaction] = field(default_factory=dict)
    fires: dict[UUID, list[dict[str, Any]]] = field(default_factory=dict)
    calls: list[_Recorded] = field(default_factory=list)
    #: resume 被准入拒绝：真实 Worker 用 200 + `admission_error` 表达"没生效"。
    refuse_resume: bool = False
    confirm_calls: int = 0

    def __post_init__(self) -> None:
        self._app = self._build_app()

    # -- 视图投影 ----------------------------------------------------------

    def _last_fire(self, schedule: _Schedule) -> dict[str, Any] | None:
        history = self.fires.get(schedule.schedule_id) or []
        if not history:
            return None
        latest = history[0]
        return {
            "scheduled_at": latest["scheduled_at"],
            "status": latest["status"],
            "execution_status": latest.get("execution_status"),
            "missed_count": latest.get("missed_count", 0),
            "dst_resolution": latest.get("dst_resolution"),
        }

    def _list_item(self, schedule: _Schedule) -> dict[str, Any]:
        return {
            "schedule_id": str(schedule.schedule_id),
            "agent_id": str(schedule.agent_id),
            "actor_user_id": str(USER_A),
            "name": schedule.name,
            "mode": schedule.mode,
            "status": schedule.status,
            "timezone": "Asia/Shanghai",
            "config_version": 1,
            "next_fire_at": _at(schedule.order).isoformat(),
            "last_fire": self._last_fire(schedule),
            "needs_attention": schedule.status == "NEEDS_ATTENTION",
            "pending_human": any(
                interaction.schedule_id == schedule.schedule_id
                for interaction in self.interactions.values()
            ),
        }

    def _detail(self, schedule: _Schedule) -> dict[str, Any]:
        item = self._list_item(schedule)
        item.update(
            {
                "revision_no": 1,
                "instruction": schedule.name,
                "normalized_goal": schedule.name,
                "context": {"parameters": {}, "output": {}},
                "dependencies": [],
                "delivery_intent": "NONE",
                "policy": {},
                "budget": None,
                "memory_mode": None,
                "end_reason": None,
                "attention_reason": schedule.attention_reason,
            }
        )
        return item

    def _pending_items(self, *, actor: UUID, agent_id: UUID, kind: str | None) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        if kind in (None, "HUMAN"):
            for interaction in self.interactions.values():
                if interaction.owner != actor or interaction.agent_id != agent_id:
                    continue
                schedule = self.schedules[interaction.schedule_id]
                items.append(
                    {
                        "kind": "HUMAN",
                        "interaction_id": str(interaction.interaction_id),
                        "schedule_id": str(interaction.schedule_id),
                        "execution_id": str(interaction.execution_id),
                        "schedule_name": schedule.name,
                        "question": interaction.question,
                        "deadline_at": interaction.deadline_at.isoformat(),
                        "input_schema": {"type": "object"},
                    }
                )
        if kind in (None, "NEEDS_ATTENTION"):
            for schedule in self.schedules.values():
                if schedule.owner != actor or schedule.agent_id != agent_id:
                    continue
                if schedule.status != "NEEDS_ATTENTION":
                    continue
                items.append(
                    {
                        "kind": "NEEDS_ATTENTION",
                        "schedule_id": str(schedule.schedule_id),
                        "schedule_name": schedule.name,
                        "reason": schedule.attention_reason or "ATTENTION_REQUIRED",
                        "action": "修复依赖后恢复任务",
                    }
                )
        return items

    def _owned(self, raw_id: str, request: Request) -> _Schedule | None:
        """读/写面按 tenant+actor 核属：别人的 Schedule 与不存在同形（404）。"""
        schedule = self.schedules.get(UUID(raw_id))
        actor = UUID(request.headers["x-actor-user-id"])
        if schedule is None or schedule.owner != actor:
            return None
        return schedule

    # -- 路由 --------------------------------------------------------------

    def _build_app(self) -> FastAPI:
        probe_app = FastAPI()

        @probe_app.middleware("http")
        async def _record(request: Request, call_next: Any) -> Response:
            # 真实内部面只认受信服务身份：网关漏带时用例必须红。
            if request.headers.get("x-internal-service") != INTERNAL_TOKEN:
                return _error(str(ErrorCode.FORBIDDEN), 403)
            raw = await request.body()
            self.calls.append(
                _Recorded(
                    path=request.url.path,
                    method=request.method,
                    headers=dict(request.headers),
                    query=dict(request.query_params),
                    body=json.loads(raw) if raw else None,
                )
            )
            return await call_next(request)

        @probe_app.get("/internal/schedules")
        async def _list_schedules(request: Request) -> Response:
            actor = UUID(request.headers["x-actor-user-id"])
            agent_filter = request.query_params.get("agent_id")
            status_filter = request.query_params.get("status")
            rows = [
                schedule
                for schedule in self.schedules.values()
                if schedule.owner == actor
                and (agent_filter is None or str(schedule.agent_id) == agent_filter)
                and (status_filter is None or schedule.status == status_filter)
            ]
            rows.sort(key=lambda schedule: (-schedule.order, str(schedule.schedule_id)))
            page = int(request.query_params.get("page", "1"))
            size = int(request.query_params.get("page_size", "20"))
            window = rows[(page - 1) * size : page * size]
            return _json(
                {
                    "items": [self._list_item(schedule) for schedule in window],
                    "page": page,
                    "page_size": size,
                    "total": len(rows),
                }
            )

        @probe_app.get("/internal/schedules/{schedule_id}")
        async def _get_schedule(schedule_id: str, request: Request) -> Response:
            schedule = self._owned(schedule_id, request)
            if schedule is None:
                return _error(str(ErrorCode.SCHEDULE_NOT_FOUND), 404)
            return _json(self._detail(schedule))

        @probe_app.get("/internal/schedules/{schedule_id}/fires")
        async def _list_fires(schedule_id: str, request: Request) -> Response:
            if self._owned(schedule_id, request) is None:
                return _error(str(ErrorCode.SCHEDULE_NOT_FOUND), 404)
            history = self.fires.get(UUID(schedule_id), [])
            return _json(
                {
                    "items": history,
                    "dst_events": [],
                    "page": int(request.query_params.get("page", "1")),
                    "page_size": int(request.query_params.get("page_size", "20")),
                    "total": len(history),
                }
            )

        @probe_app.post("/internal/schedules/{schedule_id}/{action}")
        async def _schedule_action(schedule_id: str, action: str, request: Request) -> Response:
            schedule = self._owned(schedule_id, request)
            if schedule is None:
                return _error(str(ErrorCode.SCHEDULE_NOT_FOUND), 404)
            body = json.loads(await request.body() or b"{}")
            payload: dict[str, Any] = {"schedule_id": str(schedule.schedule_id), "action": action}
            if self.refuse_resume and action == "resume":
                payload["admission_error"] = {
                    "code": "SCHEDULE_DEPENDENCY_MISSING",
                    "permanent": True,
                    "repair": "补上缺失的 Skill 后再恢复",
                }
            elif action == "pause":
                schedule.status = "PAUSED"
            elif action == "resume":
                schedule.status = "ACTIVE"
            elif action == "cancel":
                schedule.status = "CANCELLED"
            payload["applied_scope"] = body.get("scope")
            return _json(payload)

        @probe_app.get("/internal/executions/{execution_id}")
        async def _get_execution(execution_id: str, request: Request) -> Response:
            execution = self.executions.get(UUID(execution_id))
            actor = UUID(request.headers["x-actor-user-id"])
            if execution is None or execution.owner != actor:
                return _error(str(ErrorCode.COMMON_NOT_FOUND), 404)
            return _json(
                {
                    "execution_id": str(execution.execution_id),
                    "status": execution.status,
                    "wait_kind": None,
                    "wait_payload": None,
                    "result": {"text": "ok"},
                    "result_artifacts": [],
                    "error_code": None,
                    "error_message": None,
                    "failure_class": None,
                    "started_at": _at(0).isoformat(),
                    "finished_at": _at(1).isoformat(),
                    "snapshot_schema_version": 4,
                    "snapshot_bytes": 1024,
                }
            )

        @probe_app.get("/internal/task-center/pending")
        async def _pending(request: Request) -> Response:
            actor = UUID(request.headers["x-actor-user-id"])
            agent = UUID(request.query_params["agent_id"])
            kind = request.query_params.get("kind")
            items = self._pending_items(actor=actor, agent_id=agent, kind=kind)
            items.sort(key=lambda item: (item["kind"], item["schedule_id"]))
            page = int(request.query_params.get("page", "1"))
            size = int(request.query_params.get("page_size", "20"))
            return _json(
                {
                    "items": items[(page - 1) * size : page * size],
                    "page": page,
                    "page_size": size,
                    "total": len(items),
                }
            )

        @probe_app.get("/internal/task-center/badges")
        async def _badges(request: Request) -> Response:
            actor = UUID(request.headers["x-actor-user-id"])
            agent = UUID(request.query_params["agent_id"])
            items = self._pending_items(actor=actor, agent_id=agent, kind=None)
            return _json(
                {
                    "pending_human": sum(1 for item in items if item["kind"] == "HUMAN"),
                    "needs_attention": sum(
                        1 for item in items if item["kind"] == "NEEDS_ATTENTION"
                    ),
                }
            )

        @probe_app.post("/internal/human-interactions/{interaction_id}/respond")
        async def _respond(interaction_id: str, request: Request) -> Response:
            interaction = self.interactions.get(UUID(interaction_id))
            actor = UUID(request.headers["x-actor-user-id"])
            agent = request.headers.get("x-agent-id")
            if interaction is None or interaction.owner != actor:
                return _error(str(ErrorCode.TASK_ACCESS_DENIED), 403)
            if agent is not None and str(interaction.agent_id) != agent:
                return _error(str(ErrorCode.TASK_ACCESS_DENIED), 403)
            return _json(
                {
                    "interaction_id": str(interaction.interaction_id),
                    "status": "RESOLVED",
                    "execution_id": str(interaction.execution_id),
                    "execution_status": "QUEUED",
                }
            )

        @probe_app.get("/internal/schedule-drafts/{draft_id}/status")
        async def _draft_status(draft_id: str, request: Request) -> Response:
            if UUID(request.headers["x-actor-user-id"]) != USER_A:
                return _error(str(ErrorCode.SCHEDULE_NOT_FOUND), 404)
            return _json(
                {
                    "draft_id": draft_id,
                    "status": "CONFIRMED",
                    "schedule_id": str(S6_CONFIRMED),
                }
            )

        @probe_app.post("/internal/schedule-drafts/{draft_id}/confirm")
        async def _confirm(draft_id: str, request: Request) -> Response:
            surface = request.headers.get("x-confirmation-surface")
            ref = request.headers.get("x-confirmation-ref")
            if not surface or not ref:
                # 真实 Worker 的口径：没有用户动作来源就不确认（模型工具走不到这里）。
                return _error(str(ErrorCode.SCHEDULE_CONFIRMATION_REQUIRED), 409)
            self.confirm_calls += 1
            return _json(
                {
                    "schedule_id": str(S6_CONFIRMED),
                    "status": "ACTIVE",
                    "revision_no": 1,
                    "config_version": 1,
                    "next_fire_at": _at(5).isoformat(),
                }
            )

        return probe_app

    def start(self) -> None:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = int(sock.getsockname()[1])
        config = uvicorn.Config(self._app, host="127.0.0.1", port=port, log_level="error")
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        deadline = time.monotonic() + 15.0
        while not self._server.started and time.monotonic() < deadline:
            time.sleep(0.02)
        if not self._server.started:
            raise RuntimeError("worker probe did not start")
        self.url = f"http://127.0.0.1:{port}"

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5)

    def paths(self) -> list[str]:
        return [call.path for call in self.calls]

    _app: FastAPI = field(init=False, repr=False)
    _server: uvicorn.Server | None = field(init=False, default=None)
    _thread: threading.Thread | None = field(init=False, default=None)
    url: str = field(init=False, default="")


def _seed(probe: _WorkerProbe) -> None:
    probe.schedules = {
        S1: _Schedule(S1, USER_A, AGENT_A1, "A 客户周报", "SKILL", "ACTIVE", order=5),
        S2: _Schedule(S2, USER_A, AGENT_A2, "线索汇总", "AGENTIC", "PAUSED", order=4),
        S3: _Schedule(S3, USER_A, AGENT_REVOKED, "已撤销助手的提醒", "REMINDER", "ACTIVE", order=3),
        S4: _Schedule(S4, USER_B, AGENT_A1, "别人的任务", "SKILL", "ACTIVE", order=2),
        S5: _Schedule(
            S5,
            USER_A,
            AGENT_A2,
            "缺工具的任务",
            "REMINDER",
            "NEEDS_ATTENTION",
            order=1,
            attention_reason="TOOL_MISSING",
        ),
    }
    probe.executions = {
        E1: _Execution(E1, S1, USER_A),
        E4: _Execution(E4, S4, USER_B),
    }
    probe.interactions = {
        I1: _Interaction(I1, S1, E1, USER_A, AGENT_A1, "请选择客户", _at(30)),
        I4: _Interaction(I4, S4, E4, USER_B, AGENT_A1, "别人的问题", _at(30)),
    }
    probe.fires = {
        S1: [
            {
                "fire_id": str(uuid.uuid4()),
                "scheduled_at": _at(0).isoformat(),
                "status": "EXECUTION_CREATED",
                "skip_reason": None,
                "missed_count": 0,
                "dst_resolution": None,
                "admission_error_code": None,
                "execution_id": str(E1),
                "execution_status": "COMPLETED",
            }
        ]
    }


# --- 夹具 ---------------------------------------------------------------------------------


@pytest.fixture
async def stub_console() -> AsyncIterator[StubConsole]:
    stub = StubConsole()
    stub.start()
    try:
        yield stub
    finally:
        stub.stop()


@pytest.fixture
def principal(stub_console: StubConsole) -> _PrincipalProbe:
    return _PrincipalProbe(stub_console)


@pytest.fixture
async def worker_probe() -> AsyncIterator[_WorkerProbe]:
    probe = _WorkerProbe()
    _seed(probe)
    probe.start()
    try:
        yield probe
    finally:
        probe.stop()


_STATE_NAMES = ("console_client", "runtime_client", "worker_client", "artifact_store", "tenant_id")


@asynccontextmanager
async def _gateway_client(
    stub_console: StubConsole,
    probe: _WorkerProbe,
    *,
    artifact_root: Path,
    cookies: dict[str, str] | None = None,
) -> AsyncIterator[AsyncClient]:
    """真实 Gateway 应用 + 真实 Console/Worker 客户端（只注入装配根会发布的状态值）。"""
    previous: dict[str, Any] = {name: getattr(app.state, name, None) for name in _STATE_NAMES}
    console = ConsoleClient(stub_console.url)
    worker = WorkerClient(probe.url, service_token=INTERNAL_TOKEN)
    # 本文件不发起产物下载；Runtime 端口仍指向真实 socket（不会被调用）。
    runtime = RuntimeClient(probe.url, service_token=INTERNAL_TOKEN)
    app.state.console_client = console
    app.state.runtime_client = runtime
    app.state.worker_client = worker
    app.state.artifact_store = NfsArtifactStore(artifact_root)
    app.state.tenant_id = GATEWAY_TENANT
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://gateway.test",
            cookies={CHAT_SESSION_COOKIE: SESSION_TOKEN} if cookies is None else cookies,
        ) as client:
            yield client
    finally:
        for name, value in previous.items():
            if value is None:
                with suppress(AttributeError):
                    delattr(app.state, name)
            else:
                setattr(app.state, name, value)
        await console.aclose()
        await worker.aclose()
        await runtime.aclose()


@pytest.fixture
async def api(
    stub_console: StubConsole,
    principal: _PrincipalProbe,
    worker_probe: _WorkerProbe,
    tmp_path: Path,
) -> AsyncIterator[AsyncClient]:
    async with _gateway_client(stub_console, worker_probe, artifact_root=tmp_path / "artifacts") as client:
        yield client


# --- 列表 / 详情 / Fire / Execution --------------------------------------------------------


async def test_list_filters_by_authorized_agents(api: AsyncClient, worker_probe: _WorkerProbe) -> None:
    response = await api.get(f"{BASE}/schedules", headers=_chat_headers())

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert [item["schedule_id"] for item in data["items"]] == [str(S1), str(S2), str(S5)]
    assert data["total"] == 3, "撤销授权 Agent 的任务与别人的任务都不计入 total"
    assert data["page"] == 1 and data["page_size"] == 20
    assert set(data["items"][0]) == {
        "schedule_id",
        "agent_id",
        "name",
        "mode",
        "status",
        "timezone",
        "config_version",
        "next_fire_at",
        "last_fire",
        "needs_attention",
        "pending_human",
    }
    # 读面只带服务身份 + 已验证 actor；浏览器带来的主体字段没有位置。
    call = worker_probe.calls[0]
    assert call.headers["x-internal-service"] == INTERNAL_TOKEN
    assert call.headers["x-tenant-id"] == GATEWAY_TENANT
    assert call.headers["x-actor-user-id"] == str(USER_A)
    assert "agent_id" not in call.query


async def test_list_mode_status_and_paging_are_exact(
    api: AsyncClient, worker_probe: _WorkerProbe
) -> None:
    agentic = await api.get(f"{BASE}/schedules", headers=_chat_headers(), params={"mode": "AGENTIC"})
    assert agentic.status_code == 200, agentic.text
    assert [item["schedule_id"] for item in agentic.json()["data"]["items"]] == [str(S2)]
    assert agentic.json()["data"]["total"] == 1, "mode 过滤后 total 必须是过滤后的条数"

    active = await api.get(f"{BASE}/schedules", headers=_chat_headers(), params={"status": "ACTIVE"})
    assert [item["schedule_id"] for item in active.json()["data"]["items"]] == [str(S1)]

    page_two = await api.get(
        f"{BASE}/schedules", headers=_chat_headers(), params={"page": 2, "page_size": 2}
    )
    assert [item["schedule_id"] for item in page_two.json()["data"]["items"]] == [str(S5)]
    assert page_two.json()["data"]["total"] == 3

    by_agent = await api.get(
        f"{BASE}/schedules", headers=_chat_headers(), params={"agent_id": str(AGENT_A2)}
    )
    assert [item["schedule_id"] for item in by_agent.json()["data"]["items"]] == [str(S2), str(S5)]
    assert worker_probe.calls[-1].query["agent_id"] == str(AGENT_A2)


async def test_list_reads_every_worker_page_before_slicing(
    api: AsyncClient, worker_probe: _WorkerProbe
) -> None:
    """范围超过单页上限时，网关按同一稳定顺序读完全段再分页（否则首页会缺项）。"""
    for index in range(6, 106):
        schedule_id = UUID(f"10000000-0000-4000-8000-{index:012d}")
        worker_probe.schedules[schedule_id] = _Schedule(
            schedule_id, USER_A, AGENT_A1, f"批量任务 {index}", "REMINDER", "ACTIVE", order=-index
        )

    response = await api.get(f"{BASE}/schedules", headers=_chat_headers(), params={"page_size": 100})

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["total"] == 103, "100 条历史 + 3 条授权范围既有任务"
    assert len(data["items"]) == 100
    assert len([path for path in worker_probe.paths() if path == "/internal/schedules"]) == 2


async def test_detail_and_fires_are_agent_scoped(api: AsyncClient, principal: _PrincipalProbe) -> None:
    detail = await api.get(f"{BASE}/schedules/{S1}", headers=_chat_headers())
    assert detail.status_code == 200, detail.text
    assert detail.json()["data"]["agent_id"] == str(AGENT_A1)
    assert detail.json()["data"]["revision_no"] == 1

    fires = await api.get(f"{BASE}/schedules/{S1}/fires", headers=_chat_headers())
    assert fires.status_code == 200, fires.text
    assert [item["status"] for item in fires.json()["data"]["items"]] == ["EXECUTION_CREATED"]
    assert fires.json()["data"]["dst_events"] == []

    revoked = await api.get(f"{BASE}/schedules/{S3}", headers=_chat_headers())
    assert revoked.status_code == 404
    assert revoked.json()["code"] == str(ErrorCode.SCHEDULE_NOT_FOUND)
    revoked_fires = await api.get(f"{BASE}/schedules/{S3}/fires", headers=_chat_headers())
    assert revoked_fires.status_code == 404

    other_user = await api.get(f"{BASE}/schedules/{S4}", headers=_chat_headers())
    assert other_user.status_code == 404

    # 撤销 AGENT_A2 之后，它的任务对同一用户也不可见（既不是 200，也不泄露存在性）。
    principal.agents = [{"agent_id": str(AGENT_A1), "key": "agent-a1", "name": "助手一"}]
    after_revoke = await api.get(f"{BASE}/schedules/{S2}", headers=_chat_headers())
    assert after_revoke.status_code == 404


async def test_execution_detail_is_owner_scoped(api: AsyncClient) -> None:
    own = await api.get(f"{BASE}/executions/{E1}", headers=_chat_headers())
    assert own.status_code == 200, own.text
    assert own.json()["data"]["status"] == "COMPLETED"
    assert own.json()["data"]["snapshot_schema_version"] == 4

    other = await api.get(f"{BASE}/executions/{E4}", headers=_chat_headers())
    assert other.status_code == 404, "别人的执行一律 404（不泄露存在性）"


# --- 待处理 / 徽标 ------------------------------------------------------------------------


async def test_pending_merges_agents_and_keeps_one_scope(api: AsyncClient) -> None:
    response = await api.get(f"{BASE}/pending", headers=_chat_headers())

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert [item["kind"] for item in data["items"]] == ["HUMAN", "NEEDS_ATTENTION"]
    assert data["total"] == 2, "撤销授权 Agent 的待处理与别人的待处理都不出现"

    humans = await api.get(f"{BASE}/pending", headers=_chat_headers(), params={"kind": "HUMAN"})
    assert [item["interaction_id"] for item in humans.json()["data"]["items"]] == [str(I1)]
    assert humans.json()["data"]["total"] == 1

    by_agent = await api.get(
        f"{BASE}/pending", headers=_chat_headers(), params={"agent_id": str(AGENT_A2)}
    )
    assert [item["kind"] for item in by_agent.json()["data"]["items"]] == ["NEEDS_ATTENTION"]

    paged = await api.get(f"{BASE}/pending", headers=_chat_headers(), params={"page": 2, "page_size": 1})
    assert [item["kind"] for item in paged.json()["data"]["items"]] == ["NEEDS_ATTENTION"]
    assert paged.json()["data"]["total"] == 2


async def test_badges_match_list_scope(api: AsyncClient, principal: _PrincipalProbe) -> None:
    badges = await api.get(f"{BASE}/badges", headers=_chat_headers())
    assert badges.status_code == 200, badges.text
    assert badges.json()["data"] == {"pending_human": 1, "needs_attention": 1}

    only_a1 = await api.get(f"{BASE}/badges", headers=_chat_headers(), params={"agent_id": str(AGENT_A1)})
    assert only_a1.json()["data"] == {"pending_human": 1, "needs_attention": 0}

    revoked = await api.get(
        f"{BASE}/badges", headers=_chat_headers(), params={"agent_id": str(AGENT_REVOKED)}
    )
    assert revoked.status_code == 404
    principal.agents = []
    empty = await api.get(f"{BASE}/badges", headers=_chat_headers())
    assert empty.json()["data"] == {"pending_human": 0, "needs_attention": 0}


# --- 回答 / 动作 / 确认（写） --------------------------------------------------------------


async def test_respond_human_carries_actor_and_idempotency(
    api: AsyncClient, worker_probe: _WorkerProbe
) -> None:
    response = await api.post(
        f"{BASE}/human-interactions/{I1}/respond",
        headers={**_chat_headers(), "Idempotency-Key": "respond-key-1"},
        json={"response": {"customer": "A"}},
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"] == {
        "interaction_id": str(I1),
        "status": "RESOLVED",
        "execution_id": str(E1),
        "execution_status": "QUEUED",
    }
    call = worker_probe.calls[-1]
    assert call.headers["x-actor-user-id"] == str(USER_A)
    assert call.headers["idempotency-key"] == "respond-key-1"
    # 两个授权 Agent 时不能替调用方声明归属（由 Worker 按 owner 裁决）。
    assert "x-agent-id" not in call.headers
    assert call.body == {"response": {"customer": "A"}}

    declared = await api.post(
        f"{BASE}/human-interactions/{I1}/respond",
        headers={**_chat_headers(), "Idempotency-Key": "respond-key-2"},
        params={"agent_id": str(AGENT_A1)},
        json={"response": {"customer": "A"}},
    )
    assert declared.status_code == 200, declared.text
    assert worker_probe.calls[-1].headers["x-agent-id"] == str(AGENT_A1)

    missing_key = await api.post(
        f"{BASE}/human-interactions/{I1}/respond",
        headers=_chat_headers(),
        json={"response": {"customer": "A"}},
    )
    assert missing_key.status_code == 422, "写请求必须带 Idempotency-Key"
    assert len(worker_probe.calls) == 2, "缺幂等键的请求不得到达 Owner"

    other_user = await api.post(
        f"{BASE}/human-interactions/{I4}/respond",
        headers={**_chat_headers(), "Idempotency-Key": "respond-key-3"},
        json={"response": {"customer": "A"}},
    )
    assert other_user.status_code == 403, "别人问题由 Owner 判 TASK_ACCESS_DENIED"


async def test_respond_with_no_authorized_agent_is_denied(
    api: AsyncClient, principal: _PrincipalProbe, worker_probe: _WorkerProbe
) -> None:
    principal.agents = []
    before = len(worker_probe.calls)
    response = await api.post(
        f"{BASE}/human-interactions/{I1}/respond",
        headers={**_chat_headers(), "Idempotency-Key": "respond-key-4"},
        json={"response": {"customer": "A"}},
    )

    assert response.status_code == 403
    assert response.json()["code"] == str(ErrorCode.TASK_ACCESS_DENIED)
    assert len(worker_probe.calls) == before, "没有授权 Agent 时不会写入任何回答"


async def test_schedule_action_returns_post_action_detail(
    api: AsyncClient, worker_probe: _WorkerProbe
) -> None:
    response = await api.post(
        f"{BASE}/schedules/{S1}/actions",
        headers={**_chat_headers(), "Idempotency-Key": "action-key-1"},
        json={"action": "pause"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["status"] == "PAUSED"
    assert worker_probe.paths()[-3:] == [
        f"/internal/schedules/{S1}",
        f"/internal/schedules/{S1}/pause",
        f"/internal/schedules/{S1}",
    ], "动作前核属、动作、动作后重读（返回的是生效之后的事实）"
    assert worker_probe.calls[-2].headers["idempotency-key"] == "action-key-1"

    cancelled = await api.post(
        f"{BASE}/schedules/{S1}/actions",
        headers={**_chat_headers(), "Idempotency-Key": "action-key-2"},
        json={"action": "cancel", "scope": "ALL_ACTIVE"},
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["data"]["status"] == "CANCELLED"
    assert worker_probe.calls[-2].body["scope"] == "ALL_ACTIVE"


async def test_schedule_action_on_revoked_agent_is_404_without_touching_worker(
    api: AsyncClient, worker_probe: _WorkerProbe
) -> None:
    before = len(worker_probe.calls)
    response = await api.post(
        f"{BASE}/schedules/{S3}/actions",
        headers={**_chat_headers(), "Idempotency-Key": "action-key-3"},
        json={"action": "cancel"},
    )
    assert response.status_code == 404
    assert response.json()["code"] == str(ErrorCode.SCHEDULE_NOT_FOUND)
    assert len(worker_probe.calls) == before + 1, "只读了详情，没有发出动作"


async def test_refused_resume_is_reported_as_failure(
    api: AsyncClient, worker_probe: _WorkerProbe
) -> None:
    worker_probe.refuse_resume = True
    response = await api.post(
        f"{BASE}/schedules/{S2}/actions",
        headers={**_chat_headers(), "Idempotency-Key": "action-key-4"},
        json={"action": "resume"},
    )

    assert response.status_code == 422, "准入拒绝不能被包装成成功"
    body = response.json()
    assert body["code"] == str(ErrorCode.SCHEDULE_ADMISSION_FAILED)
    assert body["data"]["repair"] == "补上缺失的 Skill 后再恢复"


async def test_confirm_supplies_surface_and_stable_ref(
    api: AsyncClient, worker_probe: _WorkerProbe
) -> None:
    response = await api.post(
        CONFIRM_URL,
        headers={**_chat_headers(), "Idempotency-Key": "confirm-key-1"},
        json={"draft_hash": "sha256:" + "a" * 64, "name": "A 客户周报"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["schedule_id"] == str(S6_CONFIRMED)
    assert worker_probe.confirm_calls == 1
    call = worker_probe.calls[-1]
    assert call.path == f"/internal/schedule-drafts/{DRAFT_ID}/confirm"
    assert call.headers["x-confirmation-surface"] == "WEBCHAT"
    assert call.headers["x-confirmation-ref"] == "confirm-key-1", "幂等键就是稳定的用户动作引用"
    assert call.headers["x-actor-user-id"] == str(USER_A)
    assert call.body == {"draft_hash": "sha256:" + "a" * 64, "name": "A 客户周报"}


async def test_draft_status_read_is_owner_scoped_and_does_not_confirm(
    api: AsyncClient, worker_probe: _WorkerProbe
) -> None:
    response = await api.get(
        f"/api/v1/webchat/schedule-drafts/{DRAFT_ID}",
        headers=_chat_headers(),
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"] == {
        "draft_id": str(DRAFT_ID),
        "status": "CONFIRMED",
        "schedule_id": str(S6_CONFIRMED),
    }
    call = worker_probe.calls[-1]
    assert call.path == f"/internal/schedule-drafts/{DRAFT_ID}/status"
    assert call.headers["x-actor-user-id"] == str(USER_A)
    assert "x-agent-id" not in call.headers, "状态读面按 owner 核属，不要求客户端知道 Agent"
    assert worker_probe.confirm_calls == 0


async def test_confirm_without_user_session_never_reaches_worker(
    stub_console: StubConsole,
    principal: _PrincipalProbe,
    worker_probe: _WorkerProbe,
    tmp_path: Path,
) -> None:
    """模型工具路径没有 CHAT 会话：确认路由在这里就拒了，Owner 一个请求都收不到。"""
    async with _gateway_client(
        stub_console, worker_probe, artifact_root=tmp_path / "artifacts", cookies={}
    ) as client:
        response = await client.post(
            CONFIRM_URL,
            headers={**_chat_headers(), "Idempotency-Key": "confirm-key-2"},
            json={"draft_hash": "sha256:" + "a" * 64},
        )

    assert response.status_code == 401
    assert worker_probe.calls == []
    assert principal.calls == [], "没有 CHAT Cookie 连 Console 核验都不会发生"


# --- 认证 / 不投递 ------------------------------------------------------------------------


async def test_reads_require_chat_principal(api: AsyncClient, worker_probe: _WorkerProbe) -> None:
    missing_context = await api.get(f"{BASE}/schedules")
    assert missing_context.status_code == 422, "缺少 X-Chat-Context 在触达 Owner 之前就被拒"

    changed_context = await api.get(
        f"{BASE}/schedules", headers=_chat_headers(context=uuid.uuid4())
    )
    assert changed_context.status_code == 409, "上下文与核验主体不一致"
    assert worker_probe.calls == []


async def test_task_center_never_writes_delivery_or_notifications(
    api: AsyncClient, principal: _PrincipalProbe, worker_probe: _WorkerProbe
) -> None:
    listing = await api.get(f"{BASE}/schedules", headers=_chat_headers())
    assert listing.status_code == 200, listing.text
    for path in (
        f"{BASE}/schedules/{S1}",
        f"{BASE}/schedules/{S1}/fires",
        f"{BASE}/pending",
        f"{BASE}/badges",
        f"{BASE}/executions/{E1}",
    ):
        response = await api.get(path, headers=_chat_headers())
        assert response.status_code == 200, response.text

    # WebChat 只 pull：Console 只被问过主体，Worker 只收到任务中心路径。
    assert principal.calls, "主体每次请求都经 Console 核验"
    assert all(headers.get("x-tenant-id") == GATEWAY_TENANT for headers in principal.calls)
    assert set(worker_probe.paths()) <= {
        "/internal/schedules",
        f"/internal/schedules/{S1}",
        f"/internal/schedules/{S1}/fires",
        "/internal/task-center/pending",
        "/internal/task-center/badges",
        f"/internal/executions/{E1}",
    }, "不得出现投递/通知类写入"
    body = listing.text
    assert str(USER_A) not in body and GATEWAY_TENANT not in body, "主体/租户字段不进响应"
    assert CHAT_PRINCIPAL_FIELDS.isdisjoint(json.loads(body)["data"]["items"][0])
