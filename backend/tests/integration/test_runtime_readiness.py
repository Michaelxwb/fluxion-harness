"""TASK-005: Runtime 健康检查验收（S-05/E-03）。

真实边界：
- 应用层：真实 RuntimeApplicationService + 真实 Runtime API（ASGITransport）
  + 真实 PostgreSQLRegistryStore；故障注入经“故障 Registry”（抛错/挂起），不断言
  mock——service.ready() 读路径真实执行，超时预算真实计时。
- Chart 层：真实 `helm template` 渲染断言探针参数。
- K8s 层（FLUXION_K8S_TEST=1）：真实集群自包含 fixtures——python:http 服务
  实现与 /readyz 同契约的 readiness 翻转端点，探针参数取自本 chart 渲染值，
  真实测量故障到摘流/恢复到接流时间（NFR-REL-01 方法学证据；应用行为由上两
  层覆盖）。
"""

from __future__ import annotations
from tests.runtime_helpers import TEST_POSTGRES_DSN

import asyncio
import os
import shutil
import socket
import subprocess
import time
import uuid
from pathlib import Path

import pytest
import yaml
from httpx import ASGITransport, AsyncClient

from fluxion.api.runtime import create_app as create_runtime_api_app
from fluxion.registry import PostgreSQLRegistryStore
from fluxion.registry.store import RegistryStoreError
from fluxion.resources import ResourceKind
from fluxion.services.runtime_app import RuntimeApplicationService

_CHART = str(Path(__file__).resolve().parents[3] / "deploy" / "helm" / "fluxion")
_MASTER_KEY = "dGVzdG1hc3RlcmtleXRlc3R0ZXN0MTIzNDU2Nzg="
_PG_LIKE_DSN = "postgresql+asyncpg://mmuser:mmuser@localhost:5432/fluxion_test"
_SECRET_SENTINEL = "sk-live-readiness-sentinel"

_k8s_enabled = os.environ.get("FLUXION_K8S_TEST") == "1"
_kubectl_available = shutil.which("kubectl") is not None


class _BrokenRegistry(PostgreSQLRegistryStore):
    """故障 Registry：读路径抛库错误（含 DSN/SQL 文本，验证脱敏）。"""

    async def get(self, kind: ResourceKind, resource_id: str, **kwargs: object):  # type: ignore[no-untyped-def]
        raise RegistryStoreError(
            f"connection failed dsn={_PG_LIKE_DSN} SELECT * FROM resource_definitions"
        )


class _HangingRegistry(PostgreSQLRegistryStore):
    """挂起 Registry：读路径永不返回（验证 readiness 预算内 503）。"""

    async def get(self, kind: ResourceKind, resource_id: str, **kwargs: object):  # type: ignore[no-untyped-def]
        await asyncio.sleep(30.0)


async def _healthy_service() -> RuntimeApplicationService:
    from fluxion.services.runtime_app import (
        CreateRuntimeProfileRequest,
        PublishRuntimeProfileRequest,
    )
    from tests.runtime_helpers import seed_agent_definition

    store = PostgreSQLRegistryStore(TEST_POSTGRES_DSN, reset_on_initialize=True)
    service = RuntimeApplicationService.create_dev_bundle(store)
    await service.initialize()
    await service.create_runtime_profile(
        CreateRuntimeProfileRequest(
            tenant_id="tenant-a",
            runtime_profile_id="assistant",
            version="1",
            default=True,
        )
    )
    await seed_agent_definition(store, provider_id="dev.echo", model_name="dev")
    await service.publish_runtime_profile(
        PublishRuntimeProfileRequest(
            tenant_id="tenant-a", runtime_profile_id="assistant", version="1"
        )
    )
    return service


