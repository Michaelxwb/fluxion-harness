"""TASK-006：定时任务的 Task owner 容量协议（API-29，design §3.1.5/§3.4.1/§4.4 item 8/9）。

真实边界（不 mock 存储、租约或约束）：

- 真实 Runtime 业务服务：真实 uvicorn 127.0.0.1 上的真实 `main.app` 装配（真实服务身份门控、
  响应封套与路由挂载），请求走真实 HTTP socket；
- 真实 PostgreSQL：`control.model_definition/model_credential/model_deployment` 解析冻结目标，
  `task.task_execution` 是归属事实的来源，`runtime.model_capacity/model_call_lease` 承载预占；
  每用例独立租户，只写/删本租户行（无 DDL）；四选一 owner 约束由真实 DB 校验；
- 真实 Worker 侧客户端 `TaskCapacityClient` 参与其中一个用例：证明"失约即停手"的失败分类在
  调用方拿到的是可用信号，而不是靠测试自己读库判断。

关键断言（Checklist 1/2/3/5）：

- 只接受内部服务身份；归属按 Execution 行复核（跨租户 404、跨操作者/缺操作者 403），请求体不是
  信任来源；预占不创建 Run；
- Task owner 落进 `num_nonnulls(run_id,memory_job_id,probe_id,task_execution_id)=1`，`owner_type`
  与列一致，`workload` 固定 BACKGROUND；主模型与摘要模型复用同一容量协议；
- 预占/续租/结算都以 owner + lease fence 生效；过期续租即 `MODEL_CALL_LEASE_LOST`；重复结算幂等；
  token usage 与并发上限有明确结果；
- Probe/Run 租约不能被 Task 端口续租/结算（端口不互相充当对方的 API）。
"""

from __future__ import annotations

import asyncio
import hashlib
import socket
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
import uvicorn
from muad_agent_runtime.application.model_capacity_service import ModelCapacityService
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.model_capacity_store import RESOURCE_KEY
from muad_agent_runtime.main import app as runtime_app
from muad_agent_worker.infrastructure.task_capacity_client import (
    CapacityLeaseLost,
    CapacityUnavailable,
    TaskCapacityClient,
)
from muad_contracts import (
    TaskCallSettlement,
    TaskCapacityOwner,
    TaskCapacityReservationRequest,
    TaskLeaseRenewal,
)
from muad_contracts.model_pool import (
    ModelCapacityReservationRequest,
    ModelTokenUsage,
    RemoteCompletionProof,
    RunCallOwner,
    derive_model_key_resource_id,
)
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

TASK_RESERVATIONS = "/internal/model-capacity/task-reservations"
PROBE_RESERVATIONS = "/internal/model-capacity/reservations"
PROVIDER_KEY = "openai"
DEFAULT_RESERVED_TOKENS = 64


@dataclass(frozen=True, slots=True)
class Member:
    """一名模型成员（真实凭据 + 成员关系）的冻结身份。"""

    credential_id: uuid.UUID
    deployment_id: uuid.UUID
    api_key: str
    digest: str
    revision: int = 1
    quota_group_id: uuid.UUID | None = None

    def key_resource_id(self, tenant_id: str) -> uuid.UUID:
        return derive_model_key_resource_id(tenant_id, PROVIDER_KEY, self.digest)


@dataclass(frozen=True, slots=True)
class TaskTenant:
    tenant_id: str
    other_tenant_id: str
    model_id: uuid.UUID
    summary_model_id: uuid.UUID
    actor_user_id: uuid.UUID
    other_actor_user_id: uuid.UUID
    agent_id: uuid.UUID
    task_execution_id: uuid.UUID
    member: Member
    summary_member: Member


# --------------------------------------------------------------------------------------
# 真实 HTTP 服务（真实 main.app 装配 + 真实 socket）
# --------------------------------------------------------------------------------------


@asynccontextmanager
async def _serve(app: Any) -> AsyncIterator[str]:
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
    task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        for _ in range(200):
            if server.started:
                break
            await asyncio.sleep(0.01)
        assert server.started
        yield f"http://127.0.0.1:{listener.getsockname()[1]}"
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, timeout=10)
        listener.close()


@pytest.fixture
async def capacity_client() -> AsyncIterator[httpx.AsyncClient]:
    async with _serve(runtime_app) as url, httpx.AsyncClient(base_url=url, timeout=30.0) as client:
        yield client


@pytest.fixture
async def capacity_url() -> AsyncIterator[str]:
    async with _serve(runtime_app) as url:
        yield url


