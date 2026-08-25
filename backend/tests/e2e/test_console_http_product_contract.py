from __future__ import annotations

from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient, Response

from fluxion.api.console import create_app
from fluxion.config import DevModeSettings
from fluxion.registry import SQLiteRegistryStore
from fluxion.resources import ExecutionSnapshot
from fluxion.runtime.context import TraceEvent
from fluxion.runtime.secrets import LocalEncryptedSecretStore
from fluxion.runtime.tracing import InMemoryTraceStore, TraceRecord
from fluxion.services.console_app import ConsoleApplicationService


@pytest.mark.asyncio
async def test_S_P13_05_console_http_contract_supports_real_ui_operations() -> None:
    store = SQLiteRegistryStore("sqlite+aiosqlite:///:memory:")
    traces = InMemoryTraceStore()
    await traces.append(_trace_record())
    secrets = LocalEncryptedSecretStore(master_key=b"s" * 32)
    await secrets.put("dev", "review", "not-returned")
    service = ConsoleApplicationService(
        store,
        trace_store=traces,
        secret_metadata_store=secrets,
    )
    await store.initialize()
    try:
        async with AsyncClient(
            transport=ASGITransport(
                app=create_app(service, dev_mode=DevModeSettings(enabled=True))
            ),
            base_url="http://console",
        ) as client:
            profile = await _create_resource(
                client,
                "runtime_profile",
                "assistant",
                {"prompt": "help", "model_policy": {"provider": "dev.echo"}},
            )
            exact = await client.get(
                "/api/v1/resources/runtime_profile/assistant?version=v1"
            )
            validated = await client.post(
                "/api/v1/resources/runtime_profile/assistant/versions/v1:validate",
                json={},
            )
            await _publish(client, "runtime_profile", "assistant")
            skill = await _create_resource(
                client,
                "skill",
                "review",
                {"name": "review", "instructions": "review carefully"},
            )
            await _publish(client, "skill", "review")
            await _create_resource(
                client,
                "mcp",
                "invalid-mcp",
                {"name": "invalid", "transport": "streamable_http"},
            )
            invalid_validation = await client.post(
                "/api/v1/resources/mcp/invalid-mcp/versions/v1:validate",
                json={},
            )
            invalid_publish = await client.post(
                "/api/v1/resources/mcp/invalid-mcp/versions/v1:publish",
                json={},
            )
            binding = await client.post(
                "/api/v1/bindings",
                json={
                    "subject_type": "user",
                    "subject_id": "user-a",
                    "resource_type": "skill",
                    "resource_id": "review",
                    "credential_ref": "secret://dev/review@1",
                },
            )
            listed = await client.get("/api/v1/bindings?page=1&page_size=100")
            binding_id = binding.json()["data"]["binding_id"]
            disabled = await client.post(f"/api/v1/bindings/{binding_id}:disable")
            listed_after = await client.get("/api/v1/bindings?page=1&page_size=100")
            credentials = await client.get("/api/v1/credentials?page=1&page_size=100")
            runs = await client.get("/api/v1/runs?page=1&page_size=100")
            run = await client.get("/api/v1/runs/execution-console")
            audit = await client.get("/api/v1/audit?page=1&page_size=100")
            all_resources = await client.get("/api/v1/resources?page=1&page_size=100")
            filtered_skill = await client.get(
                "/api/v1/resources?resource_type=skill&page=1&page_size=100"
            )
            filtered_binding = await client.get(
                "/api/v1/bindings?resource_type=skill&page=1&page_size=100"
            )
            empty_binding = await client.get(
                "/api/v1/bindings?resource_type=mcp&page=1&page_size=100"
            )
            invalid_binding_type = await client.get("/api/v1/bindings?resource_type=bogus")

        assert profile.json()["data"]["tenant_id"] == "dev"
        assert profile.json()["data"]["updated_at"]
        assert exact.json()["data"]["version"] == "v1"
        assert validated.json()["data"] == {"diagnostics": ["校验通过"], "valid": True}
        assert skill.status_code == 200
        assert invalid_validation.json()["data"]["valid"] is False
        assert invalid_publish.status_code == 400
        assert listed.json()["data"]["total"] == 1
        assert listed.json()["data"]["items"][0]["enabled"] is True
        assert disabled.json()["data"] == {"binding_id": binding_id, "status": "disabled"}
        assert listed_after.json()["data"]["items"][0]["enabled"] is False
        assert credentials.json()["data"]["items"][0]["credential_ref"] == (
            "secret://dev/review@1"
        )
        assert runs.json()["data"]["items"][0]["execution_id"] == "execution-console"
        assert run.json()["data"]["trace_events"][0]["event"] == "runtime.started"
        assert audit.json()["data"]["total"] >= 4
        assert all("token" not in str(item) for item in audit.json()["data"]["items"])
        # 单表 resource_definitions，一个 GET /api/v1/resources 返回全部类型（已发布的）。
        assert all_resources.json()["data"]["total"] == 2
        assert {item["resource_type"] for item in all_resources.json()["data"]["items"]} == {
            "runtime_profile",
            "skill",
        }
        assert [item["resource_id"] for item in filtered_skill.json()["data"]["items"]] == ["review"]
        # 绑定列表同样支持 resource_type 过滤，非法类型返回 400。
        assert filtered_binding.json()["data"]["total"] == 1
        assert [item["resource_type"] for item in filtered_binding.json()["data"]["items"]] == ["skill"]
        assert empty_binding.json()["data"]["total"] == 0
        assert invalid_binding_type.status_code == 400
    finally:
        await store.close()


async def _create_resource(
    client: AsyncClient,
    resource_type: str,
    resource_id: str,
    spec: dict[str, object],
) -> Response:
    return await client.post(
        f"/api/v1/resources/{resource_type}",
        json={
            "resource_id": resource_id,
            "version": "v1",
            "spec": spec,
            "visibility": "private",
        },
    )


async def _publish(client: AsyncClient, resource_type: str, resource_id: str) -> None:
    response = await client.post(
        f"/api/v1/resources/{resource_type}/{resource_id}/versions/v1:publish",
        json={},
    )
    assert response.status_code == 200


def _trace_record() -> TraceRecord:
    created_at = datetime(2026, 8, 24, tzinfo=UTC)
    snapshot = ExecutionSnapshot(
        execution_id="execution-console",
        tenant_id="dev",
        user_id="user-a",
        runtime_profile_id="assistant",
        runtime_profile_version="v1",
        model_resolution={"provider": "fixture"},
        trace_id="trace-console",
        skill_versions={"review": "v1"},
        mcp_versions={"weather": "v1"},
        policy_version="policy-v1",
        created_at=created_at,
    )
    event = TraceEvent(
        name="runtime.started",
        tenant_id="dev",
        execution_id=snapshot.execution_id,
        trace_id=snapshot.trace_id,
    )
    return TraceRecord(
        trace_id=snapshot.trace_id,
        execution_id=snapshot.execution_id,
        tenant_id=snapshot.tenant_id,
        runtime_profile_id=snapshot.runtime_profile_id,
        runtime_profile_version=snapshot.runtime_profile_version,
        snapshot=snapshot,
        events=(event,),
        latency_ms=12.0,
        error=None,
    )