def _render(extra: list[str] | None = None) -> list[dict]:
    cmd = ["helm", "template", "release-name", _CHART, "--set", f"secrets.masterKey={_MASTER_KEY}", *(extra or [])]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
    assert result.returncode == 0, f"helm template 失败: {result.stderr.strip()}"
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def _deployment(docs: list[dict], name: str) -> dict:
    for doc in docs:
        if doc.get("kind") == "Deployment" and doc.get("metadata", {}).get("name") == name:
            return doc
    raise AssertionError(f"未渲染出 Deployment/{name}")


def _container(deployment: dict) -> dict:
    return deployment["spec"]["template"]["spec"]["containers"][0]


class TestRuntimeProbesChart:
    def test_readiness_probe_uses_readyz_with_budget(self) -> None:
        """S-05：Runtime Deployment readiness 挂 /readyz，周期/超时/阈值符合设计。"""
        deployment = _deployment(_render(), "release-name-fluxion-runtime")
        readiness = _container(deployment)["readinessProbe"]
        assert readiness["httpGet"]["path"] == "/readyz"
        assert readiness["periodSeconds"] == 5
        assert readiness["timeoutSeconds"] == 2
        assert readiness["failureThreshold"] == 3
        assert readiness["initialDelaySeconds"] == 5

    def test_liveness_probe_ignores_registry(self) -> None:
        """S-05：liveness 挂 /healthz（进程存活语义，不依赖 Registry）。"""
        deployment = _deployment(_render(), "release-name-fluxion-runtime")
        liveness = _container(deployment)["livenessProbe"]
        assert liveness["httpGet"]["path"] == "/healthz"
        assert liveness["periodSeconds"] == 10
        assert liveness["timeoutSeconds"] == 2
        assert liveness["failureThreshold"] == 3

    def test_startup_probe_configurable(self) -> None:
        """S-05：慢启动 startupProbe 可配置（默认关闭，开启后渲染）。"""
        default = _container(_deployment(_render(), "release-name-fluxion-runtime"))
        assert "startupProbe" not in default
        enabled = _container(
            _deployment(_render(["--set", "runtime.startupProbe.enabled=true"]), "release-name-fluxion-runtime")
        )
        assert enabled["startupProbe"]["httpGet"]["path"] == "/readyz"


