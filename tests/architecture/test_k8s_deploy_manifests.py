"""k8s 清单契约：部署侧那几条「漏了就跑不起来、且症状难查」的硬约束。

全部来自实测踩坑（2026-10-04 真集群多 Pod 验证，见 `deploy/k8s/README.md`）：
  1. 探针口径：readiness 接 `/readyz`（依赖缺失回 503，不该往这种 Pod 转流量）、liveness 接 `/healthz`；
  2. 服务间接线：四个单元的地址必须由 ConfigMap 显式给（否则应用回落到 `127.0.0.1` ⇒ 集群内连不上自己）；
  3. 服务身份：四个单元都要 `INTERNAL_SERVICE_TOKEN`（内部端点要求 `X-Internal-Service`）；
  4. 镜像必须全名（裸名会被当 Docker Hub 的 `docker.io/library/...` 去拉）；
  5. 迁移有入口（Job 在清单里、且被 kustomize 收集）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

BASE = Path("deploy/k8s/base")
CONNECTOR_OVERLAY = Path("deploy/k8s/with-connector")
DEPLOYMENTS = ("console-platform", "agent-runtime", "agent-worker", "im-gateway")


def _docs(name: str) -> list[dict[str, Any]]:
    return [
        doc
        for doc in yaml.safe_load_all((BASE / name).read_text(encoding="utf-8"))
        if isinstance(doc, dict)
    ]


def _container(name: str) -> dict[str, Any]:
    docs = [d for d in _docs(f"{name}.yaml") if d.get("kind") == "Deployment"]
    assert len(docs) == 1, f"{name}.yaml 应有且仅有一个 Deployment"
    return docs[0]["spec"]["template"]["spec"]["containers"][0]


@pytest.mark.parametrize("name", DEPLOYMENTS)
def test_probes_use_readyz_for_readiness_and_healthz_for_liveness(name: str) -> None:
    container = _container(name)
    assert container["readinessProbe"]["httpGet"]["path"] == "/readyz", (
        f"{name}: readiness 必须接 /readyz —— 依赖缺失时就绪为 503，别把流量转进去"
    )
    assert container["livenessProbe"]["httpGet"]["path"] == "/healthz", (
        f"{name}: liveness 接 /healthz（存活），不要接 /readyz，否则依赖抖动会重启 Pod"
    )


@pytest.mark.parametrize("name", DEPLOYMENTS)
@pytest.mark.parametrize("probe_name", ("readinessProbe", "livenessProbe"))
def test_probe_budgets_tolerate_measured_burst_latency(name: str, probe_name: str) -> None:
    """800 并发时默认 1s 探针误杀活跃 Runtime，断开全部在飞流。"""
    probe = _container(name)[probe_name]
    assert probe.get("timeoutSeconds", 1) >= 5, f"{name}: {probe_name} 超时至少 5s"
    assert probe.get("periodSeconds") == 10, f"{name}: 探针周期显式保持 10s"
    assert probe.get("failureThreshold") == 3, f"{name}: 连续 3 次失败才判不健康"


@pytest.mark.parametrize("name", DEPLOYMENTS)
def test_image_is_fully_qualified(name: str) -> None:
    image = _container(name)["image"]
    assert "/" in image, f"{name}: 镜像 {image!r} 是裸名，会被解析到 docker.io/library"


@pytest.mark.parametrize("name", DEPLOYMENTS)
def test_internal_service_token_is_injected(name: str) -> None:
    env = {item["name"]: item for item in _container(name).get("env", [])}
    assert "INTERNAL_SERVICE_TOKEN" in env, (
        f"{name}: 缺 INTERNAL_SERVICE_TOKEN —— 内部端点要求 X-Internal-Service，缺了什么都调不通"
    )
    source = env["INTERNAL_SERVICE_TOKEN"]["valueFrom"]["secretKeyRef"]
    assert source["name"] == "muad-external" and source["key"] == "INTERNAL_SERVICE_TOKEN"


def test_configmap_carries_inter_service_urls_and_default_tenant() -> None:
    data = next(d for d in _docs("configmap.yaml") if d.get("kind") == "ConfigMap")["data"]
    for key in (
        "CONSOLE_PLATFORM_URL",
        "AGENT_RUNTIME_URL",
        "AGENT_WORKER_URL",
        "IM_GATEWAY_URL",
        "DEFAULT_TENANT_ID",
        "ARTIFACT_ROOT",
    ):
        assert key in data, f"ConfigMap 缺 {key}：应用会回落到 127.0.0.1 的本机默认值"
    # 服务间地址必须是集群内 DNS 名，不是本机
    assert data["CONSOLE_PLATFORM_URL"].startswith("http://muad-console-platform")


def test_migration_job_exists_and_is_collected_by_kustomize() -> None:
    docs = _docs("migrate-job.yaml")
    job = next((d for d in docs if d.get("kind") == "Job"), None)
    assert job is not None, "缺少迁移 Job：集群里就没有 schema 变更入口"
    assert "db_migrate.py" in job["spec"]["template"]["spec"]["containers"][0]["args"][0]

    resources = next(
        d for d in _docs("kustomization.yaml") if d.get("kind") == "Kustomization"
    )["resources"]
    assert "migrate-job.yaml" in resources, "Job 没被 kustomize 收集 = 部署时不会生效"


@pytest.mark.parametrize("name", DEPLOYMENTS)
def test_pods_run_as_non_root_with_matching_fs_group(name: str) -> None:
    """与镜像里固定的 UID/GID 10001 对齐：不对齐时共享产物卷写不进去（fsGroup 决定组权限）。"""
    pod_spec = [d for d in _docs(f"{name}.yaml") if d.get("kind") == "Deployment"][0]["spec"][
        "template"
    ]["spec"]
    security = pod_spec["securityContext"]
    assert security["runAsNonRoot"] is True
    assert security["runAsUser"] == 10001 and security["fsGroup"] == 10001


# --------------------------------------------------------------------------------------
# Connector（im-gateway 的有状态运行时角色）：独立镜像/无公开入口/自身 secret/PDB
# --------------------------------------------------------------------------------------


def _connector_docs() -> list[dict[str, Any]]:
    return [
        doc
        for doc in yaml.safe_load_all(
            (CONNECTOR_OVERLAY / "connector.yaml").read_text(encoding="utf-8")
        )
        if isinstance(doc, dict)
    ]


def _connector_deployment() -> dict[str, Any]:
    docs = [d for d in _connector_docs() if d.get("kind") == "Deployment"]
    assert len(docs) == 1, "connector.yaml 应有且仅有一个 Deployment"
    return docs[0]


def test_connector_overlay_is_collected_and_gateway_is_multi_replica() -> None:
    """5 单元 overlay 只多一个 Connector（无 patches）；Gateway 多副本在 base 里声明。"""
    kustomization = next(
        d for d in yaml.safe_load_all(
            (CONNECTOR_OVERLAY / "kustomization.yaml").read_text(encoding="utf-8")
        ) if isinstance(d, dict) and d.get("kind") == "Kustomization"
    )
    assert "connector.yaml" in kustomization["resources"], "Connector 清单必须被 overlay 收集"
    assert "../base" in kustomization["resources"], "5 单元 overlay 必须复用 base 的四单元"
    assert "patches" not in kustomization, (
        "overlay 只用 resources：预检在无 kustomize 的环境里走收集展开也要能验证它"
    )
    gateway = next(
        d for d in yaml.safe_load_all(
            (BASE / "im-gateway.yaml").read_text(encoding="utf-8")
        ) if isinstance(d, dict) and d.get("kind") == "Deployment"
    )
    assert gateway["spec"]["replicas"] == 2, "Gateway 允许 N 副本（design §4.1）：base 基线 2"
    config = next(
        d for d in yaml.safe_load_all(
            (BASE / "configmap.yaml").read_text(encoding="utf-8")
        ) if isinstance(d, dict) and d.get("kind") == "ConfigMap"
    )
    assert config["data"].get("GATEWAY_ROUTED_CHANNELS") == "WECOM", (
        "请求面必须声明远程通道只路由（入站 im.inbound、出站 im.outbound）"
    )


def test_connector_uses_its_own_secret_without_database_url() -> None:
    env = {
        item["name"]: item
        for item in _connector_deployment()["spec"]["template"]["spec"]["containers"][0].get(
            "env", []
        )
    }
    assert "DATABASE_URL" not in env, "Connector 不持库：不得注入 DATABASE_URL"
    assert "WECOM_SECRET" not in env and "BOT_SECRET" not in env, (
        "渠道连接凭据由 Console 快照下发，不得进 Deployment 环境变量"
    )
    for key in ("REDIS_URL", "INTERNAL_SERVICE_TOKEN"):
        source = env[key]["valueFrom"]["secretKeyRef"]
        assert source["name"] == "muad-connector-external", (
            f"{key} 必须来自 Connector 自己的 secret（只挂自身 secret）"
        )


def test_connector_probes_metrics_and_lease_budget_are_declared() -> None:
    container = _connector_deployment()["spec"]["template"]["spec"]["containers"][0]
    assert container["readinessProbe"]["httpGet"]["path"] == "/readyz"
    assert container["livenessProbe"]["httpGet"]["path"] == "/healthz"
    for probe in ("readinessProbe", "livenessProbe"):
        assert container[probe]["timeoutSeconds"] >= 5
        assert container[probe]["periodSeconds"] == 10
        assert container[probe]["failureThreshold"] == 3
    env = {item["name"]: item.get("value") for item in container.get("env", [])}
    ttl = float(str(env["CONNECTOR_LEASE_TTL_SEC"]))
    renew = float(str(env["CONNECTOR_LEASE_RENEW_SEC"]))
    assert ttl >= 5 and renew < ttl / 2, "lease 续租必须显著早于过期（避免互相抢连接）"
    assert container["ports"][0]["containerPort"] == 8004, "Connector 的 metrics/probe 端口"


def test_connector_has_pdb_and_no_public_ingress_route() -> None:
    pdbs = [d for d in _connector_docs() if d.get("kind") == "PodDisruptionBudget"]
    assert len(pdbs) == 1, "Connector 必须有显式 PDB（有状态 bot 连接）"
    assert pdbs[0]["spec"]["maxUnavailable"] == 1, "PDB 允许一次有界中断，接管由 lease 完成"
    services = [d for d in _connector_docs() if d.get("kind") == "Service"]
    assert len(services) == 1, "Connector 需要一个内部 Service（探针/指标），不是公开入口"
    ingress = [
        doc for doc in yaml.safe_load_all(
            (BASE / "ingress.yaml").read_text(encoding="utf-8")
        ) if isinstance(doc, dict)
    ]
    backend_services = {
        ((entry.get("backend") or {}).get("service") or {}).get("name")
        for doc in ingress
        for rule in (doc.get("spec", {}).get("rules") or [])
        for entry in ((rule.get("http") or {}).get("paths") or [])
    }
    assert "muad-connector" not in backend_services, "Connector 不得有公开入口（不暴露业务路由）"
