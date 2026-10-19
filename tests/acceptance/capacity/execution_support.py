"""TASK-110 容量执行验收共用件：加速节拍栈、真实 control 种子与 PG/HTTP 观测。

真实边界（与 TASK-107 的独立栈同源，不 mock 任何服务）：
- **数据存储**：种子写进本栈自己的空库（`muad_acc_*` + alembic head），断言用真实 PG 回读；
- **模型侧**：凭据 `base_url` 指向栈内 TASK-106 的真实 HTTP 探针子进程（Key 计数/限流/延迟）；
- **四单元**：Runtime/Worker 都是真实 uvicorn 子进程，模型调用/任务领取都发生在它们里面；
- **节拍加速**：验收断言「最终发生」而不是节拍的绝对长度（同 task_schedule 口径），
  故把租约/轮询压小到仍满足 `lease >= 3 * heartbeat` 的启动校验；不改变被测语义。

本模块只做「建现场 + 读事实」，行为编排与断言留在用例里。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import asyncpg
import httpx
from muad_contracts.model_pool import derive_model_key_resource_id

__all__ = [
    "EXECUTION_CADENCE_ENV",
    "ChatUserSeed",
    "CredentialSpec",
    "ModelPoolSeed",
    "ProbeCapacity",
    "RunPost",
    "TenantSeed",
    "build_run_payload",
    "build_task_body",
    "cleanup_tenant",
    "console_headers",
    "fetch_all",
    "fetch_one",
    "http_client",
    "login_console_admin",
    "post_run",
    "probe_calls",
    "probe_capacity_reserve",
    "probe_capacity_settle",
    "probe_control",
    "probe_state",
    "probe_target_payload",
    "run_async",
    "run_row",
    "runtime_headers",
    "seed_model_pool",
    "seed_chat_users",
    "seed_tenant",
    "service_headers",
    "submit_task",
    "task_events",
    "task_row",
    "wait_run_terminal",
    "wait_until",
    "write_skill_package",
]

#: 执行节拍（栈级 env）：租约/轮询压小，仍满足 `lease >= 3 * heartbeat` 的启动校验。
EXECUTION_CADENCE_ENV: dict[str, dict[str, str]] = {
    "agent-runtime": {
        "RUN_LEASE_SEC": "5",
        "RUN_HEARTBEAT_SEC": "1",
        "RUN_REAPER_INTERVAL_SEC": "2",
        "MODEL_CALL_LEASE_SEC": "4",
        "MODEL_CALL_HEARTBEAT_SEC": "1",
        "RUN_EVENT_HEARTBEAT_SEC": "1",
        "RUN_EVENT_FALLBACK_POLL_SEC": "1",
        "STREAM_DELTA_FLUSH_INTERVAL_MS": "10",
        "CHANNEL_DELIVERY_POLL_SEC": "1",
        "CHANNEL_DELIVERY_RETRY_BASE_SEC": "1",
    },
    "agent-worker": {
        "WORKER_POLL_INTERVAL_SEC": "1",
        "TASK_HEARTBEAT_SEC": "1",
        "TASK_LEASE_SEC": "6",
        "TASK_CANCEL_CHECK_SEC": "1",
        "TASK_DEADLINE_SWEEP_INTERVAL_SEC": "2",
        "RUN_REAPER_INTERVAL_SEC": "1",
        "DELIVERY_POLL_INTERVAL_SEC": "1",
        "SCHEDULER_POLL_INTERVAL_SEC": "2",
    },
}

#: 平台设置（租户级，受理时冻结一次）：稳定 epoch + 短模型等待预算（验收不真等 30 秒）。
PLATFORM_SETTINGS_OVERRIDES: dict[str, Any] = {
    "model_pool": {"dispatch_epoch_sec": 3600},
    "capacity": {"model_wait_timeout_sec": 2},
}

TERMINAL_RUN_STATUSES = ("COMPLETED", "FAILED", "CANCELLED")


# --------------------------------------------------------------------------------------
# 跨线程 async 桥 + 真实 PG 读事实
# --------------------------------------------------------------------------------------


def run_async(factory: Callable[[], Any]) -> Any:
    """在独立线程/事件循环里跑协程（同 `tests.acceptance.capacity.stack.run_async` 口径）。

    不能用主线程 `asyncio.run`：它会把主线程事件循环置空，污染后续 pytest-asyncio 用例。
    """
    box: list[Any] = []
    failure: list[BaseException] = []

    def target() -> None:
        loop = asyncio.new_event_loop()
        try:
            box.append(loop.run_until_complete(factory()))
        except BaseException as exc:  # noqa: BLE001 - 跨线程原样回抛
            failure.append(exc)
        finally:
            loop.close()

    thread = threading.Thread(target=target, name="capacity-execution-db")
    thread.start()
    thread.join()
    if failure:
        raise failure[0]
    return box[0] if box else None


def _dsn(database_url: str) -> str:
    return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


def fetch_all(database_url: str, sql: str, *params: object) -> list[dict[str, Any]]:
    """参数化只读多行查询（真实 PostgreSQL）；不拼 SQL。"""

    async def query() -> list[dict[str, Any]]:
        connection = await asyncpg.connect(_dsn(database_url), timeout=10)
        try:
            rows = await connection.fetch(sql, *params)
            return [dict(row) for row in rows]
        finally:
            await connection.close()

    return list(run_async(query))


def fetch_one(database_url: str, sql: str, *params: object) -> dict[str, Any] | None:
    rows = fetch_all(database_url, sql, *params)
    return rows[0] if rows else None


def wait_until(
    predicate: Callable[[], Any],
    *,
    timeout_sec: float = 30.0,
    interval_sec: float = 0.2,
    label: str = "condition",
) -> Any:
    """轮询真实事实直到成立；超时显式失败（不回 False 冒充）。"""
    deadline = time.monotonic() + timeout_sec
    last: Any = None
    while time.monotonic() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(interval_sec)
    raise AssertionError(f"{label} did not become true within {timeout_sec:.0f}s (last={last!r})")


def http_client(*, timeout_sec: float = 30.0) -> httpx.Client:
    """真实 HTTP 客户端；`trust_env=False` 避免系统代理改写探针的真实 socket 行为。"""
    return httpx.Client(timeout=timeout_sec, trust_env=False)


def service_headers(tenant_id: str, token: str) -> dict[str, str]:
    return {"X-Tenant-Id": tenant_id, "X-Internal-Service": token}


def runtime_headers(tenant_id: str) -> dict[str, str]:
    """Runtime 公开受理面只按租户门控（执行身份来自 PG claim，不来自 HTTP 头）。"""
    return {"X-Tenant-Id": tenant_id}


def console_headers(tenant_id: str) -> dict[str, str]:
    return {"X-Tenant-Id": tenant_id}


# --------------------------------------------------------------------------------------
# 真实 control 种子（成员/凭据/额度组/Agent/用户/管理员/平台设置）
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CredentialSpec:
    """一名模型成员要用的物理 Key 现场（真实 HTTP 探针上按 Key 计数/限流）。"""

    key: str
    api_key: str
    max_inflight: int = 1
    weight: int = 1
    #: 共享账号额度组名（同一名字 = 同一 `model_quota_group` 行）；None = 该 Key 独占自身额度
    quota_group: str | None = None
    group_max_inflight: int | None = None
    group_rpm: int | None = None
    group_tpm: int | None = None


@dataclass(frozen=True, slots=True)
class ModelPoolSeed:
    """一次模型池种子：逻辑模型 + 成员凭据 + Agent + 用户（真实 control 行）。"""

    model_id: uuid.UUID
    agent_id: uuid.UUID
    credentials: tuple[uuid.UUID, ...]
    deployments: tuple[uuid.UUID, ...]
    key_resource_ids: tuple[uuid.UUID, ...]
    quota_group_id: uuid.UUID | None
    model_name: str

    def credential_of(self, index: int) -> uuid.UUID:
        return self.credentials[index]

    def deployment_of(self, index: int) -> uuid.UUID:
        return self.deployments[index]


@dataclass(frozen=True, slots=True)
class TenantSeed:
    """租户级现场：真实平台用户 + Console 管理员 + 平台设置（受理时冻结一次）。"""

    tenant_id: str
    user_id: uuid.UUID
    admin_username: str
    admin_password: str


def _seed_async(database_url: str, work: Callable[[Any], Any]) -> Any:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    async def runner() -> Any:
        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                result = await work(session)
                await session.commit()
                return result
        finally:
            await engine.dispose()

    return run_async(runner)


def seed_tenant(
    database_url: str,
    *,
    tenant_id: str,
    settings: Mapping[str, Any] | None = None,
) -> TenantSeed:
    """租户级种子：真实平台用户 + ADMIN 账号（Console 探针要真实登录）+ 平台设置一行。"""
    from muad_console_platform.application.auth_service import hash_password
    from muad_console_platform.infrastructure.models.auth import ConsoleAccount
    from muad_console_platform.infrastructure.models.control import PlatformUser

    username = f"capacity-admin-{uuid.uuid4().hex[:8]}"
    password = f"capacity-admin-{uuid.uuid4().hex}"

    async def work(session: Any) -> uuid.UUID:
        user = PlatformUser(
            tenant_id=tenant_id,
            user_code=f"capacity-user-{uuid.uuid4().hex[:8]}",
            display_name="Capacity User",
        )
        session.add(user)
        session.add(
            ConsoleAccount(
                tenant_id=tenant_id,
                username=username,
                display_name="Capacity Admin",
                password_hash=hash_password(password),
                role="ADMIN",
                enabled=True,
            )
        )
        await session.flush()
        return user.id

    user_id = _seed_async(database_url, work)
    from tests.acceptance.datastores import seed_platform_settings

    seed_platform_settings(
        database_url,
        tenant_id,
        dict(settings if settings is not None else PLATFORM_SETTINGS_OVERRIDES),
    )
    return TenantSeed(
        tenant_id=tenant_id,
        user_id=user_id,
        admin_username=username,
        admin_password=password,
    )


def seed_model_pool(
    database_url: str,
    *,
    tenant_id: str,
    user_id: uuid.UUID,
    probe_url: str,
    credentials: Sequence[CredentialSpec],
    agent_runtime_config: Mapping[str, Any] | None = None,
    existing_credentials: Sequence[tuple[uuid.UUID, str]] = (),
) -> ModelPoolSeed:
    """逻辑模型 + 成员凭据（真实探针地址/真实 Key）+ Agent + 授权（真实 control 行）。

    `existing_credentials` 把**已存在的物理凭据**（`(credential_id, api_key)`）也挂成本模型的
    成员：同一物理 Key 因此被多个模型成员引用（S-12 的合并计量现场），而不是复制一行凭据。
    """
    from muad_console_platform.infrastructure.models.control import (
        AgentAccessGrant,
        AgentDefinition,
        ModelCredential,
        ModelDefinition,
        ModelDeployment,
        ModelQuotaGroup,
    )

    model_name = "gpt-4o-mini"
    groups: dict[str, uuid.UUID] = {}

    async def work(session: Any) -> tuple[Any, ...]:
        model = ModelDefinition(
            tenant_id=tenant_id,
            key=f"capacity-model-{uuid.uuid4().hex[:8]}",
            name="Capacity Model",
            model_id=model_name,
            provider_key="openai",
            params_json={"temperature": 0.0, "max_tokens": 64},
        )
        session.add(model)
        await session.flush()
        credential_ids: list[uuid.UUID] = []
        deployment_ids: list[uuid.UUID] = []
        for spec in credentials:
            group_id = None
            if spec.quota_group is not None:
                group_id = groups.get(spec.quota_group)
                if group_id is None:
                    group = ModelQuotaGroup(
                        tenant_id=tenant_id,
                        key=f"capacity-group-{uuid.uuid4().hex[:8]}",
                        max_inflight=spec.group_max_inflight or spec.max_inflight,
                        rpm=spec.group_rpm,
                        tpm=spec.group_tpm,
                    )
                    session.add(group)
                    await session.flush()
                    group_id = group.id
                    groups[spec.quota_group] = group_id
            credential = ModelCredential(
                tenant_id=tenant_id,
                key=spec.key,
                provider_key="openai",
                base_url=f"{probe_url}/v1",
                api_key=spec.api_key,
                api_key_digest=hashlib.sha256(spec.api_key.encode()).hexdigest(),
                max_inflight=spec.max_inflight,
                quota_group_id=group_id,
            )
            session.add(credential)
            await session.flush()
            deployment = ModelDeployment(
                tenant_id=tenant_id,
                key=f"capacity-member-{uuid.uuid4().hex[:8]}",
                model_id=model.id,
                credential_id=credential.id,
                weight=spec.weight,
            )
            session.add(deployment)
            await session.flush()
            credential_ids.append(credential.id)
            deployment_ids.append(deployment.id)
        for credential_id, _ in existing_credentials:
            deployment = ModelDeployment(
                tenant_id=tenant_id,
                key=f"capacity-member-{uuid.uuid4().hex[:8]}",
                model_id=model.id,
                credential_id=credential_id,
                weight=1,
            )
            session.add(deployment)
            await session.flush()
            credential_ids.append(credential_id)
            deployment_ids.append(deployment.id)
        agent = AgentDefinition(
            tenant_id=tenant_id,
            key=f"capacity-agent-{uuid.uuid4().hex[:8]}",
            name="Capacity Agent",
            instructions="You are a capacity acceptance agent.",
            model_id=model.id,
            runtime_config=dict(agent_runtime_config or {"max_model_retries": 0}),
        )
        session.add(agent)
        await session.flush()
        session.add(AgentAccessGrant(user_id=user_id, agent_id=agent.id, granted_by=user_id))
        return model.id, agent.id, tuple(credential_ids), tuple(deployment_ids)

    model_id, agent_id, credential_ids, deployment_ids = _seed_async(database_url, work)
    # 计量身份由 tenant/provider/Key 指纹派生（`derive_model_key_resource_id`）：同一物理 Key
    # 跨模型/成员/进程合并为同一 `key_resource_id`。验收按**同一处派生**复算，不猜实现内部行。
    key_resources = tuple(
        derive_model_key_resource_id(
            tenant_id, "openai", hashlib.sha256(spec.api_key.encode()).hexdigest()
        )
        for spec in credentials
    ) + tuple(
        derive_model_key_resource_id(
            tenant_id, "openai", hashlib.sha256(api_key.encode()).hexdigest()
        )
        for _, api_key in existing_credentials
    )
    return ModelPoolSeed(
        model_id=model_id,
        agent_id=agent_id,
        credentials=tuple(credential_ids),
        deployments=tuple(deployment_ids),
        key_resource_ids=key_resources,
        quota_group_id=next(iter(groups.values()), None),
        model_name=model_name,
    )


@dataclass(frozen=True, slots=True)
class ChatUserSeed:
    """一名真实 CHAT 主体：用户行 + Agent 授权 + 随机 Token（原文只在本次种子内存里）。"""

    index: int
    user_id: uuid.UUID
    token: str


def seed_chat_users(
    database_url: str,
    *,
    tenant_id: str,
    agent_id: uuid.UUID,
    count: int,
    user_prefix: str = "capacity-load-user",
) -> tuple[ChatUserSeed, ...]:
    """批量真实用户现场：每用户一份 `PlatformUser` + `AgentAccessGrant` + `ChatAccessToken`。

    容量负载的形状是「N 个不同用户各一个活跃 Run」（同用户并发多 Run 会撞
    `capacity.max_open_runs_per_user`，那是配置边界而不是用户容量），所以 1000 用户必须有
    1000 份独立主体。Token 只存摘要（与产品同口径），原文只在返回值里，不落日志/审计。
    """
    if count < 1:
        raise ValueError(f"count 必须 ≥ 1，收到 {count}")
    from datetime import UTC, datetime, timedelta

    from muad_console_platform.application.chat_token_service import (
        generate_chat_token,
        hash_chat_token,
    )
    from muad_console_platform.infrastructure.models.auth import ChatAccessToken
    from muad_console_platform.infrastructure.models.control import (
        AgentAccessGrant,
        PlatformUser,
    )

    tokens = tuple(generate_chat_token() for _ in range(count))
    issued_at = datetime.now(UTC)
    expires_at = issued_at + timedelta(days=30)

    async def work(session: Any) -> tuple[uuid.UUID, ...]:
        user_ids: list[uuid.UUID] = []
        for index, token in enumerate(tokens):
            user = PlatformUser(
                tenant_id=tenant_id,
                user_code=f"{user_prefix}-{index}-{uuid.uuid4().hex[:8]}",
                display_name=f"Capacity Load User {index}",
                status="ACTIVE",
            )
            session.add(user)
            await session.flush()
            session.add(AgentAccessGrant(user_id=user.id, agent_id=agent_id, granted_by=user.id))
            session.add(
                ChatAccessToken(
                    tenant_id=tenant_id,
                    platform_user_id=user.id,
                    token_hash=hash_chat_token(token),
                    revision=1,
                    issued_at=issued_at,
                    expires_at=expires_at,
                )
            )
            user_ids.append(user.id)
        return tuple(user_ids)

    user_ids = _seed_async(database_url, work)
    if len(user_ids) != count:
        raise AssertionError(f"种子用户数与请求不一致：{len(user_ids)} != {count}")
    return tuple(
        ChatUserSeed(index=index, user_id=user_id, token=tokens[index])
        for index, user_id in enumerate(user_ids)
    )


def login_console_admin(
    client: httpx.Client, console_url: str, tenant_id: str, admin: Any
) -> str:
    """真实 Console 登录：下发 `muad_session`/`muad_csrf`，返回 CSRF 供非安全方法带头。"""
    response = client.post(
        f"{console_url}/api/v1/auth/login",
        json={"username": admin.admin_username, "password": admin.admin_password},
        headers=console_headers(tenant_id),
    )
    assert response.status_code == 200, response.text
    csrf = client.cookies.get("muad_csrf")
    assert csrf, "登录必须下发 muad_csrf"
    assert client.cookies.get("muad_session"), "登录必须下发 muad_session"
    return str(csrf)


# --------------------------------------------------------------------------------------
# 真实受理（Runtime /v1/runs）与探针控制面
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RunPost:
    """一次真实受理的结果：状态码、run_id（从持久 SSE 帧里读）与原始帧。"""

    status_code: int
    run_id: uuid.UUID | None
    body: str
    events: tuple[dict[str, Any], ...] = field(default_factory=tuple)


def _sse_events(body: str) -> tuple[dict[str, Any], ...]:
    events: list[dict[str, Any]] = []
    for line in body.splitlines():
        if not line.startswith("data:"):
            continue
        try:
            parsed = json.loads(line[5:].strip())
        except ValueError:
            continue
        if isinstance(parsed, dict):
            events.append(parsed)
    return tuple(events)


def build_run_payload(
    *,
    agent_id: uuid.UUID,
    user_id: uuid.UUID,
    text: str,
    conversation_id: uuid.UUID | None = None,
    message_id: str | None = None,
    attachments: Sequence[Mapping[str, Any]] = (),
    bot_id: str = "capacity-webchat",
) -> dict[str, Any]:
    """真实 `POST /v1/runs` 的请求体（受理指纹含 message 的正文与附件）。"""
    payload: dict[str, Any] = {
        "agent_id": str(agent_id),
        "platform_user_id": str(user_id),
        "channel": {
            "type": "WEBCHAT",
            "bot_id": bot_id,
            "external_user_id": str(user_id),
            "external_conversation_id": str(conversation_id) if conversation_id else None,
        },
        "message": {
            "id": message_id or f"capacity-msg-{uuid.uuid4().hex}",
            "type": "text",
            "text": text,
            "attachments": [dict(item) for item in attachments],
        },
    }
    if conversation_id is not None:
        payload["conversation_id"] = str(conversation_id)
    return payload


def post_run(
    client: httpx.Client,
    runtime_url: str,
    *,
    tenant_id: str,
    agent_id: uuid.UUID,
    user_id: uuid.UUID,
    text: str,
    conversation_id: uuid.UUID | None = None,
    message_id: str | None = None,
    idempotency_key: str | None = None,
    payload: Mapping[str, Any] | None = None,
    timeout_sec: float = 60.0,
) -> RunPost:
    """真实 `POST /v1/runs`：受理持久化后才回 200/202，SSE 帧带真实 run_id。

    `payload` 可显式覆盖请求体（E-01 要**并发投递同一份请求**，不能各造一份）。
    """
    body = (
        dict(payload)
        if payload is not None
        else build_run_payload(
            agent_id=agent_id,
            user_id=user_id,
            text=text,
            conversation_id=conversation_id,
            message_id=message_id,
        )
    )
    headers = runtime_headers(tenant_id)
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    response = client.post(
        f"{runtime_url}/v1/runs", json=body, headers=headers, timeout=timeout_sec
    )
    events = _sse_events(response.text)
    run_id = None
    for event in events:
        candidate = event.get("run_id")
        if candidate:
            run_id = uuid.UUID(str(candidate))
            break
    return RunPost(status_code=response.status_code, run_id=run_id, body=response.text, events=events)


def run_row(database_url: str, tenant_id: str, run_id: uuid.UUID) -> dict[str, Any] | None:
    return fetch_one(
        database_url,
        "SELECT id, status, error_code, queue_stage, deadline_at, execution_epoch"
        " FROM runtime.run_record WHERE tenant_id = $1 AND id = $2",
        tenant_id,
        run_id,
    )


def wait_run_terminal(
    database_url: str, tenant_id: str, run_id: uuid.UUID, *, timeout_sec: float = 60.0
) -> dict[str, Any]:
    """等真实 Run 行到终态（PG 权威事实，不看内存状态）。"""
    row = wait_until(
        lambda: value
        if (value := run_row(database_url, tenant_id, run_id))
        and value["status"] in TERMINAL_RUN_STATUSES
        else None,
        timeout_sec=timeout_sec,
        label=f"run {run_id} terminal",
    )
    return dict(row)


def probe_control(
    client: httpx.Client, probe_url: str, path: str, payload: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """TASK-106 探针控制面（真实 HTTP）：behavior / key-limits / reset。"""
    response = client.post(f"{probe_url}/__probe__/{path}", json=dict(payload or {}))
    assert response.status_code == 200, f"{path}: {response.status_code} {response.text}"
    return dict(response.json())


def probe_state(client: httpx.Client, probe_url: str) -> dict[str, Any]:
    response = client.get(f"{probe_url}/__probe__/state")
    assert response.status_code == 200, response.text
    return dict(response.json())


def probe_calls(client: httpx.Client, probe_url: str) -> list[dict[str, Any]]:
    """逐次真实调用的请求体快照（Key 只有指纹，凭据原文永不出现）。"""
    response = client.get(f"{probe_url}/__probe__/calls")
    assert response.status_code == 200, response.text
    return list(response.json().get("calls", []))


def probe_target_payload(model_id: uuid.UUID, credential_id: uuid.UUID) -> dict[str, Any]:
    """Console 物理 Key 检测（API-16）的目标载荷：只探测被点名的成员。"""
    return {"targets": [{"model_id": str(model_id), "credential_id": str(credential_id)}]}


# --------------------------------------------------------------------------------------
# API-17：真实容量端口（预占/结算）——探针与主调用共用同一份额度存储
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProbeCapacity:
    """一次 API-17 调用的结果：HTTP 状态码 + 封套 data（或错误码）。"""

    status_code: int
    data: dict[str, Any]
    code: str | None = None


def probe_capacity_reserve(
    client: httpx.Client,
    runtime_url: str,
    *,
    headers: Mapping[str, str],
    model_id: uuid.UUID,
    credential_id: uuid.UUID,
    reserved_tokens: int = 16,
    probe_id: uuid.UUID | None = None,
    attempt_id: uuid.UUID | None = None,
    deadline_sec: int = 30,
) -> ProbeCapacity:
    from datetime import UTC, datetime, timedelta

    body = {
        "probe_id": str(probe_id or uuid.uuid4()),
        "model_id": str(model_id),
        "credential_id": str(credential_id),
        "attempt_id": str(attempt_id or uuid.uuid4()),
        "deadline_at": (datetime.now(UTC) + timedelta(seconds=deadline_sec)).isoformat(),
        "reserved_tokens": reserved_tokens,
    }
    return _probe_call(client, runtime_url, headers, "POST", "/reservations", body)


def probe_capacity_settle(
    client: httpx.Client,
    runtime_url: str,
    *,
    headers: Mapping[str, str],
    reservation_id: str,
    epoch: int,
    outcome: str,
    usage: Mapping[str, int] | None = None,
    remote_completion: bool = False,
) -> ProbeCapacity:
    from datetime import UTC, datetime

    body: dict[str, Any] = {"epoch": epoch, "outcome": outcome, "usage": usage}
    if remote_completion:
        body["remote_completion"] = {
            "kind": "PROVIDER_CONFIRMED",
            "reference": "capacity-acceptance:http-200",
            "completed_at": datetime.now(UTC).isoformat(),
        }
    return _probe_call(
        client, runtime_url, headers, "POST", f"/reservations/{reservation_id}/settle", body
    )


def _probe_call(
    client: httpx.Client,
    runtime_url: str,
    headers: Mapping[str, str],
    method: str,
    path: str,
    body: Mapping[str, Any],
) -> ProbeCapacity:
    response = client.request(
        method, f"{runtime_url}/internal/model-capacity{path}", json=dict(body), headers=dict(headers)
    )
    try:
        parsed = response.json()
    except ValueError:
        return ProbeCapacity(response.status_code, {}, None)
    if isinstance(parsed, Mapping) and isinstance(parsed.get("data"), Mapping):
        return ProbeCapacity(response.status_code, dict(parsed["data"]), None)
    code = parsed.get("code") if isinstance(parsed, Mapping) else None
    return ProbeCapacity(response.status_code, {}, str(code) if code else None)


# --------------------------------------------------------------------------------------
# Worker 任务现场（E-15）：真实 Skill 包 + 真实提交体
# --------------------------------------------------------------------------------------


def write_skill_package(artifact_root: Path, storage_key: str, script: str) -> str:
    """把真实 Skill 包写进共享卷（RWX 口径），返回真实 checksum。"""
    import tempfile
    import zipfile

    target = artifact_root / storage_key
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as staging:
        package = Path(staging)
        (package / "scripts").mkdir()
        (package / "scripts" / "main.py").write_text(script, encoding="utf-8")
        with zipfile.ZipFile(target, "w") as archive:
            archive.write(package / "scripts" / "main.py", "scripts/main.py")
    return "sha256:" + hashlib.sha256(target.read_bytes()).hexdigest()


def build_task_body(
    *,
    tenant_id: str,
    agent_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    skill_id: uuid.UUID,
    artifact_id: uuid.UUID,
    storage_key: str,
    checksum: str,
    idempotency_key: str,
    input_data: Mapping[str, Any],
    retry_safety: str,
    max_attempts: int = 3,
    delivery_mode: str = "NONE",
) -> dict[str, Any]:
    """按 Runtime Owner 的冻结口径组装 Task 提交体（真实 Worker 受理边界）。

    快照由**唯一构建器**（`muad_contracts.snapshot.build_task_snapshot`）产出：必需版本键、
    字段白名单与 `snapshot_hash` 口径都跟生产同源，不在用例里重抄一份形状。

    `budget.retry_safety` 是设计 §3.6 的可信冻结键：Owner 侧工具声明一次冻结，Worker 侧只读它；
    未声明/未知一律 NON_RETRYABLE（空值 = 不声明该键，用于覆盖"未声明"分支）。
    """
    from muad_contracts import ResolvedAgent, ResolvedModel, ResolvedSkill, SkillExecutionMode
    from muad_contracts.snapshot import build_task_snapshot, snapshot_hash

    agent = ResolvedAgent(
        id=agent_id, key="capacity-agent", revision=1, instructions="", runtime_config={}
    )
    model = ResolvedModel(
        id=uuid.uuid4(), revision=1, model_id="gpt-4o-mini", base_url="http://127.0.0.1:1/v1"
    )
    skill = ResolvedSkill(
        skill_id=skill_id,
        artifact_id=artifact_id,
        key="capacity_external_write",
        name="Capacity External Write",
        description="",
        version="1.0.0",
        checksum=checksum,
        storage_key=storage_key,
        execution_mode=SkillExecutionMode.ASYNC,
    )
    snapshot = build_task_snapshot(agent=agent, model=model, skill=skill)
    budget: dict[str, Any] = {**snapshot["budget"], "max_attempts": max_attempts}
    if retry_safety:
        budget["retry_safety"] = retry_safety
    snapshot["budget"] = budget
    return {
        "tenant_id": tenant_id,
        "agent_id": str(agent_id),
        "actor_user_id": str(actor_user_id),
        "intent_key": "capacity_external_write",
        "skill_id": str(skill_id),
        "skill_artifact_id": str(artifact_id),
        "input": dict(input_data),
        "execution_snapshot": snapshot,
        "execution_snapshot_schema_version": 1,
        "snapshot_hash": snapshot_hash(snapshot),
        "idempotency_key": idempotency_key,
        "delivery_mode": delivery_mode,
    }


def submit_task(
    client: httpx.Client,
    worker_url: str,
    *,
    headers: Mapping[str, str],
    body: Mapping[str, Any],
    idempotency_key: str,
) -> tuple[int, dict[str, Any]]:
    """真实 `POST /internal/tasks`：同键同指纹重放、异指纹 IDEMPOTENCY_MISMATCH。"""
    response = client.post(
        f"{worker_url}/internal/tasks",
        json=dict(body),
        headers={**dict(headers), "Idempotency-Key": idempotency_key},
    )
    try:
        parsed = response.json()
    except ValueError:
        return response.status_code, {}
    if isinstance(parsed, Mapping) and isinstance(parsed.get("data"), Mapping):
        return response.status_code, dict(parsed["data"])
    return response.status_code, dict(parsed) if isinstance(parsed, Mapping) else {}


def task_row(database_url: str, tenant_id: str, task_id: str) -> dict[str, Any] | None:
    row = fetch_one(
        database_url,
        "SELECT id, status, attempt, max_attempts, error_code, lease_owner, lease_until,"
        " result_json, idempotency_key, not_before"
        " FROM task.task_execution WHERE tenant_id = $1 AND id = $2",
        tenant_id,
        uuid.UUID(task_id),
    )
    if row is not None and isinstance(row.get("result_json"), str):
        # asyncpg 默认不解析 jsonb：按真实 JSON 文本回读成对象（不让用例看见字符串）
        row["result_json"] = json.loads(row["result_json"])
    return row


def task_events(database_url: str, tenant_id: str, task_id: str) -> list[str]:
    rows = fetch_all(
        database_url,
        "SELECT event_type FROM task.task_event WHERE tenant_id = $1 AND task_id = $2"
        " ORDER BY seq ASC",
        tenant_id,
        uuid.UUID(task_id),
    )
    return [str(row["event_type"]) for row in rows]


#: 收尾清理顺序：引用方先删（FK 依赖），再删被引用行；只按本用例自己的租户清。
_CLEANUP_STATEMENTS: tuple[str, ...] = (
    "DELETE FROM task.runtime_result_outbox WHERE tenant_id = $1",
    "DELETE FROM task.runtime_operation WHERE tenant_id = $1",
    "DELETE FROM task.task_event WHERE tenant_id = $1",
    "DELETE FROM task.task_submission WHERE tenant_id = $1",
    "DELETE FROM task.task_execution WHERE tenant_id = $1",
    "DELETE FROM task.delivery_route WHERE tenant_id = $1",
    # 引用方先删：模型租约引用 run/记忆 job/probe，容量行与审计同样先于它们的被引用方
    "DELETE FROM runtime.model_call_lease WHERE tenant_id = $1",
    "DELETE FROM runtime.model_capacity WHERE tenant_id = $1",
    "DELETE FROM runtime.model_dispatch_state WHERE tenant_id = $1",
    "DELETE FROM runtime.model_invocation_audit WHERE tenant_id = $1",
    "DELETE FROM runtime.memory_consolidation_job WHERE tenant_id = $1",
    "DELETE FROM runtime.memory_command_receipt WHERE tenant_id = $1",
    "DELETE FROM runtime.memory_scope WHERE tenant_id = $1",
    "DELETE FROM runtime.user_memory_history WHERE tenant_id = $1",
    "DELETE FROM runtime.user_memory WHERE tenant_id = $1",
    "DELETE FROM runtime.tool_result_inbox WHERE tenant_id = $1",
    "DELETE FROM runtime.tool_control_outbox WHERE tenant_id = $1",
    "DELETE FROM runtime.tool_operation WHERE tenant_id = $1",
    "DELETE FROM runtime.run_continuation WHERE tenant_id = $1",
    "DELETE FROM runtime.canonical_event WHERE tenant_id = $1",
    "DELETE FROM runtime.run_interrupt WHERE tenant_id = $1",
    "DELETE FROM runtime.run_submission WHERE tenant_id = $1",
    "DELETE FROM runtime.channel_notification WHERE tenant_id = $1",
    "DELETE FROM runtime.channel_notification_feed WHERE tenant_id = $1",
    "DELETE FROM runtime.channel_conversation WHERE tenant_id = $1",
    "DELETE FROM runtime.channel_upload WHERE tenant_id = $1",
    "DELETE FROM runtime.artifact WHERE tenant_id = $1",
    "DELETE FROM runtime.runtime_snapshot WHERE tenant_id = $1",
    "DELETE FROM runtime.egress_audit WHERE tenant_id = $1",
    "DELETE FROM runtime.tool_call_audit WHERE tenant_id = $1",
    "DELETE FROM runtime.run_record WHERE tenant_id = $1",
    "DELETE FROM runtime.conversation WHERE tenant_id = $1",
    "DELETE FROM runtime.run_admission_policy WHERE tenant_id = $1",
    "DELETE FROM control.agent_access_grant WHERE agent_id IN"
    " (SELECT id FROM control.agent_definition WHERE tenant_id = $1)",
    "DELETE FROM control.agent_skill_binding WHERE agent_id IN"
    " (SELECT id FROM control.agent_definition WHERE tenant_id = $1)",
    "DELETE FROM control.agent_mcp_binding WHERE agent_id IN"
    " (SELECT id FROM control.agent_definition WHERE tenant_id = $1)",
    "DELETE FROM control.skill_artifact WHERE skill_id IN"
    " (SELECT id FROM control.skill WHERE tenant_id = $1)",
    "DELETE FROM control.skill WHERE tenant_id = $1",
    "DELETE FROM control.mcp_server WHERE tenant_id = $1",
    "DELETE FROM control.platform_user WHERE tenant_id = $1",
    "DELETE FROM control.agent_definition WHERE tenant_id = $1",
    "DELETE FROM control.model_deployment WHERE tenant_id = $1",
    "DELETE FROM control.model_credential WHERE tenant_id = $1",
    "DELETE FROM control.model_quota_group WHERE tenant_id = $1",
    "DELETE FROM control.model_definition WHERE tenant_id = $1",
    "DELETE FROM control.platform_setting WHERE tenant_id = $1",
    "DELETE FROM control.config_audit_log WHERE tenant_id = $1",
    "DELETE FROM control.console_session WHERE account_id IN"
    " (SELECT id FROM control.console_account WHERE tenant_id = $1)",
    "DELETE FROM control.console_account WHERE tenant_id = $1",
)


def cleanup_tenant(database_url: str, tenant_id: str) -> None:
    """收尾只清本用例租户：不留 PENDING 记忆 job / QUEUED 任务给后续套件（claim 不带租户谓词）。"""

    async def purge() -> None:
        connection = await asyncpg.connect(_dsn(database_url), timeout=30)
        try:
            for statement in _CLEANUP_STATEMENTS:
                await connection.execute(statement, tenant_id)
        finally:
            await connection.close()

    run_async(purge)
