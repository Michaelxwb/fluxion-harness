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
