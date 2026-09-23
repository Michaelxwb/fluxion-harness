"""真实浏览器 E2E 的 Console 后端：api-kit 封套 + 会话原语 + 静态前端产物。"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from muad_api import (
    ApiResponse,
    AppError,
    ErrorCode,
    install_api_foundation,
    install_console_security,
    install_health_probes,
    ok,
    require_session,
)
from muad_common import SharedSettings
from muad_console_platform.api.deps import get_account_tenant_id as console_get_account_tenant_id
from muad_console_platform.api.deps import get_worker_client as console_get_worker_client
from muad_console_platform.api.schedules import router as console_schedules_router
from muad_console_platform.api.tasks import router as console_tasks_router
from muad_console_platform.infrastructure.worker_client import WorkerAdminClient
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / "apps/console-platform/frontend/dist"
MESSAGES_FILE = ROOT / "config/api-messages.yaml"

SESSION_COOKIE = "muad_session"
E2E_SESSION_TOKEN = "e2e-session"
E2E_ACCOUNT = {
    "id": str(uuid.uuid5(uuid.NAMESPACE_DNS, "e2e-admin")),
    "username": "admin",
    "display_name": "E2E Admin",
    "role": "ADMIN",
}


class LoginPayload(BaseModel):
    username: str
    password: str


class SessionVerifier:
    async def verify(self, session_token: str) -> dict[str, str] | None:
        return dict(E2E_ACCOUNT) if session_token == E2E_SESSION_TOKEN else None


class RoleResolver:
    async def roles_for(self, principal: dict[str, str]) -> set[str]:
        return {principal["role"]}


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
catalog = install_api_foundation(app, messages_file=MESSAGES_FILE, default_locale="zh-CN")
install_console_security(app, SessionVerifier(), RoleResolver())
install_health_probes(app, {})


@app.post("/api/v1/auth/login")
async def login(payload: LoginPayload, response: Response) -> ApiResponse[Any]:
    if payload.username != "admin" or payload.password != "admin123":
        raise AppError(ErrorCode.UNAUTHORIZED)
    response.set_cookie(SESSION_COOKIE, E2E_SESSION_TOKEN, httponly=True, samesite="lax", path="/")
    return ok(catalog, dict(E2E_ACCOUNT))


@app.post("/api/v1/auth/logout")
async def logout(response: Response) -> ApiResponse[Any]:
    response.delete_cookie(SESSION_COOKIE, path="/")
    return ok(catalog, {"logged_out": True})


SessionPrincipal = Annotated[dict[str, Any], Depends(require_session)]


@app.get("/api/v1/auth/me")
async def me(principal: SessionPrincipal) -> ApiResponse[Any]:
    return ok(catalog, principal)


# 装载生产 Task/Schedule 路由：租户固定为 E2E 租户，Worker 客户端指向真实 Worker HTTP。
_settings = SharedSettings()


class _E2EWorkerClient:
    """真实 WorkerAdminClient 的薄包装：支持注入真实 transport 级失败（非浏览器 mock）。"""

    def __init__(self, inner: WorkerAdminClient) -> None:
        self._inner = inner
        self.failing = False

    def __getattr__(self, name: str) -> Any:
        attribute = getattr(self._inner, name)
        if name == "aclose" or not callable(attribute):
            return attribute

        async def call(*args: Any, **kwargs: Any) -> Any:
            if self.failing:
                raise AppError(ErrorCode.COMMON_INTERNAL_ERROR)
            return await attribute(*args, **kwargs)

        return call


_worker = _E2EWorkerClient(
    WorkerAdminClient(_settings.agent_worker_url, service_token=_settings.internal_service_token)
)
# 生产路由的租户取自登录账号；E2E 会话主体不是 ConsoleAccount，固定为 E2E 租户。
app.dependency_overrides[console_get_account_tenant_id] = lambda: _settings.default_tenant_id
app.dependency_overrides[console_get_worker_client] = lambda: _worker
app.include_router(console_tasks_router)
app.include_router(console_schedules_router)


@app.post("/__e2e/worker-failure")
async def toggle_worker_failure(payload: dict[str, bool]) -> ApiResponse[Any]:
    _worker.failing = bool(payload.get("failing"))
    return ok(catalog, {"failing": _worker.failing})


@app.post("/__e2e/seed-task")
async def seed_task(payload: dict[str, Any]) -> ApiResponse[Any]:
    """按 E2E 租户写入真实 Task 行（测试数据种子，读路径仍走真实 Worker → PG）。"""
    import uuid as uuid_module
    from datetime import UTC, datetime, timedelta

    from muad_agent_worker.infrastructure.db import get_session_factory
    from muad_agent_worker.infrastructure.models.task import TaskEvent, TaskExecution

    task_id = uuid_module.uuid4()
    now = datetime.now(UTC)
    deadline_at = payload.get("deadline_at")
    parent_id = payload.get("parent_id")
    schedule_id = payload.get("schedule_id")
    values: dict[str, Any] = {
        "id": task_id,
        "tenant_id": _settings.default_tenant_id,
        "schedule_id": uuid_module.UUID(str(schedule_id)) if schedule_id else None,
        "parent_id": uuid_module.UUID(str(parent_id)) if parent_id else None,
        "root_id": uuid_module.UUID(str(payload["root_id"])) if payload.get("root_id") else None,
        "item_key": payload.get("item_key"),
        "agent_id": uuid_module.UUID(str(payload.get("agent_id") or uuid_module.uuid4())),
        "actor_user_id": uuid_module.uuid4(),
        "intent_key": str(payload.get("intent_key") or "e2e_policy_check"),
        "skill_id": uuid_module.uuid4(),
        "skill_artifact_id": uuid_module.uuid4(),
        "trigger_type": str(payload.get("trigger_type") or "IMMEDIATE"),
        "execution_mode": "ASYNC",
        "task_type": str(payload.get("task_type") or "SKILL"),
        "status": str(payload.get("status") or "QUEUED"),
        "input_json": {},
        "execution_snapshot_schema_version": 1,
        "execution_snapshot_json": {"schema_version": 1},
        "snapshot_hash": "sha256:" + "f" * 64,
        "idempotency_key": f"e2e-seed-{task_id}",
        "priority": 100,
        "attempt": 0,
        "max_attempts": 3,
        # 种子是 UI 夹具，不是待执行任务：默认 not_before 推到一天后，避免真实 Worker
        # 在断言前 claim 掉（写入 CLAIMED 事件/改状态），造成与时序相关的偶发失败。
        "not_before": (
            datetime.fromisoformat(str(payload["not_before"]))
            if payload.get("not_before")
            else now + timedelta(days=1)
        ),
        "deadline_at": (
            datetime.fromisoformat(deadline_at) if deadline_at else now + timedelta(hours=1)
        ),
        "finished_at": (
            now if str(payload.get("status")) in {"COMPLETED", "FAILED", "CANCELLED"} else None
        ),
        "error_code": payload.get("error_code"),
        "error_message": payload.get("error_message"),
        "delivery_mode": "NONE",
        "delivery_status": "NONE",
        "delivery_key": f"task:{task_id}:final",
        "delivery_attempts": 0,
    }
    event_types = payload.get("events") or []
    session_factory = get_session_factory()
    async with session_factory() as session:
        async with session.begin():
            session.add(TaskExecution(**values))
            await session.flush()
            for index, event_type in enumerate(event_types, start=1):
                session.add(
                    TaskEvent(
                        tenant_id=_settings.default_tenant_id,
                        task_id=task_id,
                        seq=index,
                        event_type=str(event_type),
                        payload_json={},
                    )
                )
    return ok(catalog, {"task_id": str(task_id)})


@app.post("/__e2e/update-task")
async def update_task(payload: dict[str, Any]) -> ApiResponse[Any]:
    """更新 E2E 租户下真实 Task 的状态（模拟 Worker 侧推进），并可追加事件。"""
    import uuid as uuid_module
    from datetime import UTC, datetime

    from muad_agent_worker.infrastructure.db import get_session_factory
    from muad_agent_worker.infrastructure.models.task import TaskEvent, TaskExecution
    from sqlalchemy import func, select, update as sql_update

    task_id = uuid_module.UUID(str(payload["task_id"]))
    session_factory = get_session_factory()
    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                sql_update(TaskExecution)
                .where(
                    TaskExecution.id == task_id,
                    TaskExecution.tenant_id == _settings.default_tenant_id,
                )
                .values(status=str(payload.get("status")), update_time=datetime.now(UTC))
            )
            event_type = payload.get("event_type")
            if event_type:
                max_seq = (
                    await session.execute(
                        select(func.coalesce(func.max(TaskEvent.seq), 0)).where(
                            TaskEvent.task_id == task_id
                        )
                    )
                ).scalar_one()
                session.add(
                    TaskEvent(
                        tenant_id=_settings.default_tenant_id,
                        task_id=task_id,
                        seq=int(max_seq) + 1,
                        event_type=str(event_type),
                        payload_json={},
                    )
                )
    return ok(catalog, {"updated": True})


@app.post("/__e2e/seed-schedule")
async def seed_schedule(payload: dict[str, Any]) -> ApiResponse[Any]:
    """按 E2E 租户写入真实 Schedule 行（含 delivery_route），读路径仍走真实 Worker → PG。"""
    import uuid as uuid_module
    from datetime import UTC, datetime, timedelta

    from muad_agent_worker.application.delivery_routes import upsert_delivery_route
    from muad_agent_worker.infrastructure.db import get_session_factory
    from muad_agent_worker.infrastructure.models.task import TaskSchedule
    from muad_contracts import DeliveryRouteInput

    schedule_id = uuid_module.uuid4()
    now = datetime.now(UTC)
    schedule_type = str(payload.get("schedule_type") or "CRON")
    next_fire_at = payload.get("next_fire_at")
    last_fire_at = payload.get("last_fire_at")
    if "next_fire_at" in payload:
        next_fire_value = (
            datetime.fromisoformat(str(next_fire_at)) if next_fire_at else None
        )
    else:
        next_fire_value = now + timedelta(hours=1)
    session_factory = get_session_factory()
    async with session_factory() as session:
        async with session.begin():
            route_id = await upsert_delivery_route(
                session,
                tenant_id=_settings.default_tenant_id,
                platform_user_id=uuid_module.uuid4(),
                route=DeliveryRouteInput(
                    channel="WECOM", bot_id="e2e-list-bot", external_user_id="e2e-list-user"
                ),
            )
            session.add(
                TaskSchedule(
                    id=schedule_id,
                    tenant_id=_settings.default_tenant_id,
                    name=str(payload.get("name") or f"e2e-schedule-{schedule_id.hex[:8]}"),
                    agent_id=uuid_module.UUID(str(payload.get("agent_id") or uuid_module.uuid4())),
                    actor_user_id=uuid_module.uuid4(),
                    intent_key=str(payload.get("intent_key") or "e2e_policy_check"),
                    skill_id=uuid_module.uuid4(),
                    input_template_json={},
                    schedule_type=schedule_type,
                    cron_expr=str(payload.get("cron_expr") or "0 9 * * *")
                    if schedule_type == "CRON"
                    else None,
                    timezone="Asia/Shanghai",
                    run_at=(
                        datetime.fromisoformat(str(payload["run_at"]))
                        if payload.get("run_at")
                        else None
                    ),
                    delivery_route_id=route_id,
                    status=str(payload.get("status") or "ACTIVE"),
                    next_fire_at=next_fire_value,
                    last_fire_at=(
                        datetime.fromisoformat(str(last_fire_at)) if last_fire_at else None
                    ),
                    revision=1,
                )
            )
    return ok(catalog, {"schedule_id": str(schedule_id)})


@app.post("/__e2e/cleanup")
async def cleanup_tenant() -> ApiResponse[Any]:
    from muad_agent_worker.infrastructure.db import get_session_factory
    from sqlalchemy import text as sql_text

    session_factory = get_session_factory()
    statements = (
        "DELETE FROM task.task_event WHERE tenant_id = :t",
        "DELETE FROM task.task_submission WHERE tenant_id = :t",
        "DELETE FROM task.task_execution WHERE tenant_id = :t",
        "DELETE FROM task.task_schedule WHERE tenant_id = :t",
        "DELETE FROM task.delivery_route WHERE tenant_id = :t",
    )
    for statement in statements:
        async with session_factory() as session:
            await session.execute(sql_text(statement), {"t": _settings.default_tenant_id})
            await session.commit()
    return ok(catalog, {"cleaned": True})


if (DIST / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")


@app.get("/{full_path:path}")
async def spa(full_path: str) -> FileResponse:
    if full_path.startswith("api/"):
        raise AppError(ErrorCode.COMMON_NOT_FOUND)
    candidate = (DIST / full_path).resolve()
    if full_path and candidate.is_file() and DIST.resolve() in candidate.parents:
        return FileResponse(candidate)
    return FileResponse(DIST / "index.html")