def _service_token() -> str:
    from muad_common import SharedSettings

    token = SharedSettings().internal_service_token
    assert token, "INTERNAL_SERVICE_TOKEN must be configured for the internal boundary"
    return token


def _headers(
    tenant: TaskTenant,
    *,
    actor: uuid.UUID | None = None,
    tenant_id: str | None = None,
    token: str | None = None,
) -> dict[str, str]:
    return {
        "X-Internal-Service": token if token is not None else _service_token(),
        "X-Tenant-Id": tenant_id or tenant.tenant_id,
        "X-Actor-User-Id": str(tenant.actor_user_id if actor is None else actor),
    }


# --------------------------------------------------------------------------------------
# 控制面 + Execution 播种 / 运行时行读取
# --------------------------------------------------------------------------------------


def _digest(api_key: str) -> str:
    return hashlib.sha256(api_key.encode()).hexdigest()


async def _insert_model(session: AsyncSession, tenant_id: str) -> uuid.UUID:
    model_id = uuid.uuid4()
    await session.execute(
        sa.text(
            "INSERT INTO control.model_definition (id, tenant_id, key, name, model_id,"
            " provider_key, params_json) VALUES (:id, :tenant_id, :key, :name, :provider_model,"
            " :provider_key, CAST(:params AS jsonb))"
        ).bindparams(
            id=model_id,
            tenant_id=tenant_id,
            key=f"model-{uuid.uuid4().hex[:10]}",
            name="task-model",
            provider_model="gpt-4o-mini",
            provider_key=PROVIDER_KEY,
            params='{"max_tokens": 8}',
        )
    )
    return model_id


async def _insert_member(
    session: AsyncSession,
    tenant_id: str,
    model_id: uuid.UUID,
    *,
    key_max_inflight: int,
) -> Member:
    credential_id, deployment_id = uuid.uuid4(), uuid.uuid4()
    api_key = f"sk-task-{uuid.uuid4().hex}"
    await session.execute(
        sa.text(
            "INSERT INTO control.model_credential (id, tenant_id, key, provider_key, base_url,"
            " api_key, api_key_digest, max_inflight, enabled, revision)"
            " VALUES (:id, :tenant_id, :key, :provider_key, :base_url, :api_key, :digest,"
            " :max_inflight, true, 1)"
        ).bindparams(
            id=credential_id,
            tenant_id=tenant_id,
            key=f"key-{uuid.uuid4().hex[:10]}",
            provider_key=PROVIDER_KEY,
            base_url="https://api.example.com/v1",
            api_key=api_key,
            digest=_digest(api_key),
            max_inflight=key_max_inflight,
        )
    )
    await session.execute(
        sa.text(
            "INSERT INTO control.model_deployment (id, tenant_id, key, model_id, credential_id,"
            " weight, enabled) VALUES (:id, :tenant_id, :key, :model_id, :credential_id, 1, true)"
        ).bindparams(
            id=deployment_id,
            tenant_id=tenant_id,
            key=f"member-{uuid.uuid4().hex[:10]}",
            model_id=model_id,
            credential_id=credential_id,
        )
    )
    return Member(
        credential_id=credential_id,
        deployment_id=deployment_id,
        api_key=api_key,
        digest=_digest(api_key),
    )


async def _insert_execution(
    session: AsyncSession,
    tenant_id: str,
    model_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    agent_id: uuid.UUID,
) -> uuid.UUID:
    execution_id = uuid.uuid4()
    snapshot = (
        '{"schema_version": 4, "mode": "AGENTIC", "agent": {"id": "'
        + str(agent_id)
        + '"}, "model": {"model_id": "'
        + str(model_id)
        + '"}}'
    )
    await session.execute(
        sa.text(
            "INSERT INTO task.task_execution (id, tenant_id, agent_id, actor_user_id, trigger_type,"
            " status, input_json, execution_snapshot_json, snapshot_hash, idempotency_key)"
            " VALUES (:id, :tenant_id, :agent_id, :actor_user_id, 'SCHEDULE', 'RUNNING',"
            " CAST(:input AS jsonb), CAST(:snapshot AS jsonb), :snapshot_hash, :idempotency_key)"
        ).bindparams(
            id=execution_id,
            tenant_id=tenant_id,
            agent_id=agent_id,
            actor_user_id=actor_user_id,
            input="{}",
            snapshot=snapshot,
            snapshot_hash="sha256:" + hashlib.sha256(snapshot.encode()).hexdigest(),
            idempotency_key=f"task-{uuid.uuid4().hex}",
        )
    )
    return execution_id