class TestS05ReadinessBehavior:
    @pytest.mark.asyncio
    async def test_readyz_200_when_healthy(self) -> None:
        """S-05：Registry 健康时 /readyz 200。"""
        service = await _healthy_service()
        try:
            app = create_runtime_api_app(service)
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://runtime") as client:
                response = await client.get("/readyz", headers={"X-Request-ID": "req-ready-ok"})
            assert response.status_code == 200
            assert response.json()["code"] == 0
        finally:
            await service.close()

    @pytest.mark.asyncio
    async def test_readyz_503_on_registry_failure_without_leak(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """S-05：Registry 故障 /readyz 503 摘流信号；响应不含 DSN/SQL/Secret。"""
        import logging

        store = _BrokenRegistry(TEST_POSTGRES_DSN)
        service = RuntimeApplicationService.create_dev_bundle(store)
        await service.initialize()
        try:
            app = create_runtime_api_app(service)
            with caplog.at_level(logging.ERROR):
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://runtime") as client:
                    response = await client.get("/readyz", headers={"X-Request-ID": "req-ready-fail"})
            assert response.status_code == 503
            assert _PG_LIKE_DSN not in response.text
            assert "SELECT" not in response.text
            assert _SECRET_SENTINEL not in response.text
            # E-03 日志关联：结构化日志含 request_id 且不带原始错误（脱敏）。
            logged = "\n".join(record.getMessage() for record in caplog.records)
            assert "req-ready-fail" in logged
            assert _PG_LIKE_DSN not in logged
        finally:
            await service.close()

    @pytest.mark.asyncio
    async def test_liveness_survives_registry_failure(self) -> None:
        """S-05：liveness 不因单纯依赖故障失败（/healthz 仍 200）。"""
        store = _BrokenRegistry(TEST_POSTGRES_DSN)
        service = RuntimeApplicationService.create_dev_bundle(store)
        await service.initialize()
        try:
            app = create_runtime_api_app(service)
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://runtime") as client:
                response = await client.get("/healthz")
            assert response.status_code == 200
        finally:
            await service.close()


class TestE03ReadinessTimeout:
    @pytest.mark.asyncio
    async def test_hanging_registry_returns_503_within_budget(self) -> None:
        """E-03：连接/查询挂起时预算内 503（默认 1s 检测预算 < 2s 探针超时）。"""
        store = _HangingRegistry(TEST_POSTGRES_DSN)
        service = RuntimeApplicationService.create_dev_bundle(store)
        await service.initialize()
        try:
            app = create_runtime_api_app(service)
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://runtime") as client:
                started = time.monotonic()
                response = await client.get("/readyz", headers={"X-Request-ID": "req-ready-hang"})
                elapsed = time.monotonic() - started
            assert response.status_code == 503
            assert elapsed < 2.0, f"readiness 超出探针超时预算：{elapsed:.2f}s"
        finally:
            await service.close()


_READINESS_SERVER = """
import http.server, os

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/healthz":
            body = b"ok"
        elif self.path == "/readyz":
            if os.path.exists("/tmp/unready"):
                self.send_response(503)
                self.end_headers()
                self.wfile.write(b"not ready")
                return
            body = b"ready"
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass

http.server.HTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
"""


@pytest.mark.skipif(not _k8s_enabled, reason="FLUXION_K8S_TEST=1 未设置")
@pytest.mark.skipif(not _kubectl_available, reason="kubectl 不可用")
class TestS05LiveEndpointIsolation:
    """S-05[K8s]：真实集群故障→摘流→恢复→接流时间（探针参数取自本 chart 渲染值）。"""

    def test_fault_to_unready_to_ready(self) -> None:
        tag = uuid.uuid4().hex[:8]
        namespace = f"s05-{tag}"
        deployment = _deployment(_render(), "release-name-fluxion-runtime")
        container = _container(deployment)
        readiness = container["readinessProbe"]
        period = int(readiness["periodSeconds"])
        threshold = int(readiness["failureThreshold"])
        timeout = int(readiness["timeoutSeconds"])

        def _kubectl(*args: str, timeout_s: float = 60.0) -> str:
            result = subprocess.run(
                ["kubectl", "-n", namespace, *args],
                capture_output=True, text=True, timeout=timeout_s, check=False,
            )
            if result.returncode != 0:
                raise AssertionError(f"kubectl {' '.join(args)} 失败: {result.stderr.strip()}")
            return result.stdout.strip()

        import json as _json

        subprocess.run(["kubectl", "create", "namespace", namespace], capture_output=True, text=True, timeout=60, check=False)
        try:
            configmap = {
                "apiVersion": "v1", "kind": "ConfigMap",
                "metadata": {"name": "s05-server"},
                "data": {"server.py": _READINESS_SERVER},
            }
            pod_spec = {
                "apiVersion": "apps/v1", "kind": "Deployment",
                "metadata": {"name": "s05-runtime"},
                "spec": {
                    "replicas": 1,
                    "selector": {"matchLabels": {"app": "s05-runtime"}},
                    "template": {
                        "metadata": {"labels": {"app": "s05-runtime"}},
                        "spec": {
                            "containers": [{
                                "name": "server", "image": "python:3.12-alpine",
                                "command": ["python", "/app/server.py"],
                                "ports": [{"name": "http", "containerPort": 8000}],
                                "readinessProbe": {
                                    "httpGet": {"path": "/readyz", "port": "http"},
                                    "periodSeconds": period,
                                    "timeoutSeconds": timeout,
                                    "failureThreshold": threshold,
                                },
                                "volumeMounts": [{"name": "app", "mountPath": "/app"}],
                            }],
                            "volumes": [{"name": "app", "configMap": {"name": "s05-server"}}],
                        },
                    },
                },
            }
            service = {
                "apiVersion": "v1", "kind": "Service",
                "metadata": {"name": "s05-runtime"},
                "spec": {
                    "type": "ClusterIP",
                    "ports": [{"port": 8000, "targetPort": "http", "name": "http"}],
                    "selector": {"app": "s05-runtime"},
                },
            }
            for manifest in (configmap, pod_spec, service):
                result = subprocess.run(
                    ["kubectl", "-n", namespace, "apply", "-f", "-"],
                    input=yaml.safe_dump(manifest), capture_output=True, text=True, timeout=60, check=False,
                )
                assert result.returncode == 0, f"apply 失败: {result.stderr.strip()}"
            waited = subprocess.run(
                ["kubectl", "-n", namespace, "wait", "--for=condition=available", "deployment/s05-runtime", "--timeout=150s"],
                capture_output=True, text=True, timeout=180, check=False,
            )
            if waited.returncode == 0:
                waited = subprocess.run(
                    ["kubectl", "-n", namespace, "wait", "--for=condition=Ready", "pod", "-l", "app=s05-runtime", "--timeout=150s"],
                    capture_output=True, text=True, timeout=180, check=False,
                )
            if waited.returncode != 0:
                describe = _kubectl("describe", "pods")
                if "ImagePullBackOff" in describe or "ErrImagePull" in describe:
                    pytest.skip("Pod 镜像拉取失败，live 腿未验证")
                raise AssertionError(f"Pod 未就绪: {waited.stderr.strip()}")

            def _addresses() -> list[str]:
                output = subprocess.run(
                    ["kubectl", "-n", namespace, "get", "endpointslices", "-o", "json"],
                    capture_output=True, text=True, timeout=60, check=False,
                )
                assert output.returncode == 0
                addresses = []
                for item in _json.loads(output.stdout)["items"]:
                    for endpoint in item.get("endpoints", []):
                        # K8s 语义：摘流 = ready 端点消失（unready 端点仍保留在
                        # slice 中但 kube-proxy 不再转发，必须按 conditions 过滤）。
                        if endpoint.get("conditions", {}).get("ready", False) is not True:
                            continue
                        addresses.extend(endpoint.get("addresses", []))
                return addresses

            deadline = time.monotonic() + 120.0
            while time.monotonic() < deadline and not _addresses():
                time.sleep(2)
            assert _addresses(), "初始 EndpointSlice 无地址"

            # 故障注入：翻转 readiness 失败。
            _kubectl("exec", "deploy/s05-runtime", "--", "touch", "/tmp/unready")
            fault_at = time.monotonic()
            removed_at: float | None = None
            deadline = time.monotonic() + 120.0
            while time.monotonic() < deadline:
                if not _addresses():
                    removed_at = time.monotonic()
                    break
                time.sleep(1)
            assert removed_at is not None, "故障后 EndpointSlice 未摘流"
            fault_to_unready = removed_at - fault_at
            # 探针周期×阈值 + EndpointSlice 传播 + 余量（真实测量，非算式证明）。
            assert fault_to_unready <= period * threshold + 30.0, f"摘流过慢：{fault_to_unready:.1f}s"

            # 恢复：删除标记 → 重新接流。
            _kubectl("exec", "deploy/s05-runtime", "--", "rm", "-f", "/tmp/unready")
            recovered_at = time.monotonic()
            readded_at: float | None = None
            deadline = time.monotonic() + 120.0
            while time.monotonic() < deadline:
                if _addresses():
                    readded_at = time.monotonic()
                    break
                time.sleep(1)
            assert readded_at is not None, "恢复后未重新接流"
            print(f"\nS-05 live: fault_to_unready={fault_to_unready:.1f}s "
                  f"recover_to_ready={readded_at - recovered_at:.1f}s "
                  f"(period={period}, threshold={threshold}, timeout={timeout})")
        finally:
            subprocess.run(["kubectl", "delete", "namespace", namespace, "--wait=true"],
                           capture_output=True, text=True, timeout=120, check=False)


def _socket_port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False
