"""TASK-005（Phase 6）真实部署 Gate（FEAT-P6-05 ①，S-07 / Gate G3 / P0-3）。

S-07[E2E]：本地 k8s（OrbStack）真实集群 ≥2 RuntimeInstance 副本（共享宿主 PG）：
- rolling restart → 全副本 Ready → Snapshot digest 一致率=100%（逐 Pod 内真实
  ContextResolver 解析对拍）；
- kill 任一副本 → 替换 Pod Ready → committed durable facts RPO=0（kill 前提交
  的事实行仍在）+ 关键 facts 表零丢失。

前置：helm 部署（见 test_k8s_deployment.py 文档）；门控 FLUXION_K8S_TEST=1。
PG 不可达/未部署时 skip——绝不伪造 GREEN。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import uuid

import pytest

_NAMESPACE = os.environ.get("FLUXION_K8S_NAMESPACE", "fluxion")
_API_DEPLOYMENT = os.environ.get("FLUXION_K8S_API_DEPLOYMENT", "fluxion")
# 宿主 PG 上的部署库（k8s Pod 与宿主共享）
_K8S_PG_DSN = os.environ.get(
    "FLUXION_K8S_PG_DSN",
    "postgresql+asyncpg://mmuser:mmuser@localhost:5432/fluxion",
)

_k8s_enabled = os.environ.get("FLUXION_K8S_TEST") == "1"
_kubectl_available = shutil.which("kubectl") is not None

pytestmark = [
    pytest.mark.skipif(not _k8s_enabled, reason="FLUXION_K8S_TEST=1 未设置（S-07 部署 Gate 门控）"),
    pytest.mark.skipif(not _kubectl_available, reason="kubectl 不可用"),
]


def _kubectl(*args: str, timeout: float = 120.0) -> str:
    result = subprocess.run(
        ["kubectl", "-n", _NAMESPACE, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(f"kubectl {' '.join(args)} 失败: {result.stderr.strip()}")
    return result.stdout.strip()


def _api_pods() -> list[str]:
    """当前存活 API Pod：Running + 全容器 ready + 无 deletionTimestamp。

    （Terminating 残留 Pod 上 exec 会 137/NotFound——rollout 完成后旧 Pod 仍会
    短暂存在，必须排除。）
    """
    import json

    payload = _kubectl(
        "get", "pods",
        "-l", "app.kubernetes.io/instance=fluxion,app.kubernetes.io/name=fluxion",
        "-o", "json",
    )
    items = json.loads(payload)["items"]
    alive: list[str] = []
    for item in items:
        name = item["metadata"]["name"]
        if "workflow-worker" in name:
            continue
        if item["metadata"].get("deletionTimestamp") is not None:
            continue
        if item.get("status", {}).get("phase") != "Running":
            continue
        containers = item.get("status", {}).get("containerStatuses", [])
        if containers and all(c.get("ready") for c in containers):
            alive.append(name)
    return sorted(alive)


def _wait_deployment_ready(deployment: str, timeout: float = 180.0) -> None:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        output = _kubectl(
            "get", "deployment", deployment,
            "-o", "jsonpath={.status.readyReplicas}/{.status.replicas}",
        )
        ready, _, total = output.partition("/")
        if total and int(ready or 0) == int(total) >= 2:
            return
        time.sleep(3)
    raise AssertionError(f"deployment {deployment} {timeout}s 内未全部 Ready")


async def _seed_gate_agent() -> str:
    """在共享 PG（部署库）发布 Gate 用 agent，返回 tenant_id。"""
    from fluxion.registry import PostgreSQLRegistryStore
    from fluxion.resources import ResourceKind
    from tests.runtime_helpers import publish_resource, seed_model_definition

    tenant_id = f"tenant-s07-{uuid.uuid4().hex[:8]}"
    agent_id = "gate-agent"
    store = PostgreSQLRegistryStore(_K8S_PG_DSN)
    await store.initialize()
    try:
        await publish_resource(
            store,
            tenant_id=tenant_id,
            kind=ResourceKind.RUNTIME_PROFILE,
            resource_id=agent_id,
            version="1",
            spec={"request_timeout_ms": 30_000, "max_retries": 1},
        )
        # ADR-A008：agent.model_policy 指向 ModelDefinition（model.dev.echo）
        await seed_model_definition(store, tenant_id=tenant_id, provider_id="dev.echo")
        await publish_resource(
            store,
            tenant_id=tenant_id,
            kind=ResourceKind.AGENT_DEFINITION,
            resource_id=agent_id,
            version="1",
            spec={
                "name": "gate-agent",
                "system_prompt": "你是产品助手。",
                "owner": "builder",
                "model_policy": {
                    "primary_model_ref": {"id": "model.dev.echo", "version": "1"}
                },
            },
        )
    finally:
        await store.close()
    return tenant_id


async def _publish_rpo_fact(tenant_id: str) -> None:
    """经治理事务发布独立 durable fact 资源（audit+publish_records+outbox 原子落库）。

    用独立 resource_id（gate-rpo-fact）而非 gate-agent 新版本——避免改变
    digest 对拍资源的 latest published 版本。
    """
    from fluxion.registry import PostgreSQLRegistryStore
    from fluxion.registry.store import PublicationCommand, PublicationOperation
    from fluxion.resources import ResourceDefinition, ResourceKind, ResourceStatus

    store = PostgreSQLRegistryStore(_K8S_PG_DSN)
    await store.initialize()
    try:
        await store.put(
            ResourceDefinition(
                kind=ResourceKind.RUNTIME_PROFILE,
                id="gate-rpo-fact",
                tenant_id=tenant_id,
                version="1",
                status=ResourceStatus.DRAFT,
                spec_json={"request_timeout_ms": 30_000, "max_retries": 1},
            )
        )
        await store.commit_publication(
            PublicationCommand(
                publish_id=f"pub-s07-{uuid.uuid4().hex[:8]}",
                event_id=f"evt-s07-{uuid.uuid4().hex[:8]}",
                tenant_id=tenant_id,
                kind=ResourceKind.RUNTIME_PROFILE,
                resource_id="gate-rpo-fact",
                version="1",
                operation=PublicationOperation.PUBLISH,
                actor_id="gate",
                request_id="req-s07",
                trace_id="trace-s07",
            )
        )
    finally:
        await store.close()


# Pod 内 digest 解析脚本（真实 ContextResolver + 真实 PG）
_DIGEST_SCRIPT = """
import asyncio, os
from fluxion.registry import PostgreSQLRegistryStore
from fluxion.services.context_resolver import ContextResolver, ResolverSelector
async def main():
    store = PostgreSQLRegistryStore(os.environ["FLUXION_DATABASE_URL"])
    resolver = ContextResolver(store)
    result = await resolver.resolve(
        ResolverSelector(tenant_id="{tenant}", agent_id="gate-agent", user_id="user-gate"),
        session_id="s07-gate",
    )
    print("DIGEST", result.snapshot.snapshot_digest)
    await store.close()
