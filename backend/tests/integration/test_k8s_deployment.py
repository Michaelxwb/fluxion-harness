"""TASK-006（Phase 6）k8s Pod 部署级验证（FEAT-P6-06，S-10 k8s 边界）。

真实边界：本地 k8s（OrbStack）真实集群——Helm 部署后的真实 Pod：
- API Deployment（Runtime/Console 生产 bundle）≥2 副本全部 Ready；
- fluxion-workflow-worker Deployment（DBOS 执行进程）≥2 副本全部 Ready；
- Pod 内 /healthz 200（生产 bundle 存活探针路径）。

前置（部署基建，见任务 Checklist k8s 三项）：
  docker build -f deploy/docker/Dockerfile -t fluxion-harness/fluxion:<tag> .
  helm upgrade --install fluxion deploy/helm/fluxion -n fluxion --create-namespace \
    --set image.tag=<tag> --set postgresql.enabled=false \
    --set externalDatabase.url=... --set secrets.masterKey=...

门控：FLUXION_K8S_TEST=1 且 kubectl 可用；未设置时 skip（不伪造 GREEN）。
"""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest

_NAMESPACE = os.environ.get("FLUXION_K8S_NAMESPACE", "fluxion")
_RELEASE = os.environ.get("FLUXION_K8S_RELEASE", "fluxion")
# fullname 帮助函数：release 名含 chart 名（fluxion）时 fullname = release 名，
# 故 API Deployment 为 `fluxion`、worker 为 `fluxion-workflow-worker`。
_API_DEPLOYMENT = os.environ.get("FLUXION_K8S_API_DEPLOYMENT", "fluxion")
_WORKER_DEPLOYMENT = os.environ.get(
    "FLUXION_K8S_WORKER_DEPLOYMENT", "fluxion-workflow-worker"
)
_MIN_REPLICAS = 2

_k8s_enabled = os.environ.get("FLUXION_K8S_TEST") == "1"
_kubectl_available = shutil.which("kubectl") is not None

pytestmark = [
    pytest.mark.skipif(not _k8s_enabled, reason="FLUXION_K8S_TEST=1 未设置（k8s 部署级验证门控）"),
    pytest.mark.skipif(not _kubectl_available, reason="kubectl 不可用"),
]


def _kubectl(*args: str) -> str:
    result = subprocess.run(
        ["kubectl", "-n", _NAMESPACE, *args],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(f"kubectl {' '.join(args)} 失败: {result.stderr.strip()}")
    return result.stdout.strip()


def _ready_replicas(deployment: str) -> tuple[int, int]:
    """返回 (readyReplicas, replicas)；deployment 不存在 → 断言失败。"""
    output = _kubectl(
        "get", "deployment", deployment,
        "-o", "jsonpath={.status.readyReplicas}/{.status.replicas}",
    )
    ready, _, total = output.partition("/")
    return int(ready or 0), int(total or 0)


class TestS10K8sDeployment:
    def test_api_replicas_ready(self) -> None:
        """S-10[k8s]：API Deployment ≥2 副本全部 Ready（生产 bundle 多副本）。"""
        ready, total = _ready_replicas(_API_DEPLOYMENT)
        assert total >= _MIN_REPLICAS, f"API 副本数 {total} < {_MIN_REPLICAS}"
        assert ready == total, f"API 就绪 {ready}/{total}——存在未就绪副本"

    def test_worker_replicas_ready(self) -> None:
        """S-10[k8s]：workflow-worker Deployment ≥2 副本全部 Ready（DBOS 执行进程）。"""
        ready, total = _ready_replicas(_WORKER_DEPLOYMENT)
        assert total >= _MIN_REPLICAS, f"worker 副本数 {total} < {_MIN_REPLICAS}"
        assert ready == total, f"worker 就绪 {ready}/{total}——存在未就绪副本"

    def test_pod_healthz(self) -> None:
        """S-10[k8s]：每个 API Pod 内 /healthz 返回 200（生产 bundle 存活路径）。"""
        pods = _kubectl(
            "get", "pods",
            "-l", f"app.kubernetes.io/instance={_RELEASE},app.kubernetes.io/name=fluxion",
            "-o", "jsonpath={.items[*].metadata.name}",
        ).split()
        api_pods = [pod for pod in pods if "workflow-worker" not in pod]
        assert len(api_pods) >= _MIN_REPLICAS, f"API Pod 数 {len(api_pods)} < {_MIN_REPLICAS}"
        for pod in api_pods:
            _kubectl(
                "exec", pod, "--",
                "python", "-c",
                "import urllib.request; urllib.request.urlopen("
                "'http://127.0.0.1:8000/healthz', timeout=5)",
            )
