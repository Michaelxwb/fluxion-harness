"""B-224：冻结模型池语义与非密钥执行策略（真实 PG + 真实 Runtime 创建/续跑路径）。

真实边界（不 mock 存储、不 mock 冻结路径）：
- **真实 PostgreSQL 行**：`control.platform_setting`（经真实 Console `PlatformSettingsService`
  读写）、`control.model_definition` / `model_credential` / `model_deployment`（成员引用与权重）、
  `runtime.runtime_snapshot`（冻结行逐列回读）、`runtime.run_record`（绝对期限）；
- **真实 Runtime 装配**：Run 经真实 `RunService.start` 创建、经真实 `RunService.resume` 续跑；
  `_create_run` 在创建边界取一次设置快照并把模型池语义冻进 `policy_json`；
- 模型调用用 no-op 执行器替身（线级发送与预占接线属 TASK-025/E-04）。

关键断言（Acceptance Contract B-224）：
- 新 Run 冻结 model（版本/型号/输出预算）、member（非密钥引用与权重）、路由（调度策略与
  `policy_hash`）、等待（模型等待预算）与完整 memory 设置；
- Key 明文、去重指纹、由其派生的计量身份（`key_resource_id`）与启动资源预算都不进快照；
- 配置/成员/定义变更只影响后续新 Run；旧 resume 不补新默认、不换型号、不延长原 deadline。
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Mapping
from dataclasses import asdict, dataclass, replace
from datetime import timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from muad_agent_runtime.application.async_tools.supervisor import ExecutionSupervisor
from muad_agent_runtime.application.context_settings import (
    PoolExecutionPolicy,
    pool_execution_policy_of,
    resolve_pool_execution_policy,
)
from muad_agent_runtime.application.executor import ExecutorEvent, ExecutorRequest, RunExecutor
from muad_agent_runtime.application.model_allocator import dispatch_policy_hash
from muad_agent_runtime.application.ports import PlatformSettingsSnapshot
from muad_agent_runtime.application.run_service import (
    RunService,
    frozen_model_members_of,
    snapshot_policy,
)
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.model_dispatch_store import MemberRef
from muad_agent_runtime.infrastructure.models.runtime import (
    RunInterrupt,
    RunRecord,
    RuntimeSnapshot,
)
from muad_common import SharedSettings
from muad_console_platform.application.audit_service import AuditActor
from muad_console_platform.application.model_credential_service import derive_key_resource_id
from muad_console_platform.application.platform_settings_service import PlatformSettingsService
from muad_contracts import (
    ChannelContext,
    MessageInput,
    ResolveDefinitionResponse,
    RunRequest,
    RunStatus,
)
from muad_contracts.platform_settings import (
    AgentSettings,
    CapacitySettings,
    MemoryPolicySettings,
    ModelPoolSettings,
    PlatformSettingsError,
)

from agent_runtime.conftest import FakeResolveClient, TenantContext

pytestmark = pytest.mark.integration

MODEL_NAME = "gpt-4o-mini"
MODEL_BASE_URL = "https://api.example.com/v1"
MODEL_REVISION = 7
MODEL_PARAMS: dict[str, Any] = {"max_tokens": 4096, "temperature": 0.2}
DEFAULT_DEADLINE_MS = AgentSettings().deadline_ms
#: 刻意都不等于 schema 默认，改动才可见
POOL_OVERRIDE = ModelPoolSettings(
    dispatch_epoch_sec=120,
    input_estimate_multiplier=2.0,
    interactive_weight=5,
    background_weight=3,
    aging_interval_sec=45,
)
CAPACITY_OVERRIDE = CapacitySettings(model_wait_timeout_sec=17)
MEMORY_OVERRIDE = MemoryPolicySettings(write_enabled=False, max_injected_memories=2)
MEMBER_KEYS = {"deployment_id", "credential_id", "weight"}


# --------------------------------------------------------------------------- #
# 真实控制面行：逻辑模型 / 凭据 / 成员（含权重）
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Member:
    """一名真实成员：成员行 + 同租户凭据行（明文/指纹/计量身份只用于排除项断言）。"""

    deployment_id: uuid.UUID
    credential_id: uuid.UUID
    weight: int
    key_resource_id: uuid.UUID
    api_key: str
    digest: str

    def ref(self) -> MemberRef:
        return MemberRef(
            deployment_id=self.deployment_id, credential_id=self.credential_id, weight=self.weight
        )


async def _seed_model(tenant_id: str) -> uuid.UUID:
    """一条真实逻辑模型定义：只作成员/凭据的 FK 目标（执行入口只剩成员凭据）。"""
    model_id = uuid.uuid4()
    async with get_session_factory()() as session:
        await session.execute(
            sa.text(
                "INSERT INTO control.model_definition (id, tenant_id, key, name, model_id, provider_key)"
                " VALUES (:id, :tenant_id, :key, :name, :provider_model, 'openai')"
            ).bindparams(
                id=model_id,
                tenant_id=tenant_id,
                key=f"model-{uuid.uuid4().hex[:12]}",
                name="pool-model",
                provider_model=MODEL_NAME,
            )
        )
        await session.commit()
    return model_id


async def _seed_member(
    tenant_id: str, model_id: uuid.UUID, *, member_key: str, weight: int
) -> Member:
    credential_id, deployment_id = uuid.uuid4(), uuid.uuid4()
    api_key, digest = f"sk-canary-{uuid.uuid4().hex}", uuid.uuid4().hex + uuid.uuid4().hex
    async with get_session_factory()() as session:
        await session.execute(
            sa.text(
                "INSERT INTO control.model_credential (id, tenant_id, key, provider_key, base_url,"
                " api_key, api_key_digest, max_inflight, enabled, revision)"
                " VALUES (:id, :tenant_id, :key, 'openai', :base_url, :api_key, :digest, 5, true, 1)"
            ).bindparams(
                id=credential_id,
                tenant_id=tenant_id,
                key=f"key-{member_key}-{uuid.uuid4().hex[:8]}",
                base_url=MODEL_BASE_URL,
                api_key=api_key,
                digest=digest,
            )
        )
        await session.execute(
            sa.text(
                "INSERT INTO control.model_deployment (id, tenant_id, key, model_id, credential_id,"
                " weight, enabled) VALUES (:id, :tenant_id, :key, :model_id, :credential_id, :weight, true)"
            ).bindparams(
                id=deployment_id,
                tenant_id=tenant_id,
                key=f"member-{member_key}",
                model_id=model_id,
                credential_id=credential_id,
                weight=weight,
            )
        )
        await session.commit()
    return Member(
        deployment_id=deployment_id,
        credential_id=credential_id,
        weight=weight,
        key_resource_id=derive_key_resource_id(tenant_id, "openai", digest),
        api_key=api_key,
        digest=digest,
    )


async def _set_member_weight(deployment_id: uuid.UUID, weight: int) -> None:
    async with get_session_factory()() as session:
        await session.execute(
            sa.text(
                "UPDATE control.model_deployment SET weight = :weight, revision = revision + 1"
                " WHERE id = :id"
            ).bindparams(id=deployment_id, weight=weight)
        )
        await session.commit()


async def _live_member_rows(tenant_id: str, model_id: uuid.UUID) -> list[dict[str, Any]]:
    """控制面当前的成员事实：证明冻结值不是"库没变"，而是来源确实变了。"""
    async with get_session_factory()() as session:
        rows = (
            await session.execute(
                sa.text(
                    "SELECT id, credential_id, weight FROM control.model_deployment"
                    " WHERE tenant_id = :tenant_id AND model_id = :model_id AND is_deleted = false"
                    " ORDER BY key, id"
                ).bindparams(tenant_id=tenant_id, model_id=model_id)
            )
        ).mappings().all()
    return [dict(row) for row in rows]


@pytest.fixture
async def control_tenant(tenant: TenantContext) -> AsyncIterator[TenantContext]:
    """本套件写入的控制面行（设置/模型/凭据/成员）在用例后按 FK 顺序清理。"""
    yield tenant
    async with get_session_factory()() as session:
        for statement in (
            "DELETE FROM control.model_deployment WHERE tenant_id = :tenant_id",
            "DELETE FROM control.model_credential WHERE tenant_id = :tenant_id",
            "DELETE FROM control.model_definition WHERE tenant_id = :tenant_id",
            "DELETE FROM control.platform_setting WHERE tenant_id = :tenant_id",
        ):
            await session.execute(sa.text(statement), {"tenant_id": tenant.tenant_id})
        await session.commit()


@pytest.fixture
async def supervisor() -> AsyncIterator[ExecutionSupervisor]:
    instance = ExecutionSupervisor()
    try:
        yield instance
    finally:
        await instance.close()


# --------------------------------------------------------------------------- #
# 平台设置真源（真实 PG → 真实 Console 服务）与真实 Run 驱动
# --------------------------------------------------------------------------- #


class _PgSettingsClient:
    """Runtime 的 `PlatformSettingsClient` 端口实现：直读真实 PG（端点同一条产码路径）。"""

    async def fetch_snapshot(self, *, tenant_id: str, trace_id: str = "") -> PlatformSettingsSnapshot:
        async with get_session_factory()() as session:
            current = await PlatformSettingsService(session).read_current(tenant_id)
            return PlatformSettingsSnapshot(revision=current.revision, settings=asdict(current.settings))


async def _save_settings(tenant_id: str, **groups: Any) -> None:
    async with get_session_factory()() as session:
        service = PlatformSettingsService(session)
        current = await service.read_current(tenant_id)
        await service.save(
            tenant_id,
            AuditActor(account_id=uuid.uuid4()),
            current.revision,
            replace(current.settings, **groups),
        )
        await session.commit()


class _DrainingExecutor:
    async def run(self) -> AsyncIterator[ExecutorEvent]:
        yield ExecutorEvent(type="message.delta", data={"delta": "ok"})


class _CapturingExecutorFactory:
    """替身只替换模型调用这一环：`_create_run` / `_build_executor` 仍是真实代码路径。"""

    def __init__(self) -> None:
        self.requests: list[ExecutorRequest] = []

    async def __call__(self, request: ExecutorRequest) -> RunExecutor:
        self.requests.append(request)
        return _DrainingExecutor()


def _resolved(resolved: ResolveDefinitionResponse, definition_id: uuid.UUID, **update: Any) -> Any:
    model = resolved.model.model_copy(
        update={
            "id": definition_id,
            "revision": MODEL_REVISION,
            "model_id": MODEL_NAME,
            "base_url": MODEL_BASE_URL,
            "params": MODEL_PARAMS,
            **update,
        }
    )
    return resolved.model_copy(update={"model": model})


def _request(tenant: TenantContext, text: str) -> RunRequest:
    return RunRequest(
        agent_id=tenant.agent_id,
        platform_user_id=tenant.platform_user_id,
        channel=ChannelContext(type="WECOM", bot_id="bot-1"),
        message=MessageInput(id=f"msg-{uuid.uuid4()}", text=text),
    )


async def _start_run(
    tenant: TenantContext,
    fake_resolve: FakeResolveClient,
    capture: _CapturingExecutorFactory,
    supervisor: ExecutionSupervisor,
    text: str = "pool snapshot",
) -> uuid.UUID:
    async with get_session_factory()() as session:
        started = await RunService(
            session,
            fake_resolve,
            "instance-b224",
            _PgSettingsClient(),
            executor_factory=capture,
            supervisor=supervisor,
        ).start(_request(tenant, text), tenant.tenant_id)
        async for _ in started.events:
            pass
        return started.run_id


async def _resume_run(
    tenant: TenantContext,
    run_id: uuid.UUID,
    fake_resolve: FakeResolveClient,
    capture: _CapturingExecutorFactory,
    supervisor: ExecutionSupervisor,
) -> None:
    async with get_session_factory()() as session:
        started = await RunService(
            session,
            fake_resolve,
            "instance-b224",
            _PgSettingsClient(),
            executor_factory=capture,
            supervisor=supervisor,
        ).resume(
            run_id,
            "补充一句新内容",
            tenant.tenant_id,
            platform_user_id=tenant.platform_user_id,
            input_id=uuid.uuid4().hex,
        )
        assert started.resumed
        async for _ in started.events:
            pass


async def _snapshot_row(run_id: uuid.UUID) -> dict[str, Any]:
    """Run 的冻结 Snapshot 行（全部列，用于逐列前后对照与排除项扫描）。"""
    async with get_session_factory()() as session:
        run = await session.get(RunRecord, run_id)
        assert run is not None and run.snapshot_id is not None
        snapshot = await session.get(RuntimeSnapshot, run.snapshot_id)
        assert snapshot is not None and snapshot.run_id == run_id
        return {
            "id": snapshot.id,
            "run_id": snapshot.run_id,
            "schema_version": snapshot.schema_version,
            "agent_revision": snapshot.agent_revision,
            "model_revision": snapshot.model_revision,
            "agent_json": snapshot.agent_json,
            "model_json": snapshot.model_json,
            "skill_catalog_json": snapshot.skill_catalog_json,
            "mcp_catalog_json": snapshot.mcp_catalog_json,
            "policy_json": snapshot.policy_json,
            "prompt_template_version": snapshot.prompt_template_version,
            "content_hash": snapshot.content_hash,
        }


async def _run_row(run_id: uuid.UUID) -> dict[str, Any]:
    async with get_session_factory()() as session:
        run = await session.get(RunRecord, run_id)
        assert run is not None
        return {
            "status": run.status,
            "start_time": run.start_time,
            "deadline_at": run.deadline_at,
            "conversation_id": run.conversation_id,
        }


async def _await_input(run_id: uuid.UUID) -> None:
    """把真实路径创建的 Run 置入 `WAITING_INPUT`（并给一条通用确认中断，真实行）。

    记忆确认的生产路径（真实 runner 挂起）已由 memory_confirmation 套件覆盖；本用例的
    验收对象是续跑**读取冻结值**，因此等待状态由夹具直接置入，不伪造任何冻结数据。
    """
    async with get_session_factory()() as session:
        run = await session.get(RunRecord, run_id)
        assert run is not None
        run.status = RunStatus.WAITING_INPUT
        session.add(
            RunInterrupt(
                tenant_id=run.tenant_id,
                run_id=run.id,
                conversation_id=run.conversation_id,
                interrupt_type="CONFIRM",
                prompt_text="continue?",
                options_json=["yes", "no"],
                status="WAITING",
            )
        )
        await session.commit()


# --------------------------------------------------------------------------- #
# B-224 主场景：新 Run 冻结 model / member / 路由 / 输出 / 等待 / memory
# --------------------------------------------------------------------------- #


async def test_b224_new_run_freezes_members_routing_output_wait_and_memory(
    control_tenant: TenantContext,
    resolved: ResolveDefinitionResponse,
    fake_resolve: FakeResolveClient,
    supervisor: ExecutionSupervisor,
) -> None:
    tenant = control_tenant
    model_id = await _seed_model(tenant.tenant_id)
    heavy = await _seed_member(tenant.tenant_id, model_id, member_key="a", weight=5)
    light = await _seed_member(tenant.tenant_id, model_id, member_key="b", weight=1)
    await _save_settings(
        tenant.tenant_id,
        model_pool=POOL_OVERRIDE,
        capacity=CAPACITY_OVERRIDE,
        memory=MEMORY_OVERRIDE,
    )
    fake_resolve.response = _resolved(resolved, model_id)

    row = await _snapshot_row(
        await _start_run(tenant, fake_resolve, _CapturingExecutorFactory(), supervisor)
    )
    policy = row["policy_json"]

    # 路由：模型池策略（非密钥）连同 `dispatch_policy_hash` 的规范身份一起冻结
    assert policy["model_pool"] == {
        "dispatch_epoch_sec": POOL_OVERRIDE.dispatch_epoch_sec,
        "input_estimate_multiplier": POOL_OVERRIDE.input_estimate_multiplier,
        "interactive_weight": POOL_OVERRIDE.interactive_weight,
        "background_weight": POOL_OVERRIDE.background_weight,
        "aging_interval_sec": POOL_OVERRIDE.aging_interval_sec,
        "policy_hash": dispatch_policy_hash(
            dispatch_epoch_sec=POOL_OVERRIDE.dispatch_epoch_sec,
            interactive_weight=POOL_OVERRIDE.interactive_weight,
            background_weight=POOL_OVERRIDE.background_weight,
            aging_interval_sec=POOL_OVERRIDE.aging_interval_sec,
        ),
    }
    assert len(policy["model_pool"]["policy_hash"]) == 64  # ≤128 列；唯一口径的自洽身份
    # 等待：Run 创建时冻结模型等待预算
    assert policy["capacity"] == {"model_wait_timeout_sec": CAPACITY_OVERRIDE.model_wait_timeout_sec}
    # member：非密钥引用 + 权重，按稳定序冻结；凭据版本/计量身份不在其中
    assert policy["model_members"] == [
        {
            "deployment_id": str(heavy.deployment_id),
            "credential_id": str(heavy.credential_id),
            "weight": heavy.weight,
        },
        {
            "deployment_id": str(light.deployment_id),
            "credential_id": str(light.credential_id),
            "weight": light.weight,
        },
    ]
    assert all(set(entry) == MEMBER_KEYS for entry in policy["model_members"])
    # memory：完整设置分组（每叶都在），不是部分投影
    assert policy["memory"] == asdict(MEMORY_OVERRIDE)
    assert set(policy["memory"]) == set(asdict(MemoryPolicySettings()))
    # 预算仍由 policy_json 承载（RULE-snapshot-001 的 Run 侧等价载体）
    assert policy["max_model_retries"] == AgentSettings().max_model_retries
    # 输出：真实发送的输出预算与型号/版本冻结在 model_json（密钥已剥离）
    assert row["model_revision"] == MODEL_REVISION
    assert row["model_json"]["model_id"] == MODEL_NAME
    assert row["model_json"]["params"] == MODEL_PARAMS
    assert "api_key" not in row["model_json"]

    # 冻结值被执行期读回（TASK-025/027 的消费口径）：冻结即"可解释"
    frozen = pool_execution_policy_of(policy)
    assert frozen == PoolExecutionPolicy(
        dispatch_epoch_sec=POOL_OVERRIDE.dispatch_epoch_sec,
        input_estimate_multiplier=POOL_OVERRIDE.input_estimate_multiplier,
        interactive_weight=POOL_OVERRIDE.interactive_weight,
        background_weight=POOL_OVERRIDE.background_weight,
        aging_interval_sec=POOL_OVERRIDE.aging_interval_sec,
        model_wait_timeout_sec=CAPACITY_OVERRIDE.model_wait_timeout_sec,
    )
    assert frozen is not None and frozen.policy_hash == policy["model_pool"]["policy_hash"]
    assert frozen_model_members_of(policy) == (heavy.ref(), light.ref())


async def test_b224_settings_member_and_model_changes_only_affect_new_runs(
    control_tenant: TenantContext,
    resolved: ResolveDefinitionResponse,
    fake_resolve: FakeResolveClient,
    supervisor: ExecutionSupervisor,
) -> None:
    tenant = control_tenant
    model_id = await _seed_model(tenant.tenant_id)
    first_member = await _seed_member(tenant.tenant_id, model_id, member_key="a", weight=5)
    second_member = await _seed_member(tenant.tenant_id, model_id, member_key="b", weight=1)
    await _save_settings(tenant.tenant_id, model_pool=POOL_OVERRIDE, capacity=CAPACITY_OVERRIDE)
    fake_resolve.response = _resolved(resolved, model_id)
    first = await _start_run(tenant, fake_resolve, _CapturingExecutorFactory(), supervisor)
    before = await _snapshot_row(first)

    # 三条腿的真实变更：设置（路由/等待）、成员权重与新增成员、模型定义（版本/参数）
    await _save_settings(
        tenant.tenant_id,
        model_pool=replace(POOL_OVERRIDE, dispatch_epoch_sec=300, aging_interval_sec=15),
        capacity=replace(CAPACITY_OVERRIDE, model_wait_timeout_sec=33),
    )
    await _set_member_weight(first_member.deployment_id, 1)
    third_member = await _seed_member(tenant.tenant_id, model_id, member_key="c", weight=9)
    fake_resolve.response = _resolved(
        resolved, model_id, revision=MODEL_REVISION + 1, params={"max_tokens": 8192}
    )

    # 控制面确实变了（否则"旧行不变"没有证据价值）
    live = await _live_member_rows(tenant.tenant_id, model_id)
    assert [(row["id"], row["weight"]) for row in live] == [
        (first_member.deployment_id, 1),
        (second_member.deployment_id, 1),
        (third_member.deployment_id, 9),
    ]

    second = await _start_run(tenant, fake_resolve, _CapturingExecutorFactory(), supervisor, "after")
    after = await _snapshot_row(second)
    assert after["policy_json"]["model_pool"]["dispatch_epoch_sec"] == 300
    assert after["policy_json"]["model_pool"]["aging_interval_sec"] == 15
    assert after["policy_json"]["capacity"] == {"model_wait_timeout_sec": 33}
    assert [
        (entry["deployment_id"], entry["weight"]) for entry in after["policy_json"]["model_members"]
    ] == [
        (str(first_member.deployment_id), 1),
        (str(second_member.deployment_id), 1),
        (str(third_member.deployment_id), 9),
    ]
    assert after["model_revision"] == MODEL_REVISION + 1
    assert after["model_json"]["params"] == {"max_tokens": 8192}
    assert after["content_hash"] != before["content_hash"]

    # 旧 Run 的冻结行逐列原样保留（含 content_hash）：配置/成员/定义变更只影响后续新 Run
    assert await _snapshot_row(first) == before
    assert frozen_model_members_of(before["policy_json"]) == (
        first_member.ref(),
        second_member.ref(),
    )


async def test_b224_resume_keeps_frozen_pool_model_and_deadline(
    control_tenant: TenantContext,
    resolved: ResolveDefinitionResponse,
    fake_resolve: FakeResolveClient,
    supervisor: ExecutionSupervisor,
) -> None:
    tenant = control_tenant
    model_id = await _seed_model(tenant.tenant_id)
    member = await _seed_member(tenant.tenant_id, model_id, member_key="a", weight=4)
    fake_resolve.response = _resolved(resolved, model_id)
    first = await _start_run(tenant, fake_resolve, _CapturingExecutorFactory(), supervisor)
    before = await _snapshot_row(first)
    run_before = await _run_row(first)
    assert run_before["deadline_at"] - run_before["start_time"] == timedelta(
        milliseconds=DEFAULT_DEADLINE_MS
    )
    await _await_input(first)

    # 等待期间的全部变更：设置（期限/重试/路由/等待）、定义（型号/参数/版本）、成员权重
    await _save_settings(
        tenant.tenant_id,
        model_pool=POOL_OVERRIDE,
        capacity=CAPACITY_OVERRIDE,
        agent=AgentSettings(deadline_ms=300_000, max_model_retries=1),
    )
    fake_resolve.response = _resolved(
        resolved,
        model_id,
        revision=MODEL_REVISION + 1,
        model_id="changed-model",
        base_url="https://api.example.com/v2",
        params={"max_tokens": 8192},
    )
    await _set_member_weight(member.deployment_id, 1)

    capture = _CapturingExecutorFactory()
    await _resume_run(tenant, first, fake_resolve, capture, supervisor)
    assert capture.requests, "续跑必须真的装配执行器"
    request = capture.requests[0]
    # 不更换型号：续跑用的是原快照里的模型定义（版本/型号/参数/连接），不是当前配置
    assert (request.model.id, request.model.revision) == (model_id, MODEL_REVISION)
    assert (request.model.model_id, request.model.base_url) == (MODEL_NAME, MODEL_BASE_URL)
    assert request.model.params == MODEL_PARAMS
    assert request.execution is not None
    assert request.execution.agent_policy.deadline_ms == DEFAULT_DEADLINE_MS
    # 不补新默认、不延长原 deadline
    assert await _snapshot_row(first) == before
    run_after = await _run_row(first)
    assert run_after["deadline_at"] == run_before["deadline_at"]
    assert run_after["deadline_at"] - run_after["start_time"] == timedelta(
        milliseconds=DEFAULT_DEADLINE_MS
    )
    frozen = pool_execution_policy_of(before["policy_json"])
    assert frozen is not None
    assert frozen.dispatch_epoch_sec == ModelPoolSettings().dispatch_epoch_sec
    assert frozen.model_wait_timeout_sec == CapacitySettings().model_wait_timeout_sec

    # 同一份变更对**后续新 Run** 生效
    new_run = await _start_run(tenant, fake_resolve, _CapturingExecutorFactory(), supervisor, "new")
    new_policy = (await _snapshot_row(new_run))["policy_json"]
    assert new_policy["model_pool"]["dispatch_epoch_sec"] == POOL_OVERRIDE.dispatch_epoch_sec
    assert new_policy["capacity"] == {
        "model_wait_timeout_sec": CAPACITY_OVERRIDE.model_wait_timeout_sec
    }
    assert new_policy["max_model_retries"] == 1
    assert new_policy["model_members"] != before["policy_json"]["model_members"]


# --------------------------------------------------------------------------- #
# B-224 排除项：Key / 指纹 / 计量身份 / 启动预算都不进快照
# --------------------------------------------------------------------------- #


def _all_keys(value: Any) -> set[str]:
    """递归收集 JSON 结构里的全部对象键（排除项扫描用）。"""
    if isinstance(value, Mapping):
        return {str(key) for key in value} | {
            nested for item in value.values() for nested in _all_keys(item)
        }
    if isinstance(value, (list, tuple)):
        return {nested for item in value for nested in _all_keys(item)}
    return set()


async def test_b224_snapshot_excludes_key_fingerprint_identity_and_startup_budget(
    control_tenant: TenantContext,
    resolved: ResolveDefinitionResponse,
    fake_resolve: FakeResolveClient,
    supervisor: ExecutionSupervisor,
) -> None:
    tenant = control_tenant
    model_id = await _seed_model(tenant.tenant_id)
    member = await _seed_member(tenant.tenant_id, model_id, member_key="a", weight=3)
    # 执行入口用的就是这条真实凭据的明文：若快照未剥离，canary 会出现在盘面上
    fake_resolve.response = _resolved(resolved, model_id, api_key=member.api_key)

    row = await _snapshot_row(
        await _start_run(tenant, fake_resolve, _CapturingExecutorFactory(), supervisor)
    )
    dump = json.dumps(row, default=str, sort_keys=True)
    assert member.api_key not in dump
    assert member.digest not in dump
    assert str(member.key_resource_id) not in dump

    keys = _all_keys(row)
    assert "api_key" not in keys
    assert "api_key_digest" not in keys
    assert "key_resource_id" not in keys
    assert "credential_revision" not in keys
    # 启动资源预算（SharedSettings 环境项）不随 Run 冻结：任何环境项键都不在快照里
    startup = set(SharedSettings.model_fields)
    assert {"runtime_execution_concurrency", "model_http_max_connections"} <= startup
    assert keys & startup == set()
    assert [set(entry) for entry in row["policy_json"]["model_members"]] == [MEMBER_KEYS]


# --------------------------------------------------------------------------- #
# B-224 读回口径：旧行不补新默认，篡改/缺叶的冻结策略不得被重新解释
# --------------------------------------------------------------------------- #


def test_b224_legacy_policy_without_pool_leaves_is_not_backfilled() -> None:
    policy = snapshot_policy()
    for key in ("model_pool", "capacity", "model_members"):
        policy.pop(key, None)
    assert pool_execution_policy_of(policy) is None
    assert frozen_model_members_of(policy) is None

    restored = resolve_pool_execution_policy(None)
    assert (restored.dispatch_epoch_sec, restored.model_wait_timeout_sec) == (
        ModelPoolSettings().dispatch_epoch_sec,
        CapacitySettings().model_wait_timeout_sec,
    )
    # 新行（直构路径）冻结 schema 默认的池策略，并把规范身份一起写下
    fresh = snapshot_policy()
    assert fresh["model_pool"]["policy_hash"] == restored.policy_hash
    assert fresh["capacity"] == {"model_wait_timeout_sec": CapacitySettings().model_wait_timeout_sec}
    assert "model_members" not in fresh  # 直构路径没有成员事实可冻结，不写空列表冒充


def test_b224_tampered_or_malformed_frozen_policy_is_rejected() -> None:
    policy = snapshot_policy()
    frozen = pool_execution_policy_of(policy)
    assert frozen is not None
    tampered = {**policy["model_pool"], "interactive_weight": frozen.interactive_weight + 1}
    with pytest.raises(PlatformSettingsError):
        pool_execution_policy_of({**policy, "model_pool": tampered})
    with pytest.raises(PlatformSettingsError):
        pool_execution_policy_of({**policy, "capacity": {"model_wait_timeout_sec": 0}})
    with pytest.raises(PlatformSettingsError):
        pool_execution_policy_of({**policy, "model_pool": {"dispatch_epoch_sec": 60}})
    with pytest.raises(PlatformSettingsError):
        frozen_model_members_of(
            {
                **policy,
                "model_members": [
                    {"deployment_id": "not-a-uuid", "credential_id": str(uuid.uuid4()), "weight": 1}
                ],
            }
        )
    with pytest.raises(PlatformSettingsError):
        frozen_model_members_of(
            {**policy, "model_members": [{"deployment_id": str(uuid.uuid4()), "weight": True}]}
        )
    with pytest.raises(PlatformSettingsError):
        frozen_model_members_of(
            {
                **policy,
                "model_members": [
                    {
                        "deployment_id": str(uuid.uuid4()),
                        "credential_id": str(uuid.uuid4()),
                        "weight": 1,
                        "key_resource_id": str(uuid.uuid4()),
                    }
                ],
            }
        )
    assert frozen_model_members_of({**policy, "model_members": []}) == ()