async def _seed_task_tenant(*, key_max_inflight: int = 2) -> TaskTenant:
    """真实控制面行 + 主/摘要两个模型 + 一条 Task Execution（归属事实的来源）。"""
    tenant_id = f"test-{uuid.uuid4().hex[:20]}"
    other_tenant_id = f"test-{uuid.uuid4().hex[:20]}"
    actor_user_id, other_actor_user_id, agent_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    async with get_session_factory()() as session:
        model_id = await _insert_model(session, tenant_id)
        summary_model_id = await _insert_model(session, tenant_id)
        member = await _insert_member(
            session, tenant_id, model_id, key_max_inflight=key_max_inflight
        )
        summary_member = await _insert_member(
            session, tenant_id, summary_model_id, key_max_inflight=key_max_inflight
        )
        task_execution_id = await _insert_execution(
            session, tenant_id, model_id, actor_user_id, agent_id
        )
        await session.commit()
    return TaskTenant(
        tenant_id=tenant_id,
        other_tenant_id=other_tenant_id,
        model_id=model_id,
        summary_model_id=summary_model_id,
        actor_user_id=actor_user_id,
        other_actor_user_id=other_actor_user_id,
        agent_id=agent_id,
        task_execution_id=task_execution_id,
        member=member,
        summary_member=summary_member,
    )


async def _cleanup_tenant(tenant_id: str) -> None:
    async with get_session_factory()() as session:
        for statement in (
            "DELETE FROM runtime.model_call_lease WHERE tenant_id = :tenant_id",
            "DELETE FROM runtime.model_capacity WHERE tenant_id = :tenant_id",
            "DELETE FROM runtime.model_invocation_audit WHERE tenant_id = :tenant_id",
            "DELETE FROM task.task_execution WHERE tenant_id = :tenant_id",
            "DELETE FROM control.model_deployment WHERE tenant_id = :tenant_id",
            "DELETE FROM control.model_credential WHERE tenant_id = :tenant_id",
            "DELETE FROM control.model_definition WHERE tenant_id = :tenant_id",
            "DELETE FROM control.model_quota_group WHERE tenant_id = :tenant_id",
        ):
            await session.execute(sa.text(statement), {"tenant_id": tenant_id})
        await session.commit()


@pytest.fixture
async def seed_task_tenant() -> AsyncIterator[Callable[..., Awaitable[TaskTenant]]]:
    created: list[str] = []

    async def seed(**kwargs: Any) -> TaskTenant:
        tenant = await _seed_task_tenant(**kwargs)
        created.append(tenant.tenant_id)
        return tenant

    try:
        yield seed
    finally:
        for tenant_id in created:
            await _cleanup_tenant(tenant_id)


async def _lease_row(tenant_id: str, reservation_id: str) -> dict[str, Any]:
    async with get_session_factory()() as session:
        row = (
            await session.execute(
                sa.text(
                    "SELECT id, call_attempt_id, run_id, memory_job_id, probe_id,"
                    " task_execution_id, owner_type, deployment_id, credential_id, key_resource_id,"
                    " quota_group_id, credential_revision, workload, lease_owner, epoch,"
                    " lease_until, reserved_tokens, state FROM runtime.model_call_lease"
                    " WHERE tenant_id = :tenant_id AND id = :reservation_id"
                ).bindparams(tenant_id=tenant_id, reservation_id=uuid.UUID(reservation_id))
            )
        ).mappings().one()
    return dict(row)


async def _scalar(tenant_id: str, statement: str) -> int:
    async with get_session_factory()() as session:
        return int(
            (await session.execute(sa.text(statement).bindparams(tenant_id=tenant_id))).scalar_one()
        )


async def _lease_count(tenant_id: str) -> int:
    return await _scalar(
        tenant_id, "SELECT count(*) FROM runtime.model_call_lease WHERE tenant_id = :tenant_id"
    )


async def _run_count(tenant_id: str) -> int:
    return await _scalar(
        tenant_id, "SELECT count(*) FROM runtime.run_record WHERE tenant_id = :tenant_id"
    )


async def _inflight(tenant_id: str, key_resource_id: uuid.UUID) -> int:
    async with get_session_factory()() as session:
        return int(
            (
                await session.execute(
                    sa.text(
                        "SELECT inflight FROM runtime.model_capacity WHERE tenant_id = :tenant_id"
                        " AND resource_type = :resource_type AND resource_id = :resource_id"
                    ).bindparams(
                        tenant_id=tenant_id,
                        resource_type=RESOURCE_KEY,
                        resource_id=key_resource_id,
                    )
                )
            ).scalar_one()
        )


