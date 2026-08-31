from __future__ import annotations

import pytest
from tests.console_helpers import console_stack, create_resource, publish_resource

from fluxion.resources import ResourceKind


@pytest.mark.asyncio
async def test_S_P13_06_console_publishes_model_provider_plugin_spec() -> None:
    # 回归：RegistryOpenAIModelProvider 按 ProviderDefinition 形状读取 plugin 资源
    # （plugin_type/protocol/base_url/model），而 console 发布校验之前误用了
    # PluginDefinition（name/package/trust_level），导致产品 golden path 无法发布插件。
    async with console_stack() as stack:
        created = await create_resource(
            stack.client,
            kind=ResourceKind.PLUGIN,
            resource_id="browser-provider",
            spec={
                "plugin_type": "model_provider",
                "protocol": "openai_compatible",
                "base_url": "http://127.0.0.1:9878/v1",
                "model": "browser-model",
                "request_timeout_ms": 3000,
                "max_retries": 0,
            },
        )
        published = await publish_resource(
            stack.client,
            kind=ResourceKind.PLUGIN,
            resource_id="browser-provider",
        )

    assert created.status_code == 200
    assert published.status_code == 200
    assert published.json()["data"]["status"] == "published"


@pytest.mark.asyncio
async def test_E_P13_06_console_rejects_non_model_provider_plugin_spec() -> None:
    async with console_stack() as stack:
        created = await create_resource(
            stack.client,
            kind=ResourceKind.PLUGIN,
            resource_id="package-plugin",
            spec={"name": "package-plugin", "package": "x", "trust_level": "trusted"},
        )
        published = await publish_resource(
            stack.client,
            kind=ResourceKind.PLUGIN,
            resource_id="package-plugin",
        )

    assert created.status_code == 200
    assert published.status_code == 400
    payload = published.json()
    assert payload["code"] != 0
    assert "Extra inputs are not permitted" in payload["message"]
