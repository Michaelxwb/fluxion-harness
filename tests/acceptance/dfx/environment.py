"""TASK-003（14-dfx-acceptance）可靠性验收基座：真实 PostgreSQL + Redis + Console/Worker 子进程。

复用 08/09 既有验收栈原语——`tests/acceptance/task_schedule/environment.py` 的
`ServiceProcess`（进程管理）/`free_port`/`require`/`run_db`/`run_async`/`build_skill_zip`
与租户清理语句（`TASK_CLEANUP`/`CONTROL_CLEANUP`），**不另造第二套进程管理**；本模块只补
DFX 自己的唯一租户、控制面种子与最小真实服务链（Console + Worker）。

真实边界：迁移到 head 的真实 PostgreSQL + 真实 Redis（既有栈是外部依赖，本层不替身化）
+ 真实 uvicorn 子进程；租户唯一（可识别前缀 `dfx-reliability-`）、产物根钉在 `tmp_path_factory`
给出的系统临时目录；收尾把本租户残留清成 0 行并删除本租户的 Redis hint（含失败路径）。

任务创建路径：本仓 Console **没有** Task 创建路由（`api/tasks.py` 只有 list/get/cancel），
真实创建边界是 Worker 的 `POST /internal/tasks`（Runtime 经 `WorkerTaskClient` 调用），
快照由 Console `POST /internal/runtime/resolve-definition` 的冻结定义 +
`muad_contracts.build_task_snapshot` 构建——与 Runtime 立即提交同一口径，无 mock。
"""

from __future__ import annotations