async def _expire_lease(tenant_id: str, reservation_id: str) -> None:
    """把真实租约的期限拨到过去（本地时间事实，不伪造状态列）。"""
    async with get_session_factory()() as session:
        await session.execute(
            sa.text(
                "UPDATE runtime.model_call_lease SET lease_until = now() - interval '1 second'"
                " WHERE tenant_id = :tenant_id AND id = :reservation_id"
            ).bindparams(tenant_id=tenant_id, reservation_id=uuid.UUID(reservation_id)),
        )
        await session.commit()


# --------------------------------------------------------------------------------------
# 线协议辅助
# --------------------------------------------------------------------------------------


def _reservation_request(
    tenant: TaskTenant,
    *,
    member: Member | None = None,
    model_id: uuid.UUID | None = None,
    task_execution_id: uuid.UUID | None = None,
    attempt_id: uuid.UUID | None = None,
    reserved_tokens: int = DEFAULT_RESERVED_TOKENS,
    deadline_in_sec: float = 30.0,
    **overrides: Any,
) -> TaskCapacityReservationRequest:
    member = member or tenant.member
    payload: dict[str, Any] = {
        "call_attempt_id": attempt_id or uuid.uuid4(),
        "model_id": model_id or tenant.model_id,
        "deployment_id": member.deployment_id,
        "credential_id": member.credential_id,
        "credential_revision": member.revision,
        "key_resource_id": member.key_resource_id(tenant.tenant_id),
        "quota_group_id": member.quota_group_id,
        "owner": TaskCapacityOwner(
            task_execution_id=task_execution_id or tenant.task_execution_id
        ),
        "deadline_at": datetime.now(UTC) + timedelta(seconds=deadline_in_sec),
        "reserved_tokens": reserved_tokens,
    }
    payload.update(overrides)
    return TaskCapacityReservationRequest(**payload)


async def _reserve(
    client: httpx.AsyncClient,
    tenant: TaskTenant,
    body: TaskCapacityReservationRequest,
    **headers: Any,
) -> httpx.Response:
    return await client.post(
        TASK_RESERVATIONS,
        headers=_headers(tenant, **headers),
        json=body.model_dump(mode="json"),
    )


async def _granted(
    client: httpx.AsyncClient, tenant: TaskTenant, body: TaskCapacityReservationRequest
) -> dict[str, Any]:
    response = await _reserve(client, tenant, body)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["code"] == "0"
    return payload["data"]


async def _renew(
    client: httpx.AsyncClient,
    tenant: TaskTenant,
    reservation_id: str,
    payload: dict[str, Any],
    **headers: Any,
) -> httpx.Response:
    return await client.post(
        f"{TASK_RESERVATIONS}/{reservation_id}/renew",
        headers=_headers(tenant, **headers),
        json=payload,
    )


async def _settle(
    client: httpx.AsyncClient,
    tenant: TaskTenant,
    reservation_id: str,
    payload: dict[str, Any],
    **headers: Any,
) -> httpx.Response:
    return await client.post(
        f"{TASK_RESERVATIONS}/{reservation_id}/settle",
        headers=_headers(tenant, **headers),
        json=payload,
    )


def _proof() -> dict[str, Any]:
    return {
        "kind": "PROVIDER_CONFIRMED",
        "reference": f"provider-{uuid.uuid4().hex}",
        "completed_at": datetime.now(UTC).isoformat(),
    }


def _error_code(response: httpx.Response) -> str:
    return str(response.json()["code"])


# --------------------------------------------------------------------------------------
# 预占：受信身份 + Execution 归属复核
# --------------------------------------------------------------------------------------


async def test_reserve_lands_task_owner_in_four_way_constraint_as_background(
    capacity_client: httpx.AsyncClient,
    seed_task_tenant: Callable[..., Awaitable[TaskTenant]],
) -> None:
    """[Checklist 1/2] Task owner 落进四选一约束，workload=BACKGROUND，且不创建 Run。"""
    tenant = await seed_task_tenant(key_max_inflight=2)
    body = _reservation_request(tenant)
    response = await _reserve(capacity_client, tenant, body)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["code"] == "0"
    grant = payload["data"]
    assert set(grant) == {"reservation_id", "epoch", "lease_until"}
    for internal in ("api_key", "key_resource_id", "quota_group_id", "provider_key", "base_url"):
        assert internal not in response.text
    assert tenant.member.api_key not in response.text

    lease = await _lease_row(tenant.tenant_id, grant["reservation_id"])
    assert lease["owner_type"] == "TASK"
    assert uuid.UUID(str(lease["task_execution_id"])) == tenant.task_execution_id
    assert lease["run_id"] is None and lease["memory_job_id"] is None and lease["probe_id"] is None
    assert lease["workload"] == "BACKGROUND"
    assert lease["lease_owner"] == f"task:{tenant.task_execution_id}"
    assert lease["state"] == "ACTIVE"
    assert uuid.UUID(str(lease["call_attempt_id"])) == body.call_attempt_id
    assert lease["reserved_tokens"] == DEFAULT_RESERVED_TOKENS

    # 容量落在该物理 Key 的真实行上；本端口不创建 Run
    assert await _inflight(tenant.tenant_id, tenant.member.key_resource_id(tenant.tenant_id)) == 1
    assert await _run_count(tenant.tenant_id) == 0


