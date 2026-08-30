"""Capacity Profile V1 scale-test 复核逻辑（Phase 6 TASK-001 / FEAT-P6-01）。

V1 契约值（design §2.3.2，用户已确认）锁定为部署/验收事实——**不是运行态配置**
（架构规则 #2/8），载体 `docs/capacity/capacity-profile-v1.md`；本模块是
`fluxion-capacity verify --profile v1` CLI 与 `tests/scale/` 套件共用的执行体。

- 批量并发 session 构造（Semaphore 有界并发，放弃逐 session 串行——design §3.5）；
- 真实 PG + 真实 Runtime（dev.echo 本地模型，无外部 LLM 依赖）；
- NFR-P6-CONSIST-01/02：双独立 ContextResolver（同 tenant+user+agent，架构规则
  28）digest 与等价性对拍；
- SLO 判定与 V1 阈值锚定在 ``V1_PROFILE``（与契约文档一致，RULE-P6-01 只紧不松：
  实测后阈值只允许收紧，修改须设计评审）。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from statistics import quantiles

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from fluxion.registry import PostgreSQLRegistryStore
from fluxion.resources import ResourceKind, ResourceStatus
from fluxion.services.context_resolver import ContextResolver, ResolverSelector
from fluxion.services.runtime_app import (
    RunRuntimeRequest,
    RuntimeApplicationService,
)

# ---------------------------------------------------------------------------
# V1 契约（与 docs/capacity/capacity-profile-v1.md 一致；RULE-P6-01 只紧不松）
#

V1_PROFILE: dict[str, float | int] = {
    "tenants": 50,
    "users_per_tenant": 1_000,
    "concurrent_sessions": 5_000,
    "runtime_replicas": 10,
    "workflow_concurrency": 100,
    "mcp_servers_per_user": 5,
    "memories_per_user": 1_000,
    # scale-test 判定 SLO（满负载口径，含 dev.echo 本地模型；实测后只紧不松）。
    # P95=1000ms 为校准值（review P0-2 披露）：初始草案 500ms → 首轮实测 608ms
    # 不达标 → 用户确认放宽至 1000ms（CLI 实跑 4 轮 583.6/603.9/687.5/580.1ms
    # + 验证轮 646.4ms；单进程集中承载 10× 单副本契约负载——详见
    # docs/capacity/capacity-profile-v1.md §2.1 校准披露）。
    "p95_execution_ms": 1000.0,
    "digest_consistency_rate": 1.0,
    "equivalence_rate": 1.0,
}


@dataclass(slots=True)
class CapacityRunReport:
    """一次 scale-test 的实测报告（CLI 输出与契约文档实测表的数据源）。"""

    run_tag: str
    tenants: int
    sessions_per_tenant: int
    total_executions: int = 0
    success_count: int = 0
    duration_seconds: float = 0.0
    latencies_ms: list[float] = field(default_factory=list)
    p50_execution_ms: float = 0.0
    p95_execution_ms: float = 0.0
    p99_execution_ms: float = 0.0
    digest_checks: int = 0
    digest_consistent: int = 0
    equivalence_checks: int = 0
    equivalent_resolutions: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def throughput_per_sec(self) -> float:
        if self.duration_seconds <= 0:
            return 0.0
        return self.total_executions / self.duration_seconds

    @property
    def digest_consistency_rate(self) -> float:
        if self.digest_checks == 0:
            return 0.0
        return self.digest_consistent / self.digest_checks

    @property
    def equivalence_rate(self) -> float:
        if self.equivalence_checks == 0:
            return 0.0
        return self.equivalent_resolutions / self.equivalence_checks

    def slo_pass(self) -> bool:
        """V1 SLO 判定：成功率 100% + P95 + digest 一致率 + equivalence。"""
        return (
            self.success_count == self.total_executions
            and self.total_executions > 0
            and self.p95_execution_ms <= float(V1_PROFILE["p95_execution_ms"])
            and self.digest_consistency_rate == float(V1_PROFILE["digest_consistency_rate"])
            and self.equivalence_rate == float(V1_PROFILE["equivalence_rate"])
        )

    def summary_lines(self) -> list[str]:
        return [
            f"executions: {self.success_count}/{self.total_executions} ok",
            (f"latency: p50={self.p50_execution_ms:.1f}ms "
            f"p95={self.p95_execution_ms:.1f}ms p99={self.p99_execution_ms:.1f}ms"),
            (f"duration: {self.duration_seconds:.1f}s "
            f"throughput={self.throughput_per_sec:.1f}/s"),
            (f"digest consistency: {self.digest_consistent}/{self.digest_checks} "
            f"({self.digest_consistency_rate:.0%})"),
            (f"capability equivalence: {self.equivalent_resolutions}/{self.equivalence_checks} "
            f"({self.equivalence_rate:.0%})"),
        ]


async def run_capacity_verification(
    *,
    registry_dsn: str,
    tenants: int,
    sessions_per_tenant: int,
    concurrency: int = 100,
    run_tag: str = "",
) -> CapacityRunReport:
    """执行一次 capacity scale-test 并返回实测报告。

    - 真实 PG：每 tenant 发布 runtime_profile + agent_definition（版本化资源）；
    - 真实 Runtime：``RuntimeApplicationService``（dev.echo）批量并发执行；
    - digest/equivalence：双独立 ContextResolver 对拍（架构规则 28 主键）。
    """
    report = CapacityRunReport(
        run_tag=run_tag or f"capacity-{uuid.uuid4().hex[:8]}",
        tenants=tenants,
        sessions_per_tenant=sessions_per_tenant,
    )
    store = PostgreSQLRegistryStore(registry_dsn)
    await store.initialize()
    service = RuntimeApplicationService.create_dev_bundle(store)
    try:
        agent_ids = await _seed_tenants(store, report)
        await _warmup(service, agent_ids)
        await _run_load(service, report, concurrency, agent_ids)
        await _check_consistency(store, report, agent_ids)
        _summarize_latency(report)
    finally:
        await service.close()

    if report.errors:
        # 明确失败留痕（不静默吞；最多展示 5 条防刷屏）
        report.errors = report.errors[:5]
    return report


# ---------------------------------------------------------------------------
# 内部实现
#


async def _seed_tenants(
    store: PostgreSQLRegistryStore, report: CapacityRunReport
) -> dict[str, str]:
    """每 tenant 发布 runtime_profile + agent_definition；返回 {tenant: agent_id}。"""
    from fluxion.resources import ResourceDefinition

    async def _publish(
        kind: ResourceKind, agent_id: str, tenant_id: str, spec: dict[str, object]
    ) -> None:
        await store.put(
            ResourceDefinition(
                kind=kind,
                id=agent_id,
                tenant_id=tenant_id,
                version="1",
                status=ResourceStatus.DRAFT,
                spec_json=spec,
            )
        )
        await store.publish(kind, agent_id, tenant_id=tenant_id, version="1")

    agent_ids: dict[str, str] = {}
    for index in range(report.tenants):
        tenant_id = f"{report.run_tag}-t{index}"
        agent_id = f"assistant-{index}"
        await _publish(
            ResourceKind.RUNTIME_PROFILE,
            agent_id,
            tenant_id,
            {"request_timeout_ms": 30_000, "max_retries": 1},
        )
        await _publish(
            ResourceKind.AGENT_DEFINITION,
            agent_id,
            tenant_id,
            {
                "name": f"助手-{index}",
                "system_prompt": "你是产品助手。",
                "owner": "builder",
                "model_ref": {"id": "dev.echo", "version": "1"},
            },
        )
        agent_ids[tenant_id] = agent_id
    return agent_ids


async def _warmup(service: RuntimeApplicationService, agent_ids: dict[str, str]) -> None:
    """每 tenant 首次执行预热 resolver/连接池（不计入延迟统计）。"""
    for index, (tenant_id, agent_id) in enumerate(agent_ids.items()):
        if index >= 3:
            break
        await service.run(
            RunRuntimeRequest(
                tenant_id=tenant_id,
                user_id="warmup-user",
                runtime_profile_id=agent_id,
                session_id="warmup",
                input_message="warmup",
            )
        )


async def _run_load(
    service: RuntimeApplicationService,
    report: CapacityRunReport,
    concurrency: int,
    agent_ids: dict[str, str],
) -> None:
    """批量并发 Execution（有界并发；非逐 session 串行）。"""

    async def _one(tenant_id: str, agent_id: str, session_index: int) -> None:
        started = time.perf_counter()
        try:
            await service.run(
                RunRuntimeRequest(
                    tenant_id=tenant_id,
                    user_id=f"user-{session_index % 10}",
                    runtime_profile_id=agent_id,
                    session_id=f"session-{session_index}",
                    input_message="capacity-ping",
                )
            )
            report.success_count += 1
            report.latencies_ms.append((time.perf_counter() - started) * 1000)
        except Exception as error:  # noqa: BLE001 — 压测失败逐条留痕，不中断整体
            report.errors.append(f"{tenant_id}/session-{session_index}: {error}")

    semaphore = asyncio.Semaphore(concurrency)

    async def _bounded(tenant_id: str, agent_id: str, session_index: int) -> None:
        async with semaphore:
            await _one(tenant_id, agent_id, session_index)

    started = time.perf_counter()
    tasks = [
        _bounded(tenant_id, agent_id, session_index)
        for tenant_id, agent_id in agent_ids.items()
        for session_index in range(report.sessions_per_tenant)
    ]
    report.total_executions = len(tasks)
    await asyncio.gather(*tasks)
    report.duration_seconds = time.perf_counter() - started


async def _check_consistency(
    store: PostgreSQLRegistryStore,
    report: CapacityRunReport,
    agent_ids: dict[str, str],
) -> None:
    """NFR-P6-CONSIST-01/02：双独立 engine/resolver 对拍（架构规则 28）。

    review P2：双独立 engine（分离连接池，强于同池代理；真实跨进程/跨 Pod 由
    S-07 k8s 逐 Pod 对拍承接）。
    """
    engine_b = create_async_engine(store.engine.url.render_as_string(hide_password=False))
    try:
        await _consistency_pairs(store, engine_b, report, agent_ids)
    finally:
        await engine_b.dispose()


async def _consistency_pairs(
    store: PostgreSQLRegistryStore,
    engine_b: AsyncEngine,
    report: CapacityRunReport,
    agent_ids: dict[str, str],
) -> None:
    resolver_a = ContextResolver(store.engine)
    resolver_b = ContextResolver(engine_b)
    for tenant_id, agent_id in agent_ids.items():
        selector = ResolverSelector(
            tenant_id=tenant_id, agent_id=agent_id, user_id="user-consistency"
        )
        result_a = await resolver_a.resolve(selector, session_id="s-consistency-a")
        result_b = await resolver_b.resolve(selector, session_id="s-consistency-b")

        report.digest_checks += 1
        if (
            result_a.snapshot.snapshot_digest
            and result_a.snapshot.snapshot_digest == result_b.snapshot.snapshot_digest
        ):
            report.digest_consistent += 1

        report.equivalence_checks += 1
        if _snapshots_equivalent(result_a.snapshot, result_b.snapshot):
            report.equivalent_resolutions += 1


def _snapshots_equivalent(snapshot_a: object, snapshot_b: object) -> bool:
    """capability equivalence：解析等价的关键字段（版本图谱 + 模型解析）。"""
    keys = (
        "tenant_id",
        "user_id",
        "runtime_profile_id",
        "runtime_profile_version",
        "agent_definition_id",
        "agent_definition_version",
        "model_resolution",
        "system_prompt",
        "policy_version",
    )
    return all(getattr(snapshot_a, key) == getattr(snapshot_b, key) for key in keys)


def _summarize_latency(report: CapacityRunReport) -> None:
    if not report.latencies_ms:
        return
    values = sorted(report.latencies_ms)
    if len(values) >= 100:
        qs = quantiles(values, n=100, method="inclusive")
        report.p50_execution_ms = qs[49]
        report.p95_execution_ms = qs[94]
        report.p99_execution_ms = qs[98]
    else:
        report.p50_execution_ms = values[len(values) // 2]
        report.p95_execution_ms = values[int(len(values) * 0.95) - 1]
        report.p99_execution_ms = values[-1]


__all__ = ["V1_PROFILE", "CapacityRunReport", "run_capacity_verification"]
