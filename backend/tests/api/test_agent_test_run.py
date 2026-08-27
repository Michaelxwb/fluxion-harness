"""TASK-005 Agent test-run SSE 验收测试。

- BE-E-03（integration）：provider 不可达时 test-run 必须有界失败——有限 retry、
  失败 SSE 帧（event: error）收束连接，总时长不超过 deadline 上限。
- BE-E-04（integration）：失败链路结构化日志含 request_id/trace_id 关联字段，
  且全程不含 SecretStore 内的凭据明文。

真实边界：本地 Docker-free 方案使用 127.0.0.1 上必然关闭的端口制造
ConnectError，经 RegistryOpenAIModelProvider → AgentRuntime 完整执行链，
无任何 mock。
"""

from __future__ import annotations

import logging
import time

import pytest
from httpx import ASGITransport, AsyncClient
from tests.console_helpers import console_stack, tenant_headers

from fluxion.api.console import create_app as create_console_app
from fluxion.registry import RegistryStore, SQLiteRegistryStore
from fluxion.resources import (
    ResourceBinding,
    ResourceDefinition,
    ResourceKind,
    ResourceStatus,
    ResourceVisibility,
    SubjectType,
)
from fluxion.runtime.secrets import CredentialResolver, LocalEncryptedSecretStore
from fluxion.services.console_app import ConsoleApplicationService
from fluxion.services.runtime_app import RuntimeApplicationService

DEAD_BASE_URL = "http://127.0.0.1:9/v1"  # discard 协议端口，本机必关
SECRET_PLAINTEXT = "sk-live-TASK005-sentinel"


def _dual_stack(
    store: SQLiteRegistryStore,
) -> tuple[AsyncClient, ConsoleApplicationService]:
    runtime = RuntimeApplicationService.create_dev_bundle(
        store,
        credential_resolver=CredentialResolver(LocalEncryptedSecretStore(master_key=b"m" * 32)),
    )
    service = ConsoleApplicationService(store)
    app = create_console_app(service, runtime_service=runtime)
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://console")
    return client, service


async def _seed_test_run_product(store: RegistryStore) -> None:
    # mechanics profile + 指向必关端口 provider 的同名 fixture agent。
    from tests.runtime_helpers import publish_resource, seed_agent_definition

    await publish_resource(
        store,
        tenant_id="tenant-a",
        kind=ResourceKind.RUNTIME_PROFILE,
        resource_id="assistant",
        version="1",
        spec={"request_timeout_ms": 30_000, "max_retries": 1},
    )
    await seed_agent_definition(store, provider_id="dead", system_prompt="你是测试代理。")

    await store.put(
        ResourceDefinition(
            tenant_id="tenant-a",
            kind=ResourceKind.PLUGIN,
            id="dead",
            version="1",
            status=ResourceStatus.DRAFT,
            visibility=ResourceVisibility.PRIVATE,
            spec_json={
                "plugin_type": "model_provider",
                "protocol": "openai_compatible",
                "base_url": DEAD_BASE_URL,
                "model": "boom",
                "request_timeout_ms": 100,
                "max_retries": 1,
                "credential_ref": "secret://tenant-a/dead-key",
            },
        )
    )
    await store.publish(ResourceKind.PLUGIN, "dead", tenant_id="tenant-a", version="1")
    binding = ResourceBinding(
        binding_id="binding-dead-key",
        tenant_id="tenant-a",
        subject_type=SubjectType.USER,
        subject_id="studio-test:admin-a",
        resource_type=ResourceKind.PLUGIN,
        resource_id="dead",
        resource_version_selector="1",
        credential_ref="secret://tenant-a/dead-key",
        enabled=True,
    )
    await store.put_binding(binding)


@pytest.mark.asyncio
async def test_be_e_03_test_run_fails_bounded_on_unreachable_provider() -> None:
    async with console_stack() as stack:
        store = stack.store
        await stack.service.initialize()
        await _seed_test_run_product(store)
        runtime = RuntimeApplicationService.create_dev_bundle(
            store,
            credential_resolver=CredentialResolver(LocalEncryptedSecretStore(master_key=b"m" * 32)),
        )
        app = create_console_app(stack.service, runtime_service=runtime)
        from httpx import AsyncClient as AC

        async with AC(transport=ASGITransport(app=app), base_url="http://console") as client:
            started = time.monotonic()
            response = await client.post(
                "/studio/agents/assistant/test-run",
                json={"input": "任意输入"},
                headers=tenant_headers(request_id="req-be-e03"),
            )
            elapsed = time.monotonic() - started

        # 有界失败：SSE 收束且不超时限（connect refused 应秒级；deadline 兜底）。
        assert elapsed < 30.0, f"test-run 未有界收束：{elapsed:.1f}s"
        assert "event:" in response.text  # SSE 帧结构存在
        assert "error" in response.text.lower() or "failed" in response.text.lower()
        await runtime.close()


@pytest.mark.asyncio
async def test_be_e_04_no_secret_in_error_logs_ids_present(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async with console_stack() as stack:
        store = stack.store
        await stack.service.initialize()
        secrets = LocalEncryptedSecretStore(master_key=b"m" * 32)
        await secrets.put("tenant-a", "dead-key", SECRET_PLAINTEXT)
        await _seed_test_run_product(store)
        runtime = RuntimeApplicationService.create_dev_bundle(
            store,
            credential_resolver=CredentialResolver(secrets),
        )
        app = create_console_app(stack.service, runtime_service=runtime)
        from httpx import AsyncClient as AC

        with caplog.at_level(logging.INFO):
            async with AC(transport=ASGITransport(app=app), base_url="http://console") as client:
                await client.post(
                    "/studio/agents/assistant/test-run",
                    json={"input": "带敏感场景的输入"},
                    headers=tenant_headers(request_id="req-be-e04-trace"),
                )
        await runtime.close()

        joined = "\n".join(str(rec.getMessage()) for rec in caplog.records)
        joined += "\n".join(
            str(getattr(rec, "args", "")) for rec in caplog.records
        )
        # 关联字段在日志链路中可见。
        assert "req-be-e04-trace" in joined, "日志缺少 request_id 关联"
        # 凭据明文哨兵不得泄漏到任何日志记录。
        assert SECRET_PLAINTEXT not in joined, "凭据明文泄漏进日志"