async def test_reserve_rejects_body_owner_not_owned_by_tenant(
    capacity_client: httpx.AsyncClient,
    seed_task_tenant: Callable[..., Awaitable[TaskTenant]],
) -> None:
    """[Checklist 1/5] 跨租户：Execution 属于别人 ⇒ 404，body 声明的 owner 不是信任来源。"""
    tenant = await seed_task_tenant()
    response = await _reserve(
        capacity_client, tenant, _reservation_request(tenant), tenant_id=tenant.other_tenant_id
    )
    assert response.status_code == 404, response.text
    assert await _lease_count(tenant.tenant_id) == 0


async def test_reserve_rejects_actor_and_missing_actor(
    capacity_client: httpx.AsyncClient,
    seed_task_tenant: Callable[..., Awaitable[TaskTenant]],
) -> None:
    """[Checklist 1/5] 跨用户：操作者与 Execution 的 actor 不符或缺声明 ⇒ 403。"""
    tenant = await seed_task_tenant()
    body = _reservation_request(tenant)

    spoofed = await _reserve(capacity_client, tenant, body, actor=tenant.other_actor_user_id)
    assert spoofed.status_code == 403, spoofed.text
    assert _error_code(spoofed) == "TASK_ACCESS_DENIED"

    missing = await capacity_client.post(
        TASK_RESERVATIONS,
        headers={
            "X-Internal-Service": _service_token(),
            "X-Tenant-Id": tenant.tenant_id,
        },
        json=body.model_dump(mode="json"),
    )
    assert missing.status_code == 403, missing.text
    assert await _lease_count(tenant.tenant_id) == 0


async def test_reserve_requires_internal_service_identity(
    capacity_client: httpx.AsyncClient,
    seed_task_tenant: Callable[..., Awaitable[TaskTenant]],
) -> None:
    """[Checklist 1] 只允许内部服务身份：缺失/错误身份一律拒绝且不落租约。"""
    tenant = await seed_task_tenant()
    body = _reservation_request(tenant)
    missing = await capacity_client.post(
        TASK_RESERVATIONS,
        headers={"X-Tenant-Id": tenant.tenant_id, "X-Actor-User-Id": str(tenant.actor_user_id)},
        json=body.model_dump(mode="json"),
    )
    assert missing.status_code in (401, 403), missing.text
    wrong = await _reserve(capacity_client, tenant, body, token="wrong-token")
    assert wrong.status_code in (401, 403), wrong.text
    assert await _lease_count(tenant.tenant_id) == 0


async def test_reserve_requires_frozen_identity_to_still_match_current_facts(
    capacity_client: httpx.AsyncClient,
    seed_task_tenant: Callable[..., Awaitable[TaskTenant]],
) -> None:
    """[Checklist 3/5] 冻结成员/版本/计量身份与当前控制面不符 ⇒ MODEL_CREDENTIAL_CHANGED。"""
    tenant = await seed_task_tenant()
    rotated = _reservation_request(tenant, credential_revision=2)
    response = await _reserve(capacity_client, tenant, rotated)
    assert response.status_code == 409, response.text
    assert _error_code(response) == "MODEL_CREDENTIAL_CHANGED"

    other_key = _reservation_request(tenant, key_resource_id=uuid.uuid4())
    response = await _reserve(capacity_client, tenant, other_key)
    assert response.status_code == 409, response.text
    assert _error_code(response) == "MODEL_CREDENTIAL_CHANGED"
    assert await _lease_count(tenant.tenant_id) == 0


async def test_reserve_is_idempotent_per_call_attempt(
    capacity_client: httpx.AsyncClient,
    seed_task_tenant: Callable[..., Awaitable[TaskTenant]],
) -> None:
    """重试同一 attempt 复用同一租约：不重复占名额，owner 仍一致。"""
    tenant = await seed_task_tenant(key_max_inflight=1)
    attempt_id = uuid.uuid4()
    first = await _granted(
        capacity_client, tenant, _reservation_request(tenant, attempt_id=attempt_id)
    )
    replay = await _granted(
        capacity_client, tenant, _reservation_request(tenant, attempt_id=attempt_id)
    )
    assert replay["reservation_id"] == first["reservation_id"]
    assert await _lease_count(tenant.tenant_id) == 1
    assert await _inflight(tenant.tenant_id, tenant.member.key_resource_id(tenant.tenant_id)) == 1


