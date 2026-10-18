"""B-227：Console 探针的受信容量端口（API-17，design §3.4/§3.7）。

真实边界（不 mock 存储或租约）：

- 真实 Runtime 业务服务：真实 uvicorn 127.0.0.1 上的真实 `main.app` 装配（真实服务身份门控、
  响应封套与路由挂载），请求走真实 HTTP socket；
- 真实 PostgreSQL：`control.model_definition/model_credential/model_deployment` 解析探针目标
  身份，`runtime.model_capacity/model_call_lease` 承载预占事实；每用例独立租户，只写/删本租户
  行（无 DDL）；
- 主调用侧用同一真实 `ModelCapacityService`（TASK-025 池化执行链的预占路径）在同一物理 Key
  与共享额度组上竞争，证明探针不绕过资源池；
- 探针 HTTP 仍由 Console 执行（TASK-028）：本端口不调用模型，故无模型探针 HTTP 依赖；也不写
  `runtime.model_invocation_audit`（模型计费审计）。

关键断言（Acceptance Contract B-227）：API-17 只接受受信 probe 身份、正确 attempt/epoch 与
期限；reserve/renew/settle 幂等；探针与 Run/job 共占实际 Key 及账号额度；响应不含密钥或内部
计量身份。
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
from muad_agent_runtime.infrastructure.model_capacity_store import (
    RESOURCE_KEY,
    RESOURCE_QUOTA_GROUP,
)
from muad_agent_runtime.main import app as runtime_app
from muad_contracts.model_pool import (
    ModelCapacityReservationRequest,
    RunCallOwner,
    derive_model_key_resource_id,
)

pytestmark = pytest.mark.integration

PROBE_RESERVATIONS = "/internal/model-capacity/reservations"
PROVIDER_KEY = "openai"
DEFAULT_RESERVED_TOKENS = 64


@dataclass(frozen=True, slots=True)
class ProbeMember:
    """一名模型成员（真实凭据 + 成员关系）的冻结身份。"""

    credential_id: uuid.UUID
    deployment_id: uuid.UUID
    api_key: str
    digest: str
    quota_group_id: uuid.UUID | None

    def key_resource_id(self, tenant_id: str) -> uuid.UUID:
        """与 TASK-025 池化链路同源的稳定计量身份（由 tenant/provider/摘要派生）。"""
        return derive_model_key_resource_id(tenant_id, PROVIDER_KEY, self.digest)


@dataclass(frozen=True, slots=True)
class ProbeTenant:
    tenant_id: str
    model_id: uuid.UUID
    members: tuple[ProbeMember, ...]
    quota_group_id: uuid.UUID | None

    @property
    def member(self) -> ProbeMember:
        return self.members[0]


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
async def probe_client() -> AsyncIterator[httpx.AsyncClient]:
    async with _serve(runtime_app) as url, httpx.AsyncClient(base_url=url, timeout=30.0) as client:
        yield client


def _service_token() -> str:
    from muad_common import SharedSettings

    token = SharedSettings().internal_service_token
    assert token, "INTERNAL_SERVICE_TOKEN must be configured for the internal boundary"
    return token


def _auth_headers(tenant: ProbeTenant, *, token: str | None = None) -> dict[str, str]:
    return {
        "X-Internal-Service": token if token is not None else _service_token(),
        "X-Tenant-Id": tenant.tenant_id,
    }


# --------------------------------------------------------------------------------------
# 控制面播种 / 运行时行读取
# --------------------------------------------------------------------------------------


async def _seed_tenant(
    *,
    key_max_inflight: int = 2,
    group_max_inflight: int | None = None,
    member_count: int = 1,
    credential_enabled: bool = True,
) -> ProbeTenant:
    """真实控制面行：模型定义 + 可选共享额度组 + N 名成员（凭据 + 成员关系）。"""
    tenant_id = f"test-{uuid.uuid4().hex[:20]}"
    model_id = uuid.uuid4()
    quota_group_id: uuid.UUID | None = None
    members: list[ProbeMember] = []
    async with get_session_factory()() as session:
        if group_max_inflight is not None:
            quota_group_id = uuid.uuid4()
            await session.execute(
                sa.text(
                    "INSERT INTO control.model_quota_group (id, tenant_id, key, max_inflight, revision)"
                    " VALUES (:id, :tenant_id, :key, :max_inflight, 1)"
                ).bindparams(
                    id=quota_group_id,
                    tenant_id=tenant_id,
                    key=f"group-{uuid.uuid4().hex[:12]}",
                    max_inflight=group_max_inflight,
                )
            )
        await session.execute(
            sa.text(
                "INSERT INTO control.model_definition (id, tenant_id, key, name, model_id,"
                " provider_key, params_json) VALUES (:id, :tenant_id, :key, :name,"
                " :provider_model, :provider_key, CAST(:params AS jsonb))"
            ).bindparams(
                id=model_id,
                tenant_id=tenant_id,
                key=f"model-{uuid.uuid4().hex[:10]}",
                name="probe-model",
                provider_model="gpt-4o-mini",
                provider_key=PROVIDER_KEY,
                params='{"max_tokens": 1}',
            )
        )
        for index in range(member_count):
            credential_id, deployment_id = uuid.uuid4(), uuid.uuid4()
            api_key = f"sk-probe-{index}-{uuid.uuid4().hex}"
            digest = hashlib.sha256(api_key.encode()).hexdigest()
            await session.execute(
                sa.text(
                    "INSERT INTO control.model_credential (id, tenant_id, key, provider_key,"
                    " base_url, api_key, api_key_digest, quota_group_id, max_inflight, enabled,"
                    " revision) VALUES (:id, :tenant_id, :key, :provider_key, :base_url,"
                    " :api_key, :digest, :quota_group_id, :max_inflight, :enabled, 1)"
                ).bindparams(
                    id=credential_id,
                    tenant_id=tenant_id,
                    key=f"key-{index}-{uuid.uuid4().hex[:10]}",
                    provider_key=PROVIDER_KEY,
                    base_url="https://api.example.com/v1",
                    api_key=api_key,
                    digest=digest,
                    quota_group_id=quota_group_id,
                    max_inflight=key_max_inflight,
                    enabled=credential_enabled,
                )
            )
            await session.execute(
                sa.text(
                    "INSERT INTO control.model_deployment (id, tenant_id, key, model_id,"
                    " credential_id, weight, enabled) VALUES (:id, :tenant_id, :key, :model_id,"
                    " :credential_id, 1, true)"
                ).bindparams(
                    id=deployment_id,
                    tenant_id=tenant_id,
                    key=f"member-{index}-{uuid.uuid4().hex[:10]}",
                    model_id=model_id,
                    credential_id=credential_id,
                )
            )
            members.append(
                ProbeMember(
                    credential_id=credential_id,
                    deployment_id=deployment_id,
                    api_key=api_key,
                    digest=digest,
                    quota_group_id=quota_group_id,
                )
            )
        await session.commit()
    return ProbeTenant(
        tenant_id=tenant_id,
        model_id=model_id,
        members=tuple(members),
        quota_group_id=quota_group_id,
    )


async def _cleanup_tenant(tenant_id: str) -> None:
    async with get_session_factory()() as session:
        for statement in (
            "DELETE FROM runtime.model_invocation_audit WHERE tenant_id = :tenant_id",
            "DELETE FROM runtime.model_call_lease WHERE tenant_id = :tenant_id",
            "DELETE FROM runtime.model_capacity WHERE tenant_id = :tenant_id",
            "DELETE FROM control.model_deployment WHERE tenant_id = :tenant_id",
            "DELETE FROM control.model_credential WHERE tenant_id = :tenant_id",
            "DELETE FROM control.model_definition WHERE tenant_id = :tenant_id",
            "DELETE FROM control.model_quota_group WHERE tenant_id = :tenant_id",
            "DELETE FROM runtime.run_record WHERE tenant_id = :tenant_id",
            "DELETE FROM runtime.conversation WHERE tenant_id = :tenant_id",
        ):
            await session.execute(sa.text(statement), {"tenant_id": tenant_id})
        await session.commit()


@pytest.fixture
async def seed_probe_tenant() -> AsyncIterator[Callable[..., Awaitable[ProbeTenant]]]:
    created: list[str] = []

    async def seed(**kwargs: Any) -> ProbeTenant:
        tenant = await _seed_tenant(**kwargs)
        created.append(tenant.tenant_id)
        return tenant

    try:
        yield seed
    finally:
        for tenant_id in created:
            await _cleanup_tenant(tenant_id)


async def _seed_run(tenant_id: str) -> uuid.UUID:
    """主调用侧的真实 Run 行（`model_call_lease.run_id` 有物理 FK，不能造逻辑引用）。"""
    conversation_id, run_id = uuid.uuid4(), uuid.uuid4()
    async with get_session_factory()() as session:
        await session.execute(
            sa.text(
                "INSERT INTO runtime.conversation (id, tenant_id, user_id, agent_id)"
                " VALUES (:id, :tenant_id, :user_id, :agent_id)"
            ).bindparams(
                id=conversation_id,
                tenant_id=tenant_id,
                user_id=uuid.uuid4(),
                agent_id=uuid.uuid4(),
            )
        )
        await session.execute(
            sa.text(
                "INSERT INTO runtime.run_record (id, tenant_id, conversation_id, user_id, agent_id,"
                " status, input_text, trace_id) VALUES (:id, :tenant_id, :conversation_id, :user_id,"
                " :agent_id, 'RUNNING', 'b227-probe-share', :trace_id)"
            ).bindparams(
                id=run_id,
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                user_id=uuid.uuid4(),
                agent_id=uuid.uuid4(),
                trace_id=uuid.uuid4().hex,
            )
        )
        await session.commit()
    return run_id


async def _lease_row(tenant_id: str, attempt_id: uuid.UUID) -> dict[str, Any]:
    async with get_session_factory()() as session:
        row = (
            await session.execute(
                sa.text(
                    "SELECT id, call_attempt_id, run_id, memory_job_id, probe_id, deployment_id,"
                    " credential_id, key_resource_id, quota_group_id, credential_revision, workload,"
                    " lease_owner, epoch, lease_until, reserved_tokens, state"
                    " FROM runtime.model_call_lease WHERE tenant_id = :tenant_id"
                    " AND call_attempt_id = :attempt_id"
                ).bindparams(tenant_id=tenant_id, attempt_id=attempt_id)
            )
        ).mappings().one()
    return dict(row)


async def _lease_by_reservation(tenant_id: str, reservation_id: uuid.UUID) -> dict[str, Any]:
    async with get_session_factory()() as session:
        row = (
            await session.execute(
                sa.text(
                    "SELECT id, call_attempt_id, run_id, memory_job_id, probe_id, workload,"
                    " lease_owner, epoch, lease_until, reserved_tokens, state"
                    " FROM runtime.model_call_lease WHERE tenant_id = :tenant_id AND id = :reservation_id"
                ).bindparams(tenant_id=tenant_id, reservation_id=reservation_id)
            )
        ).mappings().one()
    return dict(row)


async def _capacity_row(
    tenant_id: str, resource_type: str, resource_id: uuid.UUID
) -> dict[str, Any]:
    async with get_session_factory()() as session:
        row = (
            await session.execute(
                sa.text(
                    "SELECT inflight, reserved_requests, reserved_tokens FROM runtime.model_capacity"
                    " WHERE tenant_id = :tenant_id AND resource_type = :resource_type"
                    " AND resource_id = :resource_id"
                ).bindparams(
                    tenant_id=tenant_id, resource_type=resource_type, resource_id=resource_id
                )
            )
        ).mappings().one()
    return dict(row)


async def _counts(
    tenant: ProbeTenant, member: ProbeMember
) -> tuple[tuple[int, int, int], tuple[int, int, int] | None]:
    """(KEY 计数, 组计数)，均为 (inflight, reserved_requests, reserved_tokens)。"""
    key_row = await _capacity_row(tenant.tenant_id, RESOURCE_KEY, member.key_resource_id(tenant.tenant_id))
    key_counts = (key_row["inflight"], key_row["reserved_requests"], key_row["reserved_tokens"])
    if member.quota_group_id is None:
        return key_counts, None
    group_row = await _capacity_row(tenant.tenant_id, RESOURCE_QUOTA_GROUP, member.quota_group_id)
    return key_counts, (
        group_row["inflight"],
        group_row["reserved_requests"],
        group_row["reserved_tokens"],
    )


async def _scalar_count(tenant_id: str, statement: str) -> int:
    async with get_session_factory()() as session:
        return int(
            (
                await session.execute(sa.text(statement).bindparams(tenant_id=tenant_id))
            ).scalar_one()
        )


async def _lease_count(tenant_id: str) -> int:
    return await _scalar_count(
        tenant_id, "SELECT count(*) FROM runtime.model_call_lease WHERE tenant_id = :tenant_id"
    )


async def _capacity_row_count(tenant_id: str) -> int:
    return await _scalar_count(
        tenant_id, "SELECT count(*) FROM runtime.model_capacity WHERE tenant_id = :tenant_id"
    )


async def _audit_count(tenant_id: str) -> int:
    return await _scalar_count(
        tenant_id, "SELECT count(*) FROM runtime.model_invocation_audit WHERE tenant_id = :tenant_id"
    )


# --------------------------------------------------------------------------------------
# 线协议辅助
# --------------------------------------------------------------------------------------


def _reserve_body(
    tenant: ProbeTenant,
    *,
    attempt_id: uuid.UUID | None = None,
    probe_id: uuid.UUID | None = None,
    member: ProbeMember | None = None,
    reserved_tokens: int = DEFAULT_RESERVED_TOKENS,
    deadline_in_sec: float = 30.0,
) -> dict[str, Any]:
    member = member or tenant.member
    return {
        "probe_id": str(probe_id or uuid.uuid4()),
        "model_id": str(tenant.model_id),
        "credential_id": str(member.credential_id),
        "attempt_id": str(attempt_id or uuid.uuid4()),
        "deadline_at": (datetime.now(UTC) + timedelta(seconds=deadline_in_sec)).isoformat(),
        "reserved_tokens": reserved_tokens,
    }


def _proof(kind: str, reference: str) -> dict[str, Any]:
    return {
        "kind": kind,
        "reference": reference,
        "completed_at": datetime.now(UTC).isoformat(),
    }


async def _reserve(
    client: httpx.AsyncClient, tenant: ProbeTenant, body: dict[str, Any]
) -> httpx.Response:
    return await client.post(PROBE_RESERVATIONS, headers=_auth_headers(tenant), json=body)


async def _renew(
    client: httpx.AsyncClient, tenant: ProbeTenant, reservation_id: str, payload: dict[str, Any]
) -> httpx.Response:
    return await client.post(
        f"{PROBE_RESERVATIONS}/{reservation_id}/renew",
        headers=_auth_headers(tenant),
        json=payload,
    )


async def _settle(
    client: httpx.AsyncClient, tenant: ProbeTenant, reservation_id: str, payload: dict[str, Any]
) -> httpx.Response:
    return await client.post(
        f"{PROBE_RESERVATIONS}/{reservation_id}/settle",
        headers=_auth_headers(tenant),
        json=payload,
    )


async def _granted(
    client: httpx.AsyncClient, tenant: ProbeTenant, body: dict[str, Any]
) -> dict[str, Any]:
    response = await _reserve(client, tenant, body)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["code"] == "0"
    return payload["data"]


def _deadline_of(body: dict[str, Any]) -> datetime:
    return datetime.fromisoformat(body["deadline_at"])


def _run_request(
    tenant: ProbeTenant,
    *,
    run_id: uuid.UUID,
    attempt_id: uuid.UUID | None = None,
    member: ProbeMember | None = None,
    reserved_tokens: int = 16,
) -> ModelCapacityReservationRequest:
    """主调用（Run）侧的真实预占请求：与 TASK-025 池化链路同一冻结身份口径。"""
    member = member or tenant.member
    return ModelCapacityReservationRequest(
        call_attempt_id=attempt_id or uuid.uuid4(),
        model_id=tenant.model_id,
        deployment_id=member.deployment_id,
        credential_id=member.credential_id,
        credential_revision=1,
        key_resource_id=member.key_resource_id(tenant.tenant_id),
        quota_group_id=member.quota_group_id,
        owner=RunCallOwner(run_id=run_id),
        workload="INTERACTIVE",
        deadline_at=datetime.now(UTC) + timedelta(seconds=30),
        reserved_tokens=reserved_tokens,
    )


# --------------------------------------------------------------------------------------
# 预占：受信身份、真实身份解析、与主调用合并计量
# --------------------------------------------------------------------------------------


async def test_b227_reserve_grants_trusted_probe_and_shares_physical_key_with_run_capacity(
    probe_client: httpx.AsyncClient,
    seed_probe_tenant: Callable[..., Awaitable[ProbeTenant]],
) -> None:
    """[B-227] 受信探针预占：真实 Key/组行占位；主调用在同一账号额度上被回压。"""
    tenant = await seed_probe_tenant(key_max_inflight=2, group_max_inflight=1)
    member = tenant.member
    body = _reserve_body(tenant)
    response = await _reserve(probe_client, tenant, body)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["code"] == "0"
    grant = payload["data"]
    assert set(grant) == {"reservation_id", "epoch", "lease_until"}
    assert grant["epoch"] == 1
    assert datetime.now(UTC) < datetime.fromisoformat(grant["lease_until"]) <= _deadline_of(body)
    # 密钥与内部计量身份绝不进入响应
    assert member.api_key not in response.text
    for internal in ("api_key", "key_resource_id", "quota_group_id", "provider_key", "base_url"):
        assert internal not in response.text

    # 真实租约行：PROBE 归属、稳定计量身份、冻结成员与版本
    attempt_id = uuid.UUID(body["attempt_id"])
    lease = await _lease_row(tenant.tenant_id, attempt_id)
    assert str(lease["id"]) == grant["reservation_id"]
    assert lease["workload"] == "PROBE"
    assert str(lease["probe_id"]) == body["probe_id"]
    assert lease["run_id"] is None and lease["memory_job_id"] is None
    assert lease["state"] == "ACTIVE" and lease["lease_owner"] == "console-probe"
    assert uuid.UUID(str(lease["key_resource_id"])) == member.key_resource_id(tenant.tenant_id)
    assert uuid.UUID(str(lease["credential_id"])) == member.credential_id
    assert uuid.UUID(str(lease["deployment_id"])) == member.deployment_id
    assert uuid.UUID(str(lease["quota_group_id"])) == tenant.quota_group_id
    assert lease["credential_revision"] == 1
    assert lease["reserved_tokens"] == DEFAULT_RESERVED_TOKENS

    # 真实容量行：探针占的是该物理 Key 与共享额度组的同一行
    assert await _counts(tenant, member) == (
        (1, 1, DEFAULT_RESERVED_TOKENS),
        (1, 1, DEFAULT_RESERVED_TOKENS),
    )

    # 主调用（真实 ModelCapacityService，TASK-025 的预占路径）在同一组额度上被回压
    service = ModelCapacityService(get_session_factory)
    run_id = await _seed_run(tenant.tenant_id)
    run_request = _run_request(tenant, run_id=run_id)
    with pytest.raises(Exception) as blocked:
        await service.reserve(tenant_id=tenant.tenant_id, lease_owner="rt-b227", request=run_request)
    assert getattr(blocked.value, "code", None) == "MODEL_CAPACITY_UNAVAILABLE"
    assert await _counts(tenant, member) == (
        (1, 1, DEFAULT_RESERVED_TOKENS),
        (1, 1, DEFAULT_RESERVED_TOKENS),
    )

    # 探针按"确定未发送"结算后释放共享额度：主调用立即可预占
    settled = await _settle(
        probe_client,
        tenant,
        grant["reservation_id"],
        {"epoch": grant["epoch"], "outcome": "NOT_SENT"},
    )
    assert settled.status_code == 200, settled.text
    assert settled.json()["data"] == {
        "reservation_id": grant["reservation_id"],
        "state": "SETTLED",
        "released": True,
    }
    run_reservation = await service.reserve(
        tenant_id=tenant.tenant_id, lease_owner="rt-b227", request=run_request
    )
    assert run_reservation.state == "ACTIVE"
    # NOT_SENT 把探针的请求数也回收了；主调用占位后两行各为 1 次请求
    assert await _counts(tenant, member) == (
        (1, 1, 16),
        (1, 1, 16),
    )


async def test_b227_reserve_replay_is_idempotent_and_attempt_identity_is_fenced(
    probe_client: httpx.AsyncClient,
    seed_probe_tenant: Callable[..., Awaitable[ProbeTenant]],
) -> None:
    """[B-227] 同 attempt 重放复用原租约（不重复占位）；换冻结身份明确拒绝。"""
    tenant = await seed_probe_tenant(key_max_inflight=2, member_count=2)
    first_member, second_member = tenant.members
    body = _reserve_body(tenant, member=first_member)
    first = await _granted(probe_client, tenant, body)
    replay = await _reserve(probe_client, tenant, body)
    assert replay.status_code == 200, replay.text
    assert replay.json()["data"] == first
    assert await _counts(tenant, first_member) == ((1, 1, DEFAULT_RESERVED_TOKENS), None)
    assert await _capacity_row_count(tenant.tenant_id) == 1

    # 同 attempt 换凭据 → 冻结身份不一致，执行方不能继续提交
    mismatch = await _reserve(
        probe_client, tenant, {**body, "credential_id": str(second_member.credential_id)}
    )
    assert mismatch.status_code == 409, mismatch.text
    assert mismatch.json()["code"] == "MODEL_CALL_LEASE_LOST"
    lease = await _lease_row(tenant.tenant_id, uuid.UUID(body["attempt_id"]))
    assert uuid.UUID(str(lease["credential_id"])) == first_member.credential_id
    assert await _capacity_row_count(tenant.tenant_id) == 1
    assert await _lease_count(tenant.tenant_id) == 1

    # 新 attempt 在另一名成员上正常预占（各自独立计量行）
    other = await _granted(probe_client, tenant, _reserve_body(tenant, member=second_member))
    assert other["reservation_id"] != first["reservation_id"]
    assert await _capacity_row_count(tenant.tenant_id) == 2
    assert await _counts(tenant, second_member) == ((1, 1, DEFAULT_RESERVED_TOKENS), None)


async def test_b227_rejects_missing_or_wrong_service_identity_before_any_write(
    probe_client: httpx.AsyncClient,
    seed_probe_tenant: Callable[..., Awaitable[ProbeTenant]],
) -> None:
    """[B-227] 服务身份缺失/错误一律拒绝，且在任何读写之前生效。"""
    tenant = await seed_probe_tenant()
    body = _reserve_body(tenant)
    for headers in ({}, {"X-Internal-Service": "wrong-token"}, {"X-Internal-Service": ""}):
        response = await probe_client.post(
            PROBE_RESERVATIONS, headers={**headers, "X-Tenant-Id": tenant.tenant_id}, json=body
        )
        assert response.status_code == 403, response.text
        assert response.json()["code"] == "FORBIDDEN"
    # renew/settle 同门控：在解析 reservation 之前就拒绝（随机 id 也返回 403 而不是 404）
    reservation_id = uuid.uuid4()
    for suffix, payload in (
        ("renew", {"epoch": 1, "deadline_at": _deadline_of(body).isoformat()}),
        ("settle", {"epoch": 1, "outcome": "NOT_SENT"}),
    ):
        response = await probe_client.post(
            f"{PROBE_RESERVATIONS}/{reservation_id}/{suffix}",
            headers={"X-Tenant-Id": tenant.tenant_id},
            json=payload,
        )
        assert response.status_code == 403, response.text
        assert response.json()["code"] == "FORBIDDEN"
    assert await _lease_count(tenant.tenant_id) == 0
    assert await _capacity_row_count(tenant.tenant_id) == 0

    # 正确身份放行（对照）
    granted = await _reserve(probe_client, tenant, body)
    assert granted.status_code == 200, granted.text


async def test_b227_reserve_rejects_unknown_member_and_invalid_deadline(
    probe_client: httpx.AsyncClient,
    seed_probe_tenant: Callable[..., Awaitable[ProbeTenant]],
) -> None:
    """[B-227] 未绑定成员/未知模型/已过期限/停用凭据分别明确拒绝且不建占位。"""
    tenant = await seed_probe_tenant()
    headers = _auth_headers(tenant)

    unknown_member = await probe_client.post(
        PROBE_RESERVATIONS,
        headers=headers,
        json={**_reserve_body(tenant), "credential_id": str(uuid.uuid4())},
    )
    assert unknown_member.status_code == 404, unknown_member.text
    assert unknown_member.json()["code"] == "COMMON_NOT_FOUND"

    unknown_model = await probe_client.post(
        PROBE_RESERVATIONS,
        headers=headers,
        json={**_reserve_body(tenant), "model_id": str(uuid.uuid4())},
    )
    assert unknown_model.status_code == 404, unknown_model.text
    assert unknown_model.json()["code"] == "COMMON_NOT_FOUND"

    expired = await _reserve(probe_client, tenant, _reserve_body(tenant, deadline_in_sec=-1))
    assert expired.status_code == 504, expired.text
    assert expired.json()["code"] == "MODEL_WAIT_TIMEOUT"

    disabled = await seed_probe_tenant(credential_enabled=False)
    disabled_response = await _reserve(probe_client, disabled, _reserve_body(disabled))
    assert disabled_response.status_code == 409, disabled_response.text
    assert disabled_response.json()["code"] == "MODEL_DISABLED"

    assert await _lease_count(tenant.tenant_id) == 0
    assert await _capacity_row_count(tenant.tenant_id) == 0
    assert await _lease_count(disabled.tenant_id) == 0
    assert await _capacity_row_count(disabled.tenant_id) == 0


# --------------------------------------------------------------------------------------
# 续租：epoch 栅栏与冻结期限
# --------------------------------------------------------------------------------------


async def test_b227_renew_verifies_epoch_and_frozen_deadline(
    probe_client: httpx.AsyncClient,
    seed_probe_tenant: Callable[..., Awaitable[ProbeTenant]],
) -> None:
    """[B-227] 续租核 epoch、只前进不倒退且不越冻结期限；失约即停手。"""
    tenant = await seed_probe_tenant(key_max_inflight=2)
    member = tenant.member
    body = _reserve_body(tenant, deadline_in_sec=180.0)
    grant = await _granted(probe_client, tenant, body)
    reservation_id = grant["reservation_id"]

    renewal = await _renew(
        probe_client,
        tenant,
        reservation_id,
        {"epoch": grant["epoch"], "deadline_at": body["deadline_at"]},
    )
    assert renewal.status_code == 200, renewal.text
    renewed = renewal.json()["data"]
    assert set(renewed) == {"reservation_id", "epoch", "lease_until"}
    assert renewed["reservation_id"] == reservation_id and renewed["epoch"] == grant["epoch"]
    assert (
        datetime.fromisoformat(grant["lease_until"])
        < datetime.fromisoformat(renewed["lease_until"])
        <= _deadline_of(body)
    )
    lease = await _lease_row(tenant.tenant_id, uuid.UUID(body["attempt_id"]))
    assert lease["state"] == "ACTIVE"
    assert lease["lease_until"] == datetime.fromisoformat(renewed["lease_until"])
    assert await _counts(tenant, member) == ((1, 1, DEFAULT_RESERVED_TOKENS), None)

    # epoch 不符 → 失约停手，期限不变
    stale = await _renew(
        probe_client,
        tenant,
        reservation_id,
        {"epoch": grant["epoch"] + 1, "deadline_at": body["deadline_at"]},
    )
    assert stale.status_code == 409, stale.text
    assert stale.json()["code"] == "MODEL_CALL_LEASE_LOST"
    assert (await _lease_row(tenant.tenant_id, uuid.UUID(body["attempt_id"])))["lease_until"] == (
        datetime.fromisoformat(renewed["lease_until"])
    )

    # 绝对期限已过 → 不再续租（调用必须停手），期限不变
    expired = await _renew(
        probe_client,
        tenant,
        reservation_id,
        {"epoch": grant["epoch"], "deadline_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat()},
    )
    assert expired.status_code == 504, expired.text
    assert expired.json()["code"] == "MODEL_WAIT_TIMEOUT"
    assert (await _lease_row(tenant.tenant_id, uuid.UUID(body["attempt_id"])))["lease_until"] == (
        datetime.fromisoformat(renewed["lease_until"])
    )

    # 结算后不得续租
    settled = await _settle(
        probe_client, tenant, reservation_id, {"epoch": grant["epoch"], "outcome": "NOT_SENT"}
    )
    assert settled.status_code == 200, settled.text
    after_settle = await _renew(
        probe_client,
        tenant,
        reservation_id,
        {"epoch": grant["epoch"], "deadline_at": body["deadline_at"]},
    )
    assert after_settle.status_code == 409, after_settle.text
    assert after_settle.json()["code"] == "MODEL_CALL_LEASE_LOST"


# --------------------------------------------------------------------------------------
# 结算：只有"确定未发送"/完成证据才释放；幂等；UNKNOWN 保留
# --------------------------------------------------------------------------------------


async def test_b227_settle_releases_only_with_evidence_and_is_idempotent(
    probe_client: httpx.AsyncClient,
    seed_probe_tenant: Callable[..., Awaitable[ProbeTenant]],
) -> None:
    """[B-227] 无证据失败保留 UNKNOWN 占用；有证据才释放；重复结算不二次扣减。"""
    tenant = await seed_probe_tenant(key_max_inflight=1, group_max_inflight=1)
    member = tenant.member
    body = _reserve_body(tenant)
    grant = await _granted(probe_client, tenant, body)
    reservation_id = grant["reservation_id"]
    attempt_id = uuid.UUID(body["attempt_id"])

    # 失败但无完成证据 → UNKNOWN 保留占用（失败不假定供方未计费）
    unknown = await _settle(
        probe_client, tenant, reservation_id, {"epoch": grant["epoch"], "outcome": "FAILED"}
    )
    assert unknown.status_code == 200, unknown.text
    assert unknown.json()["data"] == {
        "reservation_id": reservation_id,
        "state": "UNKNOWN",
        "released": False,
    }
    assert (await _lease_row(tenant.tenant_id, attempt_id))["state"] == "UNKNOWN"
    assert await _counts(tenant, member) == (
        (1, 1, DEFAULT_RESERVED_TOKENS),
        (1, 1, DEFAULT_RESERVED_TOKENS),
    )

    # UNKNOWN 上声称"确定未发送"是自相矛盾，明确拒绝
    unsafe = await _settle(
        probe_client, tenant, reservation_id, {"epoch": grant["epoch"], "outcome": "NOT_SENT"}
    )
    assert unsafe.status_code == 409, unsafe.text
    assert unsafe.json()["code"] == "MODEL_CALL_OUTCOME_UNKNOWN"
    assert (await _lease_row(tenant.tenant_id, attempt_id))["state"] == "UNKNOWN"

    # 有服务端硬超时证据才释放：在飞释放、无 usage 保守保留预占
    evidenced = await _settle(
        probe_client,
        tenant,
        reservation_id,
        {
            "epoch": grant["epoch"],
            "outcome": "FAILED",
            "remote_completion": _proof("SERVER_HARD_TIMEOUT", "probe-b227-timeout"),
        },
    )
    assert evidenced.status_code == 200, evidenced.text
    assert evidenced.json()["data"] == {
        "reservation_id": reservation_id,
        "state": "SETTLED",
        "released": True,
    }
    assert await _counts(tenant, member) == (
        (0, 1, DEFAULT_RESERVED_TOKENS),
        (0, 1, DEFAULT_RESERVED_TOKENS),
    )

    # 结算幂等：重复结算只回放终态，不再二次扣减；epoch 仍须正确
    repeat = await _settle(
        probe_client, tenant, reservation_id, {"epoch": grant["epoch"], "outcome": "NOT_SENT"}
    )
    assert repeat.status_code == 200, repeat.text
    assert repeat.json()["data"] == {
        "reservation_id": reservation_id,
        "state": "SETTLED",
        "released": False,
    }
    wrong_epoch = await _settle(
        probe_client,
        tenant,
        reservation_id,
        {"epoch": grant["epoch"] + 1, "outcome": "NOT_SENT"},
    )
    assert wrong_epoch.status_code == 409, wrong_epoch.text
    assert wrong_epoch.json()["code"] == "MODEL_CALL_LEASE_LOST"
    assert await _counts(tenant, member) == (
        (0, 1, DEFAULT_RESERVED_TOKENS),
        (0, 1, DEFAULT_RESERVED_TOKENS),
    )

    # 成功按实际 usage 释放多余预占（请求数保留：供方确实被调用过）
    second = await _granted(probe_client, tenant, _reserve_body(tenant))
    success = await _settle(
        probe_client,
        tenant,
        second["reservation_id"],
        {
            "epoch": second["epoch"],
            "outcome": "SUCCEEDED",
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "remote_completion": _proof("PROVIDER_CONFIRMED", "probe-b227-ok"),
        },
    )
    assert success.status_code == 200, success.text
    assert success.json()["data"] == {
        "reservation_id": second["reservation_id"],
        "state": "SETTLED",
        "released": True,
    }
    # 第一次尝试无 usage，保守预占 64 留在窗口；第二次尝试按实际 usage 15 替换自己的 64
    assert await _counts(tenant, member) == (
        (0, 2, 64 + 15),
        (0, 2, 64 + 15),
    )


async def test_b227_wire_shape_is_exact_and_settlement_evidence_is_checked(
    probe_client: httpx.AsyncClient,
    seed_probe_tenant: Callable[..., Awaitable[ProbeTenant]],
) -> None:
    """[B-227] 线协议精确：未知字段/缺字段/非正预占/无时区期限一律 422，不落任何占位。"""
    tenant = await seed_probe_tenant()
    headers = _auth_headers(tenant)
    good = _reserve_body(tenant)
    cases = [
        {key: value for key, value in good.items() if key != "attempt_id"},
        {**good, "reserved_tokens": 0},
        {**good, "deadline_at": good["deadline_at"].replace("+00:00", "")},
        {**good, "run_id": str(uuid.uuid4())},
    ]
    for payload in cases:
        response = await probe_client.post(PROBE_RESERVATIONS, headers=headers, json=payload)
        assert response.status_code == 422, response.text
        assert response.json()["code"] == "COMMON_VALIDATION_ERROR"
    assert await _lease_count(tenant.tenant_id) == 0
    assert await _capacity_row_count(tenant.tenant_id) == 0

    # 结算形状：UNKNOWN 不得声称远端已确认完成（与主链同一结算口径）
    grant = await _granted(probe_client, tenant, good)
    bad_settle = await _settle(
        probe_client,
        tenant,
        grant["reservation_id"],
        {
            "epoch": grant["epoch"],
            "outcome": "UNKNOWN",
            "remote_completion": _proof("PROVIDER_CONFIRMED", "probe-b227-shape"),
        },
    )
    assert bad_settle.status_code == 422, bad_settle.text
    assert bad_settle.json()["code"] == "COMMON_VALIDATION_ERROR"
    assert (await _lease_row(tenant.tenant_id, uuid.UUID(good["attempt_id"])))["state"] == "ACTIVE"


async def test_b227_probe_port_never_acts_on_run_leases_and_writes_no_billing_audit(
    probe_client: httpx.AsyncClient,
    seed_probe_tenant: Callable[..., Awaitable[ProbeTenant]],
) -> None:
    """[B-227] 探针端口不触碰 Run 租约，也不写模型计费审计。"""
    tenant = await seed_probe_tenant(key_max_inflight=2)
    member = tenant.member
    service = ModelCapacityService(get_session_factory)
    run_id = await _seed_run(tenant.tenant_id)
    run_request = _run_request(tenant, run_id=run_id)
    run_reservation = await service.reserve(
        tenant_id=tenant.tenant_id, lease_owner="rt-b227", request=run_request
    )

    for suffix, payload in (
        (
            "renew",
            {
                "epoch": run_reservation.epoch,
                "deadline_at": _deadline_of(_reserve_body(tenant)).isoformat(),
            },
        ),
        ("settle", {"epoch": run_reservation.epoch, "outcome": "NOT_SENT"}),
    ):
        response = await probe_client.post(
            f"{PROBE_RESERVATIONS}/{run_reservation.reservation_id}/{suffix}",
            headers=_auth_headers(tenant),
            json=payload,
        )
        assert response.status_code == 404, response.text
        assert response.json()["code"] == "COMMON_NOT_FOUND"

    run_lease = await _lease_by_reservation(tenant.tenant_id, run_reservation.reservation_id)
    assert run_lease["state"] == "ACTIVE" and run_lease["probe_id"] is None
    assert run_lease["workload"] == "INTERACTIVE"
    assert uuid.UUID(str(run_lease["run_id"])) == run_id
    assert await _counts(tenant, member) == ((1, 1, 16), None)

    # 未知 reservation 同样按"不存在"拒绝
    unknown = await _settle(
        probe_client, tenant, str(uuid.uuid4()), {"epoch": 1, "outcome": "NOT_SENT"}
    )
    assert unknown.status_code == 404, unknown.text
    assert unknown.json()["code"] == "COMMON_NOT_FOUND"

    # 完整探针链路（reserve → settle）不写模型计费审计
    body = _reserve_body(tenant)
    grant = await _granted(probe_client, tenant, body)
    settled = await _settle(
        probe_client, tenant, grant["reservation_id"], {"epoch": grant["epoch"], "outcome": "NOT_SENT"}
    )
    assert settled.status_code == 200, settled.text
    assert await _audit_count(tenant.tenant_id) == 0
    assert await _counts(tenant, member) == ((1, 1, 16), None)