import os
import socket
import subprocess
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import httpx
from muad_contracts import (
    ResolveDefinitionResponse,
    TaskType,
    build_task_snapshot,
    snapshot_hash,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.acceptance.task_schedule.environment import (
    CONTROL_CLEANUP,
    TASK_CLEANUP,
    ServiceProcess,
    build_skill_zip,
    free_port,
    require,
    run_async,
    run_db,
)

TENANT_PREFIX = "dfx-reliability"
# 每次运行唯一租户：可识别前缀 + 随机后缀；跨租户对照样本同样唯一，不与历史残留相撞。
TENANT = f"{TENANT_PREFIX}-{uuid.uuid4().hex[:12]}"
CROSS_TENANT = f"{TENANT_PREFIX}-cross-{uuid.uuid4().hex[:12]}"
INTERNAL_TOKEN = "dfx-reliability-internal-token"
MODEL_API_KEY = "dfx-reliability-model-key"
SKILL_KEY = "dfx_reliability_probe"
SKILL_VERSION = "1.0.0"

# 子进程租约口径：心跳 1s / 租约 30s（生产默认 `task_lease_sec=60` / `task_heartbeat_sec=20` 的同向缩放）。
# 租约必须显著大于心跳间隔：整跑 `tests/acceptance` 时同机有大量并发服务进程，心跳协程的 1s
# sleep 与一次续租的 PG/Redis 往返都可能被延误。若只留 1s 余量（心跳 1s / 租约 2s），一次延误
# 就让租约按设计合法过期并被 `WorkerLoop._requeue_expired` 回收 —— 那是产品在正确工作，但用例会
# 看到 `attempt` 递增而误判成 heartbeat 失效。30x 余量让「执行中不失约」成为可稳定观测的不变式；
# 心跳真停摆时租约仍会在 30s 内失效并被回收（PG 落 RECLAIMED 事件），用例仍抓得住。
TASK_LEASE_SEC = 30
TASK_HEARTBEAT_SEC = 1
TASK_CANCEL_CHECK_SEC = 1
WORKER_POLL_INTERVAL_SEC = 1
READY_TIMEOUT_SEC = 45.0
# 服务子进程启动：负载下首次绑定/启动可能瞬时失败——重试，而不是让用例失败。
START_ATTEMPTS = 3
START_RETRY_BACKOFF_SEC = 2.0
# `/readyz`（依赖就绪，而非仅进程存活）的等待窗口；进程活着 ≠ 依赖已连上。
READYZ_TIMEOUT_SEC = 60.0
POLL_INTERVAL_SEC = 0.2

# 可注入时长的真实 Skill：`input.sleep_sec` 决定脚本执行多长，用于制造连续心跳窗口。
SKILL_SCRIPT = (
    "import json, sys, time\n"
    "payload = json.loads(sys.stdin.read() or '{}')\n"
    "time.sleep(float(payload.get('sleep_sec', 0)))\n"
    "print(json.dumps({'slept_sec': payload.get('sleep_sec', 0),"
    " 'probe': payload.get('probe')}))\n"
)

# 收尾核对：本租户在这些表里的行必须全部为 0（顺序与清理语句一致，含子查询归属）。
PURGE_COUNTS: tuple[tuple[str, str], ...] = (
    ("task.task_execution", "tenant_id = :t"),
    ("task.task_event", "tenant_id = :t"),
    ("task.task_submission", "tenant_id = :t"),
    ("task.task_schedule", "tenant_id = :t"),
    ("task.delivery_route", "tenant_id = :t"),
    ("control.skill_artifact", "skill_id IN (SELECT id FROM control.skill WHERE tenant_id = :t)"),
    ("control.skill", "tenant_id = :t"),
    ("control.agent_skill_binding",
     "agent_id IN (SELECT id FROM control.agent_definition WHERE tenant_id = :t)"),
    ("control.agent_mcp_binding",
     "agent_id IN (SELECT id FROM control.agent_definition WHERE tenant_id = :t)"),
    ("control.agent_access_grant",
     "agent_id IN (SELECT id FROM control.agent_definition WHERE tenant_id = :t)"),
    ("control.platform_user", "tenant_id = :t"),
    ("control.agent_definition", "tenant_id = :t"),
    ("control.model_definition", "tenant_id = :t"),
    ("control.config_audit_log", "tenant_id = :t"),
)


@dataclass(frozen=True)
class TaskSpec:
    """Runtime 立即提交口径的冻结快照（由 Console resolve + 契约构建器产出）。"""

    snapshot: dict[str, Any]
    skill_id: uuid.UUID
    artifact_id: uuid.UUID


@dataclass
class DfxStack:
    console_url: str
    worker_url: str
    artifact_root: Path
    skill_cache_root: Path
    tenant_id: str
    agent_id: uuid.UUID
    platform_user_id: uuid.UUID
    skill_id: uuid.UUID
    skill_artifact_id: uuid.UUID
    storage_key: str
    checksum: str
    # 本运行提交过的 Task id：收尾据此清理 Redis cancel hint（不留残留 key）。
    task_ids: list[uuid.UUID] = field(default_factory=list)
    processes: dict[str, ServiceProcess] = field(default_factory=dict)

    def service_headers(self, tenant_id: str | None = None) -> dict[str, str]:
        return {
            "X-Tenant-Id": tenant_id or self.tenant_id,
            "X-Internal-Service": INTERNAL_TOKEN,
        }


def _service_env(base: dict[str, str], **overrides: str) -> dict[str, str]:
    """子进程环境：DFX 自己的租户与内部身份（不用 08/09 栈的哪个固定租户）。"""
    env = {**base, **overrides}
    env["DEFAULT_TENANT_ID"] = TENANT
    env["INTERNAL_SERVICE_TOKEN"] = INTERNAL_TOKEN
    return env


def distinct_free_ports(count: int) -> list[int]:
    """取 `count` 个互不相同的空闲端口：避免两个服务抢到同一个 ephemeral 端口。"""
    ports: list[int] = []
    while len(ports) < count:
        port = free_port()
        if port not in ports:
            ports.append(port)
    return ports


def start_service(process: ServiceProcess, *, attempts: int = START_ATTEMPTS) -> ServiceProcess:
    """启动真实服务子进程；负载下首次启动/绑定失败时重试，不把瞬时失败当用例失败。"""
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            process.start()
            return process
        except RuntimeError as exc:
            last = exc
            process.stop()  # 早退与就绪超时都可能留下半启动进程：重试前先收干净
            if attempt < attempts:
                time.sleep(START_RETRY_BACKOFF_SEC * attempt)
    raise AssertionError(f"{process.name} 启动失败（重试 {attempts} 次）：{last}") from last


def await_readyz(url: str, *, timeout_sec: float = READYZ_TIMEOUT_SEC) -> dict[str, Any]:
    """等真实服务的 `/readyz` 到 200（依赖就绪而非仅进程存活）；有界，超时给出最后盘面。"""
    deadline = time.monotonic() + timeout_sec
    status = 0
    detail: dict[str, Any] = {}
    while time.monotonic() < deadline:
        try:
            response = httpx.get(f"{url}/readyz", timeout=5.0)
            status = response.status_code
            body = response.json()
            detail = cast(dict[str, Any], body.get("data") or {})
        except httpx.HTTPError:
            status = 0
        if status == 200:
            return detail
        time.sleep(POLL_INTERVAL_SEC)
    raise AssertionError(f"{url}/readyz 未在 {timeout_sec}s 内就绪：{status} {detail}")


def instance_id(process: ServiceProcess) -> str:
    """服务子进程的 lease 实例标识（与 `worker/service.py::default_instance_id` 同口径）。

    用于断言「租约属于本用例驱动的那一个执行者」，而不是「某个本机进程」——claim 是全局的
    （`claimer.py::_claimable_conditions` 没有 tenant 谓词），别的 Worker 也领得到本租户的行。
    """
    handle = process._process
    assert handle is not None and handle.pid is not None, f"{process.name} 子进程未启动"
    return f"{socket.gethostname()}:{handle.pid}"


def worker_instance_id(stack: DfxStack) -> str:
    """当前基座 Worker 子进程的 lease 实例标识（见 `instance_id`）。"""
    return instance_id(stack.processes["worker"])


def seed_control(database_url: str, artifact_root: Path) -> dict[str, Any]:
    """本租户控制面真实行 + 真实 Skill 包（Artifact Store 根下一个真 zip）。"""
    from muad_console_platform.infrastructure.models.control import (
        AgentAccessGrant,
        AgentDefinition,
        AgentSkillBinding,
        ModelDefinition,
        PlatformUser,
        Skill,
        SkillArtifact,
    )

    async def seed() -> dict[str, Any]:
        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            model = ModelDefinition(
                tenant_id=TENANT,
                key=f"dfx-model-{uuid.uuid4().hex[:8]}",
                name="DFX Reliability Model",
                model_id="gpt-4o-mini",
                base_url="http://127.0.0.1:9/v1",
                api_key=MODEL_API_KEY,
                params_json={"temperature": 0.0},
            )
            session.add(model)
            await session.flush()
            agent = AgentDefinition(
                tenant_id=TENANT,
                key=f"dfx-agent-{uuid.uuid4().hex[:8]}",
                name="DFX Reliability Agent",
                instructions="You are a dfx reliability agent.",
                model_id=model.id,
                runtime_config={},
            )
            session.add(agent)
            await session.flush()
            user = PlatformUser(
                tenant_id=TENANT,
                user_code=f"dfx-user-{uuid.uuid4().hex[:8]}",
                display_name="DFX Reliability User",
            )
            session.add(user)
            await session.flush()
            session.add(AgentAccessGrant(user_id=user.id, agent_id=agent.id, granted_by=user.id))
            skill = Skill(
                tenant_id=TENANT,
                key=SKILL_KEY,
                name="DFX Reliability Probe",
                description="dfx reliability probe skill",
                user_scope="ALL",
                enabled=True,
            )
            session.add(skill)
            await session.flush()
            storage_key = f"skills/{skill.id}/{SKILL_VERSION}/skill.zip"
            checksum = build_skill_zip(artifact_root, storage_key, body=SKILL_SCRIPT)
            artifact = SkillArtifact(
                skill_id=skill.id,
                version=SKILL_VERSION,
                checksum=checksum,
                storage_key=storage_key,
                execution_mode="ASYNC",
                instructions="",
                package_size=(artifact_root / storage_key).stat().st_size,
                validation_status="READY",
                created_by=user.id,
            )
            session.add(artifact)
            await session.flush()
            skill.current_artifact_id = artifact.id
            session.add(AgentSkillBinding(agent_id=agent.id, skill_id=skill.id, sort_order=0))
            await session.commit()
            ids: dict[str, Any] = {
                "agent_id": agent.id,
                "user_id": user.id,
                "skill_id": skill.id,
                "skill_artifact_id": artifact.id,
                "storage_key": storage_key,
                "checksum": checksum,
            }
        await engine.dispose()
        return ids

    return cast(dict[str, Any], run_async(seed))


def resolve_task_spec(stack: DfxStack, http: httpx.Client) -> TaskSpec:
    """Console 冻结定义 → `build_task_snapshot`：与 Runtime 立即提交同一口径（无 mock）。"""
    response = http.post(
        f"{stack.console_url}/internal/runtime/resolve-definition",
        json={
            "agent_id": str(stack.agent_id),
            "actor_user_id": str(stack.platform_user_id),
            "channel": "WECOM",
        },
        headers=stack.service_headers(),
    )
    assert response.status_code == 200, f"resolve-definition 失败：{response.text}"
    resolved = ResolveDefinitionResponse.model_validate(response.json()["data"])
    frozen = [item for item in resolved.skills if item.artifact_id == stack.skill_artifact_id]
    assert frozen, "Console resolve 未返回本租户的冻结 Skill（种子/授权未生效）"
    skill = frozen[0]
    return TaskSpec(
        snapshot=build_task_snapshot(agent=resolved.agent, model=resolved.model, skill=skill),
        skill_id=skill.skill_id,
        artifact_id=skill.artifact_id,
    )


def post_task_request(
    stack: DfxStack,
    http: httpx.Client,
    spec: TaskSpec,
    *,
    input_data: dict[str, Any],
    idempotency_key: str,
    worker_url: str | None = None,
    delivery_mode: str = "NONE",
    delivery_route: dict[str, Any] | None = None,
) -> httpx.Response:
    """真实 HTTP 提交「原样返回响应」：故障路径要据响应本身断言失败（不得改写为成功）。

    `delivery_mode`/`delivery_route` 由调用方显式给出（默认 `NONE` 不投递）；
    `FINAL_ONLY` 按契约必须带路由（`CreateTaskRequest._require_route_for_delivery`）。
    """
    payload: dict[str, Any] = {
        "tenant_id": stack.tenant_id,
        "agent_id": str(stack.agent_id),
        "actor_user_id": str(stack.platform_user_id),
        "intent_key": "dfx_reliability_probe",
        "skill_id": str(spec.skill_id),
        "skill_artifact_id": str(spec.artifact_id),
        "input": input_data,
        "execution_snapshot": spec.snapshot,
        "snapshot_hash": snapshot_hash(spec.snapshot),
        "idempotency_key": idempotency_key,
        "delivery_mode": delivery_mode,
    }
    if delivery_route is not None:
        payload["delivery_route"] = delivery_route
    return http.post(
        f"{worker_url or stack.worker_url}/internal/tasks",
        json=payload,
        headers=stack.service_headers(),
    )


def submit_task(
    stack: DfxStack,
    http: httpx.Client,
    spec: TaskSpec,
    *,
    input_data: dict[str, Any],
    idempotency_key: str,
    worker_url: str | None = None,
    delivery_mode: str = "NONE",
    delivery_route: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """真实 HTTP 提交：Worker `POST /internal/tasks`（Runtime 同一个内部接口）。"""
    response = post_task_request(
        stack,
        http,
        spec,
        input_data=input_data,
        idempotency_key=idempotency_key,
        worker_url=worker_url,
        delivery_mode=delivery_mode,
        delivery_route=delivery_route,
    )
    assert response.status_code == 200, f"提交 Task 失败：{response.status_code} {response.text}"
    data = cast(dict[str, Any], response.json()["data"])
    stack.task_ids.append(uuid.UUID(data["task_id"]))
    return data


def read_task_row(task_id: uuid.UUID, *, tenant_id: str | None = None) -> dict[str, Any] | None:
    """从真实 PostgreSQL 逐行回读 claim/租约/投递盘面（不以日志或返回值代替）。"""

    async def query(factory: Any) -> dict[str, Any] | None:
        async with factory() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT tenant_id, status, task_type, attempt, max_attempts, lease_owner,"
                        " lease_until, heartbeat_at, cancel_requested, result_json,"
                        " error_code, error_message, finished_at, delivery_mode, delivery_status,"
                        " delivery_attempts, delivered_at, delivery_key"
                        " FROM task.task_execution WHERE tenant_id = :t AND id = :id"
                    ),
                    {"t": tenant_id or TENANT, "id": task_id},
                )
            ).one_or_none()
        if row is None:
            return None
        return {
            "tenant_id": row[0],
            "status": row[1],
            "task_type": row[2],
            "attempt": row[3],
            "max_attempts": row[4],
            "lease_owner": row[5],
            "lease_until": row[6],
            "heartbeat_at": row[7],
            "cancel_requested": row[8],
            "result_json": row[9],
            "error_code": row[10],
            "error_message": row[11],
            "finished_at": row[12],
            "delivery_mode": row[13],
            "delivery_status": row[14],
            "delivery_attempts": row[15],
            "delivered_at": row[16],
            "delivery_key": row[17],
        }

    return cast(dict[str, Any] | None, run_db(query))