async def test_reserve_rejects_when_concurrency_limit_is_reached(
    capacity_client: httpx.AsyncClient,
    seed_task_tenant: Callable[..., Awaitable[TaskTenant]],
) -> None:
    """[Checklist 3] 并发上限是真实上限：第二个在飞预占得到 MODEL_CAPACITY_UNAVAILABLE。"""
    tenant = await seed_task_tenant(key_max_inflight=1)
    await _granted(capacity_client, tenant, _reservation_request(tenant))

    blocked = await _reserve(capacity_client, tenant, _reservation_request(tenant))
    assert blocked.status_code == 503, blocked.text
    assert _error_code(blocked) == "MODEL_CAPACITY_UNAVAILABLE"
    assert await _lease_count(tenant.tenant_id) == 1


async def test_summary_model_uses_the_same_task_capacity_protocol(
    capacity_client: httpx.AsyncClient,
    seed_task_tenant: Callable[..., Awaitable[TaskTenant]],
) -> None:
    """[Checklist 2] 冻结模型池与摘要模型同一条容量协议：同一 owner、各自结算。"""
    tenant = await seed_task_tenant(key_max_inflight=2)
    main_grant = await _granted(capacity_client, tenant, _reservation_request(tenant))
    summary_grant = await _granted(
        capacity_client,
        tenant,
        _reservation_request(
            tenant,
            member=tenant.summary_member,
            model_id=tenant.summary_model_id,
            reserved_tokens=16,
        ),
    )
    main_lease = await _lease_row(tenant.tenant_id, main_grant["reservation_id"])
    summary_lease = await _lease_row(tenant.tenant_id, summary_grant["reservation_id"])
    for lease in (main_lease, summary_lease):
        assert lease["owner_type"] == "TASK"
        assert uuid.UUID(str(lease["task_execution_id"])) == tenant.task_execution_id
        assert lease["workload"] == "BACKGROUND"
        assert lease["lease_owner"] == f"task:{tenant.task_execution_id}"
    assert uuid.UUID(str(main_lease["key_resource_id"])) != uuid.UUID(
        str(summary_lease["key_resource_id"])
    )

    for reservation_id in (main_grant["reservation_id"], summary_grant["reservation_id"]):
        settled = await _settle(
            capacity_client,
            tenant,
            reservation_id,
            {"epoch": 1, "outcome": "NOT_SENT"},
        )
        assert settled.status_code == 200, settled.text
        assert settled.json()["data"]["state"] == "SETTLED"
    assert await _inflight(tenant.tenant_id, tenant.member.key_resource_id(tenant.tenant_id)) == 0
    assert (
        await _inflight(tenant.tenant_id, tenant.summary_member.key_resource_id(tenant.tenant_id))
        == 0
    )


# --------------------------------------------------------------------------------------
# 续租 / 结算：owner + lease fence
# --------------------------------------------------------------------------------------


async def test_renew_extends_lease_and_stops_on_lost_fence(
    capacity_client: httpx.AsyncClient,
    seed_task_tenant: Callable[..., Awaitable[TaskTenant]],
) -> None:
    """[Checklist 3] 续租只前进；过期与错误 epoch 都是 MODEL_CALL_LEASE_LOST（停手）。"""
    tenant = await seed_task_tenant()
    grant = await _granted(capacity_client, tenant, _reservation_request(tenant))
    before = datetime.fromisoformat(grant["lease_until"])

    deadline = datetime.now(UTC) + timedelta(seconds=60)
    renewed = await _renew(
        capacity_client, tenant, grant["reservation_id"], {"epoch": 1, "deadline_at": deadline.isoformat()}
    )
    assert renewed.status_code == 200, renewed.text
    assert datetime.fromisoformat(renewed.json()["data"]["lease_until"]) > before

    wrong_epoch = await _renew(
        capacity_client,
        tenant,
        grant["reservation_id"],
        {"epoch": 2, "deadline_at": deadline.isoformat()},
    )
    assert wrong_epoch.status_code == 409, wrong_epoch.text
    assert _error_code(wrong_epoch) == "MODEL_CALL_LEASE_LOST"

    await _expire_lease(tenant.tenant_id, grant["reservation_id"])
    expired = await _renew(
        capacity_client,
        tenant,
        grant["reservation_id"],
        {"epoch": 1, "deadline_at": (datetime.now(UTC) + timedelta(seconds=90)).isoformat()},
    )
    assert expired.status_code == 409, expired.text
    assert _error_code(expired) == "MODEL_CALL_LEASE_LOST"


