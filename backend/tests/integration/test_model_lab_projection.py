"""golden-path-closure TASK-025 / F-S-19：Model 页聚合投影契约（integration）。

- `GET /studio/model-lab/projection`：单请求返回 providers/models/credentials
  聚合结构（消费方 ModelsPage 由 3+3N 请求降为 1 请求）。
- 网络请求数常数断言：Playwright 侧（frontend/e2e/model-projection.spec.ts）。

真实边界：真实 PG RegistryStore + Console HTTP ASGI，无 mock Store。
"""

from __future__ import annotations

import pytest

from tests.console_helpers import console_stack, tenant_headers

H = tenant_headers(request_id="req-fs19")


@pytest.mark.asyncio
async def test_fs19_model_lab_projection_single_request_aggregate() -> None:
    """投影端点：一次请求返回 Provider→Model 分组 + 凭据选项聚合结构。"""
    async with console_stack() as stack:
        credential = await stack.client.post(
            "/api/v1/credentials",
            json={"name": "fs19-key", "secret": "sk-fs19", "purpose": "模型供应商"},
            headers=tenant_headers(request_id="req-fs19-cred"),
        )
        assert credential.status_code == 200, credential.text
        secret_ref = credential.json()["data"]["spec"]["secret_ref"]

        provider = await stack.client.post(
            "/api/v1/resources/model_provider",
            json={
                "resource_id": "fs19-provider",
                "version": "1",
                "spec": {
                    "protocol": "openai-compatible",
                    "base_url": "https://fs19.invalid/v1",
                    "credential_ref": secret_ref,
                    "default_model": "echo",
                    "request_timeout_ms": 3000,
                    "max_retries": 0,
                },
            },
            headers=H,
        )
        assert provider.status_code == 200, provider.text
        model = await stack.client.post(
            "/api/v1/resources/model_definition",
            json={
                "resource_id": "fs19-model",
                "version": "1",
                "spec": {
                    "name": "echo",
                    "provider_ref": {"id": "fs19-provider", "version": "1"},
                },
            },
            headers=H,
        )
        assert model.status_code == 200, model.text

        projection = await stack.client.get(
            "/studio/model-lab/projection",
            headers=H,
        )
        assert projection.status_code == 200, projection.text
        body = projection.json()
        assert body["code"] == 0
        data = body["data"]
        # 聚合结构：providers / models / credentials 三段齐备
        assert {item["resource_id"] for item in data["providers"]} >= {"fs19-provider"}
        provider_entry = next(
            item for item in data["providers"] if item["resource_id"] == "fs19-provider"
        )
        assert provider_entry["base_url"] == "https://fs19.invalid/v1"
        assert provider_entry["credential_ref"] == secret_ref
        model_entry = next(item for item in data["models"] if item["resource_id"] == "fs19-model")
        assert model_entry["provider_id"] == "fs19-provider"
        assert model_entry["name"] == "echo"
        assert any(item["value"] == secret_ref for item in data["credentials"])