def count_task_events(task_id: uuid.UUID, event_type: str) -> int:
    """事件计数同样取自真实库（用于判定「落败者没有执行」）。"""

    async def query(factory: Any) -> int:
        async with factory() as session:
            total = await session.scalar(
                text(
                    "SELECT count(*) FROM task.task_event"
                    " WHERE tenant_id = :t AND task_id = :id AND event_type = :e"
                ),
                {"t": TENANT, "id": task_id, "e": event_type},
            )
        return int(total or 0)

    return cast(int, run_db(query))


def count_rows(
    table: str, tenant_id: str, where: str = "true", params: dict[str, Any] | None = None
) -> int:
    """按给定谓词统计真实行数（跨租户可见性对照用；谓词由调用方显式给出）。"""

    async def query(factory: Any) -> int:
        async with factory() as session:
            total = await session.scalar(
                text(f"SELECT count(*) FROM {table} WHERE tenant_id = :t AND {where}"),
                {"t": tenant_id, **(params or {})},
            )
        return int(total or 0)

    return cast(int, run_db(query))


def list_task_types(tenant_id: str | None = None) -> list[str]:
    """PG 里实际出现过的 task_type 取值（规则口径的机检素材；默认取全表）。"""

    async def query(factory: Any) -> list[str]:
        statement = "SELECT DISTINCT task_type FROM task.task_execution"
        params: dict[str, Any] = {}
        if tenant_id is not None:
            statement += " WHERE tenant_id = :t"
            params["t"] = tenant_id
        async with factory() as session:
            rows = (await session.execute(text(statement), params)).all()
        return [str(row[0]) for row in rows]

    return cast(list[str], run_db(query))