async def test_settle_is_idempotent_and_usage_replaces_reservation(
    capacity_client: httpx.AsyncClient,
    seed_task_tenant: Callable[..., Awaitable[TaskTenant]],
) -> None:
    """[Checklist 3] 成功结算按实际 usage 释放；重复结算不再二次扣减。"""
    tenant = await seed_task_tenant()
    grant = await _granted(capacity_client, tenant, _reservation_request(tenant))
    key = tenant.member.key_resource_id(tenant.tenant_id)
    assert await _inflight(tenant.tenant_id, key) == 1

    settled = await _settle(
        capacity_client,
        tenant,
        grant["reservation_id"],
        {
            "epoch": 1,
            "outcome": "SUCCEEDED",
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "remote_completion": _proof(),
        },
    )
    assert settled.status_code == 200, settled.text
    assert settled.json()["data"]["state"] == "SETTLED"
    assert await _inflight(tenant.tenant_id, key) == 0

    replay = await _settle(
        capacity_client,
        tenant,
        grant["reservation_id"],
        {"epoch": 1, "outcome": "NOT_SENT"},
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["data"]["released"] is False
    assert await _inflight(tenant.tenant_id, key) == 0


async def test_settle_without_evidence_keeps_capacity_unknown(
    capacity_client: httpx.AsyncClient,
    seed_task_tenant: Callable[..., Awaitable[TaskTenant]],
) -> None:
    """[Checklist 3] 缺证据的结算保守保留占用（UNKNOWN），不假定供方未计费。"""
    tenant = await seed_task_tenant()
    grant = await _granted(capacity_client, tenant, _reservation_request(tenant))
    key = tenant.member.key_resource_id(tenant.tenant_id)

    parked = await _settle(
        capacity_client, tenant, grant["reservation_id"], {"epoch": 1, "outcome": "UNKNOWN"}
    )
    assert parked.status_code == 200, parked.text
    assert parked.json()["data"] == {
        "reservation_id": grant["reservation_id"],
        "state": "UNKNOWN",
        "released": False,
    }
    assert await _inflight(tenant.tenant_id, key) == 1
    assert (await _lease_row(tenant.tenant_id, grant["reservation_id"]))["state"] == "UNKNOWN"


async def test_task_port_never_serves_probe_or_run_leases(
    capacity_client: httpx.AsyncClient,
    seed_task_tenant: Callable[..., Awaitable[TaskTenant]],
) -> None:
    """[Checklist 5/约束] 端口不互相充当对方的 API：Probe 租约不能被 Task 端口结算。"""
    tenant = await seed_task_tenant()
    probe_body = {
        "probe_id": str(uuid.uuid4()),
        "model_id": str(tenant.model_id),
        "credential_id": str(tenant.member.credential_id),
        "attempt_id": str(uuid.uuid4()),
        "deadline_at": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
        "reserved_tokens": 8,
    }
    probe = await capacity_client.post(
        PROBE_RESERVATIONS,
        headers={
            "X-Internal-Service": _service_token(),
            "X-Tenant-Id": tenant.tenant_id,
        },
        json=probe_body,
    )
    assert probe.status_code == 200, probe.text
    probe_reservation = probe.json()["data"]["reservation_id"]

    rejected = await _settle(
        capacity_client, tenant, probe_reservation, {"epoch": 1, "outcome": "NOT_SENT"}
    )
    assert rejected.status_code == 404, rejected.text
    probe_lease = await _lease_row(tenant.tenant_id, probe_reservation)
    assert probe_lease["state"] == "ACTIVE"
    # Probe 租约的 owner 列与 owner_type 仍是同一事实（新增列不改变原有归属口径）
    assert probe_lease["owner_type"] == "PROBE"
    assert probe_lease["task_execution_id"] is None


# --------------------------------------------------------------------------------------
# Worker 客户端：失败分类驱动"失约即停手"
# --------------------------------------------------------------------------------------


async def test_worker_client_reserves_and_stops_on_lost_lease(
    capacity_url: str,
    seed_task_tenant: Callable[..., Awaitable[TaskTenant]],
) -> None:
    """[Checklist 3] 真实客户端：预占成功拿到栅栏事实，失约抛 `CapacityLeaseLost`。"""
    tenant = await seed_task_tenant(key_max_inflight=1)
    client = TaskCapacityClient(capacity_url, service_token=_service_token())
    try:
        grant = await client.reserve(
            tenant_id=tenant.tenant_id,
            actor_user_id=tenant.actor_user_id,
            request=_reservation_request(tenant, attempt_id=uuid.uuid4()),
        )
        assert grant.epoch == 1
        with pytest.raises(CapacityUnavailable):
            await client.reserve(
                tenant_id=tenant.tenant_id,
                actor_user_id=tenant.actor_user_id,
                request=_reservation_request(tenant, attempt_id=uuid.uuid4()),
            )
        with pytest.raises(CapacityLeaseLost):
            await client.renew(
                tenant_id=tenant.tenant_id,
                actor_user_id=tenant.actor_user_id,
                reservation_id=grant.reservation_id,
                renewal=TaskLeaseRenewal(
                    epoch=2, deadline_at=datetime.now(UTC) + timedelta(seconds=30)
                ),
            )
        release = await client.settle(
            tenant_id=tenant.tenant_id,
            actor_user_id=tenant.actor_user_id,
            reservation_id=grant.reservation_id,
            settlement=TaskCallSettlement(
                epoch=1,
                outcome="SUCCEEDED",
                usage=ModelTokenUsage(input_tokens=4, output_tokens=2),
                remote_completion=RemoteCompletionProof(
                    kind="PROVIDER_CONFIRMED",
                    reference=f"provider-{uuid.uuid4().hex}",
                    completed_at=datetime.now(UTC),
                ),
            ),
        )
        assert release.state == "SETTLED"
        assert release.released is True
    finally:
        await client.aclose()


async def test_run_and_memory_job_owners_are_unchanged(
    capacity_client: httpx.AsyncClient,
    seed_task_tenant: Callable[..., Awaitable[TaskTenant]],
) -> None:
    """[Checklist 5] 原有 owner 行为不变：Task 端口不接管 Run/job 的调用。

    直接经服务层预占一个 Run owner 租约（真实 PG + 四选一约束），再用 Task 端口结算该租约：
    必须 404 —— 端口按 `owner_type/workload` 收窄，不能把 Run 的调用当 Task 的。
    """
    tenant = await seed_task_tenant()
    async with get_session_factory()() as session:
        conversation_id, run_id = uuid.uuid4(), uuid.uuid4()
        await session.execute(
            sa.text(
                "INSERT INTO runtime.conversation (id, tenant_id, user_id, agent_id)"
                " VALUES (:id, :tenant_id, :user_id, :agent_id)"
            ).bindparams(
                id=conversation_id,
                tenant_id=tenant.tenant_id,
                user_id=uuid.uuid4(),
                agent_id=tenant.agent_id,
            )
        )
        await session.execute(
            sa.text(
                "INSERT INTO runtime.run_record (id, tenant_id, conversation_id, user_id, agent_id,"
                " status, input_text, trace_id) VALUES (:id, :tenant_id, :conversation_id,"
                " :user_id, :agent_id, 'RUNNING', 'task-006-owner-parity', :trace_id)"
            ).bindparams(
                id=run_id,
                tenant_id=tenant.tenant_id,
                conversation_id=conversation_id,
                user_id=tenant.actor_user_id,
                agent_id=tenant.agent_id,
                trace_id=uuid.uuid4().hex,
            )
        )
        await session.commit()

    service = ModelCapacityService(get_session_factory)
    reservation = await service.reserve(
        tenant_id=tenant.tenant_id,
        lease_owner="rt-task-006",
        request=ModelCapacityReservationRequest(
            call_attempt_id=uuid.uuid4(),
            model_id=tenant.model_id,
            deployment_id=tenant.member.deployment_id,
            credential_id=tenant.member.credential_id,
            credential_revision=tenant.member.revision,
            key_resource_id=tenant.member.key_resource_id(tenant.tenant_id),
            quota_group_id=None,
            owner=RunCallOwner(run_id=run_id),
            workload="INTERACTIVE",
            deadline_at=datetime.now(UTC) + timedelta(seconds=30),
            reserved_tokens=16,
        ),
    )
    lease = await _lease_row(tenant.tenant_id, str(reservation.reservation_id))
    assert lease["owner_type"] == "RUN"
    assert lease["task_execution_id"] is None
    assert str(lease["run_id"]) == str(run_id)

    rejected = await _settle(
        capacity_client, tenant, str(reservation.reservation_id), {"epoch": 1, "outcome": "NOT_SENT"}
    )
    assert rejected.status_code == 404, rejected.text
    assert (await _lease_row(tenant.tenant_id, str(reservation.reservation_id)))["state"] == "ACTIVE"