asyncio.run(main())
"""


def _pod_digest(pod: str, tenant_id: str) -> str:
    script = _DIGEST_SCRIPT.format(tenant=tenant_id)
    output = _kubectl("exec", pod, "--", "python", "-c", script, timeout=120.0)
    for line in output.splitlines():
        if line.startswith("DIGEST "):
            return line.split(" ", 1)[1].strip()
    raise AssertionError(f"{pod} 未输出 DIGEST: {output}")


class TestS07DeploymentGate:
    async def test_s07_rolling_restart_digest_consistency_rpo_zero(self) -> None:
        """S-07[E2E]：rolling restart → digest 一致率=100% + RPO=0 + facts 零丢失。"""
        tenant_id = await _seed_gate_agent()

        # 基线：先等既有 rollout 完成（避免上次滚动与本轮基线竞争），再取 Ready Pod
        _kubectl(
            "rollout", "status", f"deployment/{_API_DEPLOYMENT}", "--timeout=180s",
            timeout=200.0,
        )
        _wait_deployment_ready(_API_DEPLOYMENT)
        pods_before = _api_pods()
        assert len(pods_before) >= 2, f"API 副本 {len(pods_before)} < 2"

        # digest 基线（逐 Pod 解析）
        digests_before = {pod: _pod_digest(pod, tenant_id) for pod in pods_before}
        assert len(set(digests_before.values())) == 1, (
            f"重启前 digest 已不一致: {digests_before}"
        )
        baseline_digest = next(iter(digests_before.values()))

        # kill 前提交 durable fact（RPO 证据）——review 残留修复：经真实治理事务
        # commit_publication（audit_logs + publish_records + outbox 原子落库），
        # 替代直插 audit_logs
        await _publish_rpo_fact(tenant_id)

        # 故障注入 1：kill 任一副本 → 替换 Pod Ready
        victim = pods_before[0]
        _kubectl("delete", "pod", victim, "--wait=true")
        _wait_deployment_ready(_API_DEPLOYMENT)

        # 故障注入 2：rolling restart（全副本滚动替换）
        _kubectl("rollout", "restart", f"deployment/{_API_DEPLOYMENT}")
        _kubectl(
            "rollout", "status", f"deployment/{_API_DEPLOYMENT}", "--timeout=180s",
            timeout=200.0,
        )
        _wait_deployment_ready(_API_DEPLOYMENT)

        # digest 一致率=100%（新副本逐 Pod 对拍，且与基线一致）
        pods_after = _api_pods()
        assert len(pods_after) >= 2
        digests_after = {pod: _pod_digest(pod, tenant_id) for pod in pods_after}
        assert len(set(digests_after.values())) == 1, f"重启后 digest 不一致: {digests_after}"
        assert next(iter(digests_after.values())) == baseline_digest, (
            "重启前后 digest 漂移（Snapshot 事实必须跨副本一致，架构规则 28）"
        )

        # RPO=0：kill/rolling restart 后治理事务产物仍在（publish_records v2 + audit）
        import psycopg

        with psycopg.connect(_K8S_PG_DSN.replace("+asyncpg", ""), autocommit=True) as conn:
            publishes = conn.execute(
                "SELECT COUNT(*) FROM publish_records WHERE tenant_id = %s "
                "AND resource_id = 'gate-rpo-fact' AND version = '1'",
                (tenant_id,),
            ).fetchone()[0]
            audits = conn.execute(
                "SELECT COUNT(*) FROM audit_logs WHERE tenant_id = %s "
                "AND target_id = 'gate-rpo-fact'",
                (tenant_id,),
            ).fetchone()[0]
        assert publishes == 1, f"治理事务 publish_records 丢失（RPO>0）: {tenant_id}"
        assert audits >= 1, f"治理事务 audit_logs 丢失（RPO>0）: {tenant_id}"

        # facts 零丢失：关键 facts 表行数（resource_definitions/audit_logs）≥ 重启前
        #（新发布的 gate agent 增加了行数——断言不减即可证零丢失）
        with psycopg.connect(_K8S_PG_DSN.replace("+asyncpg", ""), autocommit=True) as conn:
            resources = conn.execute(
                "SELECT COUNT(*) FROM resource_definitions WHERE tenant_id = %s",
                (tenant_id,),
            ).fetchone()[0]
        # gate-agent v1（runtime_profile + agent_definition）+ model.dev.echo v1
        # （ADR-A008 解析链 fixture）+ gate-rpo-fact v1 = 4 行
        assert resources == 4, (
            f"租户资源事实零丢失（gate-agent×2 + model_definition×1 + rpo-fact×1）: {resources}"
        )