def task_type_values() -> set[str]:
    """TaskType 的封闭取值集合（契约口径：仅 SKILL/BATCH）。"""
    return {str(member) for member in TaskType}


def purge_residue(database_url: str) -> dict[str, int]:
    """删除本租户残留并回读计数（返回逐表剩余行数，供断言与证据引用）。"""

    async def purge() -> dict[str, int]:
        engine = create_async_engine(database_url)
        async with engine.begin() as connection:
            for statement in (*TASK_CLEANUP, *CONTROL_CLEANUP):
                await connection.execute(text(statement), {"t": TENANT})
        async with engine.connect() as connection:
            remaining = {
                table: int(
                    (
                        await connection.execute(
                            text(f"SELECT count(*) FROM {table} WHERE {where}"), {"t": TENANT}
                        )
                    ).scalar_one()
                )
                for table, where in PURGE_COUNTS
            }
        await engine.dispose()
        return remaining

    return cast(dict[str, int], run_async(purge))


def purge_redis_hints(redis_url: str, task_ids: list[uuid.UUID]) -> int:
    """删除本运行写入的 `task:cancel:{id}` hint，返回删除数（不留残留 key）。"""
    import redis.asyncio as redis

    async def purge() -> int:
        client = redis.from_url(redis_url, decode_responses=True)
        try:
            keys = [f"task:cancel:{task_id}" for task_id in task_ids]
            return int(await client.delete(*keys)) if keys else 0
        finally:
            await client.aclose()

    return cast(int, run_async(purge))


