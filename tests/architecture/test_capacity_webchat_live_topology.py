"""压测前核对实际集群，拦截 Console/Gateway 的副本漂移。"""

from __future__ import annotations

import subprocess
from typing import Any

import pytest

from scripts import check_agent_capacity_deployment as preflight


def _documents() -> list[dict[str, Any]]:
    documents: list[dict[str, Any]] = []
    for unit, replicas in (("console-platform", 1), ("im-gateway", 1), ("agent-runtime", 4)):
        name = f"muad-{unit}"
        documents.append({
            "kind": "Deployment", "metadata": {"name": name},
            "spec": {"replicas": replicas},
            "status": {"readyReplicas": replicas, "replicas": replicas},
        })
        for index in range(replicas):
            documents.append({
                "kind": "Pod", "metadata": {"name": f"{name}-{index}", "labels": {"app": name}},
                "status": {"phase": "Running", "conditions": [{"type": "Ready", "status": "True"}]},
            })
    return documents


def test_live_topology_allows_multiple_runtime_pods() -> None:
    assert preflight.check_live_single_pods(_documents()) == []


@pytest.mark.parametrize("unit", ("console-platform", "im-gateway"))
@pytest.mark.parametrize("drift", (
    "desired", "actual", "extra_pod", "not_ready", "terminating", "missing", "hpa",
))
def test_live_topology_rejects_single_pod_drift(unit: str, drift: str) -> None:
    documents = _documents()
    name = f"muad-{unit}"
    deployment = next(d for d in documents if d["kind"] == "Deployment" and d["metadata"]["name"] == name)
    pod = next(d for d in documents if d["kind"] == "Pod" and d["metadata"]["labels"]["app"] == name)
    if drift == "desired":
        deployment["spec"]["replicas"] = 4
    elif drift == "actual":
        deployment["status"]["replicas"] = 4
    elif drift == "extra_pod":
        documents.append({**pod, "metadata": {"name": f"{name}-extra", "labels": {"app": name}}})
    elif drift == "not_ready":
        pod["status"]["conditions"][0]["status"] = "False"
    elif drift == "terminating":
        pod["metadata"]["deletionTimestamp"] = "2026-10-21T11:00:00Z"
    elif drift == "missing":
        documents.remove(deployment)
    else:
        documents.append({
            "kind": "HorizontalPodAutoscaler", "metadata": {"name": "arbitrary-name"},
            "spec": {"scaleTargetRef": {"name": name}},
        })
    violations = preflight.check_live_single_pods(documents)
    assert violations and all(v.check == "live-topology" for v in violations)
    assert all(unit in v.message for v in violations)


@pytest.mark.parametrize("payload", ("not-json", "{}", '{"items":1}', '{"items":[null]}'))
def test_live_reader_rejects_malformed_cluster_output(
    monkeypatch: pytest.MonkeyPatch, payload: str,
) -> None:
    def run(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert argv == ["kubectl", "--namespace", "muad-cap", "get", "deployments,pods,hpa", "-o", "json"]
        return subprocess.CompletedProcess(argv, 0, stdout=payload)

    monkeypatch.setattr(preflight.subprocess, "run", run)
    with pytest.raises(preflight.PreflightError, match="实时拓扑"):
        preflight._read_live_resources("muad-cap")


def test_live_reader_reports_kubectl_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def run(_argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired("kubectl", 30)

    monkeypatch.setattr(preflight.subprocess, "run", run)
    with pytest.raises(preflight.PreflightError, match="不能给出压测拓扑通过结论"):
        preflight._read_live_resources("muad-cap")
