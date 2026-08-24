from __future__ import annotations

import pytest
from tests.product_wire import openai_final_response, openai_wire_server
from tests.runtime_helpers import publish_resource

from fluxion.registry import RegistryStore
from fluxion.resources import ResourceBinding, ResourceKind
from fluxion.runtime.secrets import CredentialResolver, LocalEncryptedSecretStore
from fluxion.services.runtime_app import RuntimeApplicationService
from fluxion.services.runtime_contracts import RunRuntimeRequest


@pytest.mark.asyncio
async def test_S_P13_01_registry_provider_resolves_versioned_definition_and_credential(
    sqlite_store: RegistryStore,
) -> None:
    secrets = LocalEncryptedSecretStore(master_key=b"m" * 32)
    credential_ref = await secrets.put("tenant-a", "wire-model", "wire-secret")
    async with openai_wire_server([openai_final_response("registry provider answer")]) as wire:
        await publish_resource(
            sqlite_store,
            tenant_id="tenant-a",
            kind=ResourceKind.PLUGIN,
            resource_id="wire-provider",
            version="1",
            spec={
                "name": "wire-provider",
                "plugin_type": "model_provider",
                "protocol": "openai_compatible",
                "base_url": wire.base_url,
                "model": "wire-model",
                "request_timeout_ms": 2_000,
                "max_retries": 0,
            },
        )
        await publish_resource(
            sqlite_store,
            tenant_id="tenant-a",
            kind=ResourceKind.RUNTIME_PROFILE,
            resource_id="assistant",
            version="1",
            spec={
                "prompt": "Use the configured provider.",
                "model_policy": {"provider": "wire-provider", "model": "wire-model"},
                "plugin_bindings": ["wire-provider@1"],
            },
        )
        await sqlite_store.put_binding(
            ResourceBinding(
                binding_id="provider-binding",
                tenant_id="tenant-a",
                subject_type="user",
                subject_id="user-a",
                resource_type=ResourceKind.PLUGIN,
                resource_id="wire-provider",
                resource_version_selector="1",
                credential_ref=credential_ref,
            )
        )
        runtime = RuntimeApplicationService(
            sqlite_store,
            credential_resolver=CredentialResolver(secrets),
        )

        result = await runtime.run(
            RunRuntimeRequest(
                tenant_id="tenant-a",
                user_id="user-a",
                runtime_profile_id="assistant",
                session_id="session-a",
                input_message="hello",
            )
        )

        assert result.output == "registry provider answer"
        assert wire.request_headers[0]["authorization"] == "Bearer wire-secret"
        trace = await runtime.trace_store.get(result.trace_id)
        assert trace is not None
        assert trace.snapshot.plugin_versions == {"wire-provider": "1"}
        assert "wire-secret" not in repr(trace)