def cleanup(database_url: str, redis_url: str, stack: DfxStack) -> dict[str, int]:
    """收尾清理（失败路径同样走这里）：本租户 0 行 + 无残留 Redis hint + 产物目录清空。"""
    remaining = purge_residue(database_url)
    purge_redis_hints(redis_url, stack.task_ids)
    if stack.artifact_root.exists():
        for path in sorted(stack.artifact_root.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink(missing_ok=True)
            elif path.is_dir():
                path.rmdir()
    leaked = {table: total for table, total in remaining.items() if total}
    assert not leaked, f"收尾后本租户仍有残留：{leaked}"
    return remaining


def clear_engine_caches() -> None:
    """服务子进程已退出：清理本进程可能缓存过的 engine/session factory。"""
    from muad_agent_runtime.infrastructure import db as runtime_db
    from muad_agent_worker.infrastructure import db as worker_db
    from muad_console_platform.infrastructure import db as console_db

    for module in (runtime_db, worker_db, console_db):
        module.get_engine.cache_clear()
        module.get_session_factory.cache_clear()


def start_dfx_stack(root: Path) -> tuple[DfxStack, list[ServiceProcess]]:
    """起真实服务链：Console（冻结定义）+ Worker（claim/执行）；Redis/PG 为真实外部依赖。"""
    from muad_common import SharedSettings

    settings = SharedSettings()
    database_url = require("DATABASE_URL", settings.database_url)
    redis_url = require("REDIS_URL", settings.redis_url)

    logs = root / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    artifact_root = root / "artifacts"
    artifact_root.mkdir(parents=True, exist_ok=True)
    skill_cache_root = root / "skill-cache"
    skill_cache_root.mkdir(parents=True, exist_ok=True)

    base_env = {
        **os.environ,
        "DATABASE_URL": database_url,
        "REDIS_URL": redis_url,
        "ARTIFACT_ROOT": str(artifact_root),
        "SKILL_CACHE_ROOT": str(skill_cache_root),
        "TASK_LEASE_SEC": str(TASK_LEASE_SEC),
        "TASK_HEARTBEAT_SEC": str(TASK_HEARTBEAT_SEC),
        "TASK_CANCEL_CHECK_SEC": str(TASK_CANCEL_CHECK_SEC),
        "WORKER_POLL_INTERVAL_SEC": str(WORKER_POLL_INTERVAL_SEC),
    }

    console_port, worker_port = distinct_free_ports(2)
    console_url = f"http://127.0.0.1:{console_port}"
    worker_url = f"http://127.0.0.1:{worker_port}"

    processes: list[ServiceProcess] = []

    def spawn(name: str, module: str, port: int, **env: str) -> ServiceProcess:
        process = ServiceProcess(
            name=name,
            module=module,
            port=port,
            env=_service_env(base_env, **env),
            log_path=logs / f"{name}.log",
        )
        processes.append(process)
        return process

    console = spawn(
        "console", "muad_console_platform.main", console_port, AGENT_WORKER_URL=worker_url
    )
    worker = spawn(
        "worker", "muad_agent_worker.main", worker_port, CONSOLE_PLATFORM_URL=console_url
    )
    ids = seed_control(database_url, artifact_root)
    start_service(console)
    start_service(worker)
    # 依赖就绪再把盘面交回用例：控制台（PG）与 Worker（PG + Artifact 根）都过 `/readyz` 才继续，
    # 否则用例的第一次真实请求会落在「进程活着但依赖还没连上」的窗口里（负载下该窗口会变长）。
    await_readyz(console_url)
    await_readyz(worker_url)

    stack = DfxStack(
        processes={process.name: process for process in processes},
        console_url=console_url,
        worker_url=worker_url,
        artifact_root=artifact_root,
        skill_cache_root=skill_cache_root,
        tenant_id=TENANT,
        agent_id=ids["agent_id"],
        platform_user_id=ids["user_id"],
        skill_id=ids["skill_id"],
        skill_artifact_id=ids["skill_artifact_id"],
        storage_key=ids["storage_key"],
        checksum=ids["checksum"],
    )
    return stack, processes


def stop_dfx_stack(processes: list[ServiceProcess]) -> None:
    for process in reversed(processes):
        process.stop()


# ---------------------------------------------------------------------------
# 服务监督（用例内换入/新起的真实服务进程，模块收尾必须全部停掉）
# ---------------------------------------------------------------------------

# 用例内换入/新起的服务进程：模块收尾必须全部停掉，否则留下孤儿进程共用同一测试库。
_SPAWNED_SERVICES: list[ServiceProcess] = []
# 基座端口上「此刻真正活着」的 Worker：基座对象，或用例内换入/还原的那个。
# 只停基座对象不够：上一个用例还原的 Worker 仍占着同一端口，新进程会绑定失败而 `/healthz`
# 由旧进程应答，于是用例以为换入成功、实际跑的是没有注入配置的那个（E-05 曾因此误判）。
_LIVE_WORKER: list[ServiceProcess] = []
PROCESS_WAIT_SEC = 30.0


def stop_spawned_services() -> None:
    """停掉用例内换入/新起的全部服务进程（模块收尾用；失败路径同样调用）。"""
    for process in _SPAWNED_SERVICES:
        process.stop()
    _SPAWNED_SERVICES.clear()
    _LIVE_WORKER.clear()


def service_commands() -> list[str]:
    """当前在跑的**真实服务进程**命令行（孤儿进程判据）。

    判据是「可执行文件是 Python 且命令行为 `-m uvicorn muad_*`」：真实服务的启动形式就是
    `python -m uvicorn muad_*.main:app`。不能只按「命令行里出现 uvicorn/muad_」过滤——
    调用方 shell 自己的命令行只要提到这两个子串（例如 `grep -E "uvicorn|muad_"`、`grep -c
    "-m uvicorn muad_"`），那个 shell 就会被算成多余服务进程，把「无多余进程」的断言变成
    假失败（2026-09-29 整跑 `tests/acceptance` 时实测到 5 例；改用 `ucomm` 判定后消除）。
    """
    completed = subprocess.run(
        ["ps", "-Aww", "-o", "ucomm=,command="], capture_output=True, text=True, check=True
    )
    services: list[str] = []
    for line in completed.stdout.splitlines():
        # `ucomm` 是 argv[0] 的名字（`python3.13`）；`comm` 在 macOS 上被截断成 16 字符，
        # 形如 `/Users/jahan/wor`，用它做判据会把真实服务漏掉。
        ucomm, _, command = line.strip().partition(" ")
        if "python" not in ucomm.lower():
            continue
        if "-m uvicorn muad_" in command:
            services.append(command.strip())
    return services


def await_service_processes(expected: dict[str, int]) -> list[str]:
    """等进程收敛到期望分布（停进程后进程表有极短抖动窗口）。"""
    deadline = time.monotonic() + PROCESS_WAIT_SEC
    commands: list[str] = []
    while time.monotonic() < deadline:
        commands = service_commands()
        if {module: sum(module in line for line in commands) for module in expected} == expected:
            return commands
        time.sleep(POLL_INTERVAL_SEC)
    raise AssertionError(f"服务进程未收敛到 {expected}：{commands}")


def await_no_extra_services() -> None:
    """本用例起的临时服务（Gateway/探针/换入 Worker）必须全部停掉，只留基座一对。"""
    commands = await_service_processes(
        {"muad_console_platform.main": 1, "muad_agent_worker.main": 1}
    )
    assert len(commands) == 2, f"本用例仍有多余服务进程：{commands}"


def await_port_free(port: int, *, timeout_sec: float = 15.0) -> None:
    """换入前确认端口真的空出来：否则新进程绑定失败、`/healthz` 由旧进程应答（静默换不成功）。"""
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        with socket.socket() as probe:
            probe.settimeout(1.0)
            if probe.connect_ex(("127.0.0.1", port)) != 0:
                return
        time.sleep(POLL_INTERVAL_SEC)
    raise AssertionError(f"端口 {port} 在 {timeout_sec}s 内仍被占用：换入的 Worker 不会真的接管")


def stop_live_worker(stack: DfxStack) -> None:
    """停掉基座端口上此刻真正活着的 Worker（基座对象或用例内还原的那个）。"""
    for process in [*_LIVE_WORKER, stack.processes["worker"]]:
        process.stop()
    _LIVE_WORKER.clear()


@contextmanager
def swapped_workers(stack: DfxStack, log_dir: Path) -> Iterator[Callable[..., ServiceProcess]]:
    """停掉基座端口上的 Worker，用**同一端口 + 同一基座环境**起替代 Worker（可叠加显式配置）。

    claim/reap/投递都是全局的：只要还有一个存活 Worker，「谁领走这一行」就不可控。用例内只让
    自己的 Worker 参与，收尾按基座环境把 Worker 起回来（同一端口 + 同一 env，与 TASK-004 的
    `_degraded_worker` 同口径，不另造第二套进程管理）。
    """
    base = stack.processes["worker"]
    stop_live_worker(stack)
    started: list[ServiceProcess] = []

    def start(name: str, **overrides: str) -> ServiceProcess:
        process = ServiceProcess(
            name=name,
            module="muad_agent_worker.main",
            port=base.port,
            env={**base.env, **overrides},
            log_path=log_dir / f"{name}.log",
        )
        await_port_free(base.port)
        start_service(process)
        started.append(process)
        return process

    try:
        yield start
    finally:
        for process in started:
            process.stop()
        _LIVE_WORKER.clear()
        restored = ServiceProcess(
            name="worker",
            module="muad_agent_worker.main",
            port=base.port,
            env=base.env,
            log_path=log_dir / "worker-restored.log",
        )
        await_port_free(base.port)
        start_service(restored)
        _SPAWNED_SERVICES.append(restored)
        _LIVE_WORKER.append(restored)


# ---------------------------------------------------------------------------
# 投递链（真实 IM Gateway + 真实渠道探针，可选响应改写代理）
# ---------------------------------------------------------------------------


@contextmanager
def delivery_pair(stack: DfxStack, log_dir: Path) -> Iterator[tuple[str, str]]:
    """真实 IM Gateway + 真实渠道探针（外部渠道由本地真实 HTTP 探针承载，不是替身）。

    退出时两个进程都停掉：它们是用例自起的临时服务，收尾不得留下孤儿。
    """
    from muad_common import SharedSettings

    probe_port, gateway_port = distinct_free_ports(2)
    probe_url = f"http://127.0.0.1:{probe_port}"
    gateway_url = f"http://127.0.0.1:{gateway_port}"
    settings = SharedSettings()
    probe = ServiceProcess(
        name="dfx-channel-probe",
        module="tests.acceptance.task_schedule.channel_probe",
        port=probe_port,
        env={**os.environ},
        log_path=log_dir / "channel-probe.log",
    )
    gateway = ServiceProcess(
        name="dfx-im-gateway",
        module="muad_im_gateway.main",
        port=gateway_port,
        env={
            **os.environ,
            "DATABASE_URL": settings.require_database_url(),
            "REDIS_URL": settings.require_redis_url(),
            # 启动校验要求 ARTIFACT_ROOT 目录存在（缺则拒启动）。必须显式给本栈的临时产物根：
            # 靠环境默认值 `./.data/artifacts` 会在干净检出/CI 上不存在（本地因历史运行碰巧有），
            # 表现为 `dfx-im-gateway exited early (code=3): artifact storage is not mounted`。
            "ARTIFACT_ROOT": str(stack.artifact_root),
            "SKILL_CACHE_ROOT": str(stack.skill_cache_root),
            "LOG_DIR": str(log_dir),
            "CONSOLE_PLATFORM_URL": stack.console_url,
            "CHANNEL_PROBE_URL": f"{probe_url}/probe/deliveries",
            "DEFAULT_TENANT_ID": stack.tenant_id,
            "INTERNAL_SERVICE_TOKEN": stack.service_headers()["X-Internal-Service"],
        },
        log_path=log_dir / "im-gateway.log",
    )
    start_service(probe)
    start_service(gateway)
    try:
        await_readyz(gateway_url, timeout_sec=READY_TIMEOUT_SEC)
        yield gateway_url, probe_url
    finally:
        gateway.stop()
        probe.stop()


@contextmanager
def delivery_rewriter(
    upstream_gateway_url: str,
    log_dir: Path,
    *,
    delay_sec: float = 0.0,
    force_not_delivered: bool = False,
) -> Iterator[str]:
    """Worker ↔ 真实 Gateway 之间的响应改写代理（故障注入，见 `delivery_rewriter.py`）。

    请求仍打到真实 Gateway（真实 Redis 去重 + 真实渠道探针收件），只在回程延迟/改写响应。
    """
    port = distinct_free_ports(1)[0]
    url = f"http://127.0.0.1:{port}"
    process = ServiceProcess(
        name="dfx-delivery-rewriter",
        module="tests.acceptance.dfx.delivery_rewriter",
        port=port,
        env={
            **os.environ,
            "REWRITE_UPSTREAM_GATEWAY_URL": upstream_gateway_url,
            "REWRITE_DELAY_SEC": str(delay_sec),
            "REWRITE_FORCE_NOT_DELIVERED": "1" if force_not_delivered else "0",
        },
        log_path=log_dir / "delivery-rewriter.log",
    )
    start_service(process)
    try:
        yield url
    finally:
        process.stop()


def probe_deliveries(probe_url: str, bot_id: str) -> list[dict[str, Any]]:
    """渠道探针真实收到的投递记录（按本用例的 bot_id 归因）。"""
    response = httpx.get(f"{probe_url}/probe/deliveries", timeout=5.0)
    assert response.status_code == 200, response.text
    records = cast(list[dict[str, Any]], response.json()["deliveries"])
    return [record for record in records if record.get("bot_id") == bot_id]


__all__ = [
    "CROSS_TENANT",
    "DfxStack",
    "PROCESS_WAIT_SEC",
    "TENANT",
    "TENANT_PREFIX",
    "TaskSpec",
    "await_no_extra_services",
    "await_port_free",
    "await_readyz",
    "await_service_processes",
    "cleanup",
    "clear_engine_caches",
    "count_rows",
    "count_task_events",
    "delivery_pair",
    "delivery_rewriter",
    "distinct_free_ports",
    "instance_id",
    "list_task_types",
    "probe_deliveries",
    "purge_redis_hints",
    "post_task_request",
    "read_task_row",
    "resolve_task_spec",
    "service_commands",
    "start_dfx_stack",
    "start_service",
    "stop_dfx_stack",
    "stop_live_worker",
    "stop_spawned_services",
    "submit_task",
    "swapped_workers",
    "task_type_values",
    "worker_instance_id",
]
