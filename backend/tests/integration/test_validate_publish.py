"""remediation §14.4（TASK-009）：发布完整校验，返回可操作问题清单。

- B-S-04：合法配置 validate-publish → valid=true，发布成功。
- B-E-02：凭据不可用 → 返回「凭据 X 不可用」可操作问题。
"""

from __future__ import annotations

import pytest

from fluxion.resources import ResourceKind
from tests.console_helpers import console_stack, create_resource, publish_resource, tenant_headers


def _provider_spec(credential_ref: str) -> dict[str, object]:
    return {
        "protocol": "openai-compatible",
        "base_url": "https://api.deepseek.com",
        "credential_ref": credential_ref,
        "default_model": "deepseek-chat",
        "request_timeout_ms": 60_000,
        "max_retries": 1,
    }


@pytest.mark.asyncio
async def test_B_S04_validate_publish_valid_model_provider() -> None:
    """合法 ProviderDefinition（凭据已定义）→ valid=true，无问题，可发布。"""
    async with console_stack() as stack:
        await create_resource(
            stack.client,
            kind=ResourceKind.SECRET,
            resource_id="openai",
            version="1",
            spec={"name": "openai", "secret_ref": "secret://tenant-a/openai", "purpose": "llm"},
        )
        await create_resource(
            stack.client,
            kind=ResourceKind.MODEL_PROVIDER,
            resource_id="prov-a",
            version="1",
            spec=_provider_spec("secret://tenant-a/openai"),
        )
        resp = await stack.client.post(
            "/api/v1/resources/model_provider/prov-a/versions/1:validate-publish",
            headers=tenant_headers(),
        )
        published = await publish_resource(
            stack.client,
            kind=ResourceKind.MODEL_PROVIDER,
            resource_id="prov-a",
            version="1",
            expected_base_version=None,
        )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["valid"] is True
    assert data["issues"] == []
    assert published.status_code == 200
    assert published.json()["data"]["status"] == "published"


@pytest.mark.asyncio
async def test_B_E02_validate_publish_credential_unavailable() -> None:
    """引用未定义的凭据 → 返回可操作问题「凭据 ... 不可用」。"""
    async with console_stack() as stack:
        await create_resource(
            stack.client,
            kind=ResourceKind.MODEL_PROVIDER,
            resource_id="prov-missing",
            version="1",
            spec=_provider_spec("secret://tenant-a/ghost"),
        )
        resp = await stack.client.post(
            "/api/v1/resources/model_provider/prov-missing/versions/1:validate-publish",
            headers=tenant_headers(),
        )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["valid"] is False
    assert any("ghost" in issue and "不可用" in issue for issue in data["issues"])


@pytest.mark.asyncio
async def test_B_E02_validate_publish_credential_available_passes() -> None:
    """凭据已定义为 SECRET 资源 → 无凭据问题。"""
    async with console_stack() as stack:
        await create_resource(
            stack.client,
            kind=ResourceKind.SECRET,
            resource_id="openai",
            version="1",
            spec={"name": "openai", "secret_ref": "secret://tenant-a/openai", "purpose": "llm"},
        )
        await create_resource(
            stack.client,
            kind=ResourceKind.MODEL_PROVIDER,
            resource_id="prov-a",
            version="1",
            spec=_provider_spec("secret://tenant-a/openai"),
        )
        resp = await stack.client.post(
            "/api/v1/resources/model_provider/prov-a/versions/1:validate-publish",
            headers=tenant_headers(),
        )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["valid"] is True
    assert data["issues"] == []
