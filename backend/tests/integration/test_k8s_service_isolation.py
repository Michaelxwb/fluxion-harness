"""TASK-002: Kubernetes Service 角色隔离验收（S-02）。

真实边界：真实 `helm template` 渲染 → 断言 Service/Deployment 的 selector
与 Pod 标签按角色隔离；有真实集群（FLUXION_K8S_TEST=1 + kubectl）时进一步
验证 Service EndpointSlice 只含对应角色 Pod。不 mock Helm/K8s。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

_CHART = str(Path(__file__).resolve().parents[3] / "deploy" / "helm" / "fluxion")
_MASTER_KEY = "dGVzdG1hc3RlcmtleXRlc3R0ZXN0MTIzNDU2Nzg="

_k8s_enabled = os.environ.get("FLUXION_K8S_TEST") == "1"
_kubectl_available = shutil.which("kubectl") is not None


def _render(release: str = "release-name", extra: list[str] | None = None) -> list[dict]:
    cmd = [
        "helm", "template", release, _CHART,
        "--set", f"secrets.masterKey={_MASTER_KEY}",
        *(extra or []),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
    assert result.returncode == 0, f"helm template 失败: {result.stderr.strip()}"
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def _find(docs: list[dict], kind: str, name: str) -> dict:
    for doc in docs:
        if doc.get("kind") == kind and doc.get("metadata", {}).get("name") == name:
            return doc
    raise AssertionError(f"未渲染出 {kind}/{name}")


def _fullname(release: str = "release-name") -> str:
    return f"{release}-fluxion"


def _matches(selector: dict[str, str], labels: dict[str, str]) -> bool:
    return all(labels.get(key) == value for key, value in selector.items())


class TestS02ServiceIsolation:
    def test_main_service_selects_only_api(self) -> None:
        """S-02：主 Service 只选 API Pod（含 component=api），不选 Runtime/Worker。"""
        docs = _render()
        service = _find(docs, "Service", _fullname())
        selector = service["spec"]["selector"]
        assert selector.get("app.kubernetes.io/component") == "api"

        api = _find(docs, "Deployment", _fullname())
        api_labels = api["spec"]["template"]["metadata"]["labels"]
        assert _matches(selector, api_labels)

        runtime = _find(docs, "Deployment", f"{_fullname()}-runtime")
        runtime_labels = runtime["spec"]["template"]["metadata"]["labels"]
        assert not _matches(selector, runtime_labels)

    def test_runtime_service_selects_only_runtime(self) -> None:
        """S-02：独立 Runtime Service 只选 Runtime Pod，ClusterIP，8000→http。"""
        docs = _render()
        service = _find(docs, "Service", f"{_fullname()}-runtime")
        assert service["spec"]["type"] == "ClusterIP"
        selector = service["spec"]["selector"]
        assert selector.get("app.kubernetes.io/component") == "runtime"
        ports = service["spec"]["ports"]
        assert any(p.get("port") == 8000 and p.get("targetPort") == "http" for p in ports)

        runtime = _find(docs, "Deployment", f"{_fullname()}-runtime")
        assert _matches(selector, runtime["spec"]["template"]["metadata"]["labels"])
        api = _find(docs, "Deployment", _fullname())
        assert not _matches(selector, api["spec"]["template"]["metadata"]["labels"])

    def test_services_exclude_worker(self) -> None:
        """S-02：两 Service 均排除 workflow-worker Pod；Worker 保持自身角色。"""
        docs = _render()
        worker = _find(docs, "Deployment", f"{_fullname()}-workflow-worker")
        worker_labels = worker["spec"]["template"]["metadata"]["labels"]
        assert worker_labels.get("app.kubernetes.io/component") == "workflow-worker"
        for name in (_fullname(), f"{_fullname()}-runtime"):
            selector = _find(docs, "Service", name)["spec"]["selector"]
            assert not _matches(selector, worker_labels), f"{name} 误选 Worker"

    def test_api_pods_carry_api_component_label(self) -> None:
        """S-02：API Deployment 的 selector 与 Pod template 均带 component=api。"""
        docs = _render()
        api = _find(docs, "Deployment", _fullname())
        match = api["spec"]["selector"]["matchLabels"]
        template_labels = api["spec"]["template"]["metadata"]["labels"]
        assert match.get("app.kubernetes.io/component") == "api"
        assert template_labels.get("app.kubernetes.io/component") == "api"
        assert _matches(match, template_labels)

    def test_custom_release_fullname_consistency(self) -> None:
        """S-02：自定义 release/fullname 下 Service 名、selector 与注入 URL 一致。"""
        docs = _render("custom", ["--set", "fullnameOverride=myflux"])
        assert _find(docs, "Service", "myflux")
        assert _find(docs, "Service", "myflux-runtime")
        api = _find(docs, "Deployment", "myflux")
        containers = api["spec"]["template"]["spec"]["containers"]
        env = {item["name"]: item.get("value") for c in containers for item in c.get("env", [])}
        assert env.get("FLUXION_RUNTIME_SERVICE_URL") == "http://myflux-runtime:8000"

    def test_runtime_zero_replicas_allows_empty_service(self) -> None:
        """S-02：Runtime 副本 0 时 Service 无 endpoint（Deployment 不渲染），
        远程请求按 E-01 失败；主 Service 不自动选 Runtime Pod。"""
        docs = _render(extra=["--set", "runtime.replicaCount=0"])
        kinds = {(d.get("kind"), d.get("metadata", {}).get("name")) for d in docs}
        assert ("Deployment", f"{_fullname()}-runtime") not in kinds
        service = _find(docs, "Service", f"{_fullname()}-runtime")
        assert service["spec"]["selector"].get("app.kubernetes.io/component") == "runtime"
        main_selector = _find(docs, "Service", _fullname())["spec"]["selector"]
        assert main_selector.get("app.kubernetes.io/component") == "api"

    def test_upgrade_notes_document_staged_rollout(self) -> None:
        """S-02：旧版本升级路径有文档（selector 不可原地变更→分阶段/替代部署）。"""
        notes = Path(_CHART) / "templates" / "NOTES.txt"
        assert notes.is_file(), "缺升级说明 NOTES.txt"
        text = notes.read_text(encoding="utf-8")
        assert "component" in text and "api" in text
        assert "selector" in text.lower() or "Selector" in text

    @pytest.mark.skipif(not _k8s_enabled, reason="FLUXION_K8S_TEST=1 未设置")
    @pytest.mark.skipif(not _kubectl_available, reason="kubectl 不可用")
    def test_live_endpointslice_isolation_selfcontained(self) -> None:
        """S-02[K8s]：真实集群 EndpointSlice 按角色隔离（自包含 fixtures）。

        在独立命名空间应用渲染出的两 Service，并按渲染出的 Pod 标签创建
        api/runtime/worker 三 Pod（含具名 http 端口），验证 EndpointSlice
        地址归属。无镜像/无网络导致 Pod 不 Ready 时 skip，不伪造 GREEN。
        """
        import json
        import uuid

        tag = uuid.uuid4().hex[:8]
        namespace = f"s02-{tag}"
        release = "s02t"
        fullname = f"s02t-flux-{tag}"
        docs = _render(release, ["--set", f"fullnameOverride={fullname}"])
        services = [d for d in docs if d.get("kind") == "Service"]
        assert len(services) == 2
        roles: dict[str, dict[str, str]] = {}
        for deploy_suffix, role in (("", "api"), ("-runtime", "runtime"),
                                    ("-workflow-worker", "worker")):
            deploy = next(
                d for d in docs
                if d.get("kind") == "Deployment"
                and d.get("metadata", {}).get("name") == f"{fullname}{deploy_suffix}"
            )
            roles[role] = dict(deploy["spec"]["template"]["metadata"]["labels"])

        def _kubectl_ns(*args: str, timeout_s: float = 60.0) -> str:
            result = subprocess.run(
                ["kubectl", "-n", namespace, *args],
                capture_output=True, text=True, timeout=timeout_s, check=False,
            )
            if result.returncode != 0:
                raise AssertionError(
                    f"kubectl {' '.join(args)} 失败: {result.stderr.strip()}"
                )
            return result.stdout.strip()

        subprocess.run(["kubectl", "create", "namespace", namespace],
                       capture_output=True, text=True, timeout=60, check=False)
        try:
            for service in services:
                manifest = yaml.safe_dump(service)
                result = subprocess.run(
                    ["kubectl", "-n", namespace, "apply", "-f", "-"],
                    input=manifest, capture_output=True, text=True,
                    timeout=60, check=False,
                )
                assert result.returncode == 0, f"Service 应用失败: {result.stderr.strip()}"
            for role, labels in roles.items():
                pod = {
                    "apiVersion": "v1", "kind": "Pod",
                    "metadata": {"name": f"s02-{role}-{tag}", "labels": labels},
                    "spec": {"containers": [{
                        "name": "web", "image": "nginx:alpine",
                        "ports": [{"name": "http", "containerPort": 8000}],
                    }]},
                }
                result = subprocess.run(
                    ["kubectl", "-n", namespace, "apply", "-f", "-"],
                    input=yaml.safe_dump(pod), capture_output=True, text=True,
                    timeout=60, check=False,
                )
                assert result.returncode == 0, f"Pod 应用失败: {result.stderr.strip()}"
            waited = subprocess.run(
                ["kubectl", "-n", namespace, "wait",
                 "--for=condition=Ready", "pod", "--all", "--timeout=150s"],
                capture_output=True, text=True, timeout=180, check=False,
            )
            if waited.returncode != 0:
                describe = _kubectl_ns("describe", "pods")
                if "ImagePullBackOff" in describe or "ErrImagePull" in describe:
                    pytest.skip("Pod 镜像拉取失败，live 腿未验证")
                raise AssertionError(f"Pod 未就绪: {waited.stderr.strip()}")

            pod_ip_role: dict[str, str] = {}
            pods = json.loads(_kubectl_ns("get", "pods", "-o", "json"))["items"]
            for item in pods:
                name = item["metadata"]["name"]
                role = next(r for r in roles if f"s02-{r}-{tag}" == name)
                pod_ip_role[item["status"]["podIP"]] = role

            for service in services:
                name = service["metadata"]["name"]
                expected = "api" if name == fullname else "runtime"
                output = subprocess.run(
                    ["kubectl", "-n", namespace, "get", "endpointslices",
                     "-l", f"kubernetes.io/service-name={name}", "-o", "json"],
                    capture_output=True, text=True, timeout=60, check=False,
                )
                assert output.returncode == 0
                slices = json.loads(output.stdout)["items"]
                assert slices, f"{name} 无 EndpointSlice"
                seen: set[str] = set()
                for item in slices:
                    for endpoint in item.get("endpoints", []):
                        for address in endpoint.get("addresses", []):
                            seen.add(pod_ip_role.get(address, f"unknown:{address}"))
                assert seen == {expected}, f"{name} endpoint 隔离失败: {seen}"
        finally:
            subprocess.run(["kubectl", "delete", "namespace", namespace, "--wait=true"],
                           capture_output=True, text=True, timeout=120, check=False)
