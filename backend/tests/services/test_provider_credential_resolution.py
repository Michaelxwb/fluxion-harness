"""TASK-003（golden-path-closure）Provider Credential 真相源统一验收测试（ADR-A008 amend）。

- B-E-02（integration）：链上无可解析 credential → fail-closed，无 `api_key=None` 出站；
- B-S-03（integration）：override 优先级 User > Tenant > spec；binding 仅 override 不构成运行前提；
- B-E-03（integration）：MODEL_PROVIDER Binding 逻辑 ID 匹配（跨版本继承），`resource_version_selector` 不参与；
- B-S-02（E2E）：仅配置 ProviderDefinition.credential_ref（无任何 binding）→ 模型调用成功。

真实边界：真实 PG RegistryStore + LocalEncryptedSecretStore + 本地 OpenAI-compatible
stub server（`tests/product_wire`）；不 mock Store/Resolver/Provider。
"""

from __future__ import annotations

import pytest

from fluxion.registry import RegistryStore
from fluxion.resources import ResourceBinding, ResourceKind
from fluxion.runtime.secrets import CredentialResolver, LocalEncryptedSecretStore
from fluxion.services.runtime_app import RuntimeApplicationService
from fluxion.services.runtime_contracts import RunRuntimeRequest
from tests.product_wire import openai_final_response, openai_wire_server
from tests.runtime_helpers import publish_resource, seed_model_definition

TENANT = "tenant-a"


def _runtime(
    store: RegistryStore, secrets: LocalEncryptedSecretStore
) -> RuntimeApplicationService:
    return RuntimeApplicationService(
        store, credential_resolver=CredentialResolver(secrets)
    )


async def _seed_provider(
    store: RegistryStore,
    *,
    provider_id: str,
    base_url: str,
    credential_ref: str,
) -> None:
    await publish_resource(
        store,
        tenant_id=TENANT,
        kind=ResourceKind.MODEL_PROVIDER,
        resource_id=provider_id,
        version="1",
        spec={
            "protocol": "openai-compatible",
            "base_url": base_url,
            "credential_ref": credential_ref,
            "default_model": "wire-model",
            "request_timeout_ms": 2_000,
            "max_retries": 0,
        },
    )


async def _seed_agent_chain(
    store: RegistryStore, *, provider_id: str, base_url: str, credential_ref: str
) -> None:
    """默认链 + agent.model_policy → ModelDefinition → ProviderDefinition。"""
    await _seed_provider(
        store, provider_id=provider_id, base_url=base_url, credential_ref=credential_ref
    )
    await publish_resource(
        store,
        tenant_id=TENANT,
        kind=ResourceKind.RUNTIME_PROFILE,
        resource_id="assistant",
        version="1",
        spec={"request_timeout_ms": 30_000, "max_retries": 1, "default": True},
    )
    await seed_model_definition(store, tenant_id=TENANT, provider_id=provider_id)
    await publish_resource(
        store,
        tenant_id=TENANT,
        kind=ResourceKind.AGENT_DEFINITION,
        resource_id="assistant",
        version="1",
        spec={
            "name": "assistant",
            "system_prompt": "p",
            "owner": "fixture",
            "model_policy": {
                "primary_model_ref": {"id": f"model.{provider_id}", "version": "1"}
            },
        },
    )


def _run_request(user_id: str = "user-a") -> RunRuntimeRequest:
    return RunRuntimeRequest(
        tenant_id=TENANT,
        user_id=user_id,
        agent_definition_id="assistant",
        runtime_profile_id="assistant",
        session_id="session-a",
        input_message="hello",
    )


@pytest.mark.asyncio
async def test_be02_unresolvable_credential_fails_closed_without_outbound_call(
    pg_store: RegistryStore,
) -> None:
    """B-E-02：链上无可解析 credential → fail-closed，无 `api_key=None` 出站。

    spec.credential_ref 指向的 Secret 在 store 中不存在（凭据库空）→ resolve
    阶段即报错，stub server 零请求（证明未发起裸调）。
    """
    secrets = LocalEncryptedSecretStore(master_key=b"m" * 32)
    async with openai_wire_server([openai_final_response("unreachable")]) as wire:
        await _seed_agent_chain(
            pg_store,
            provider_id="wire-provider",
            base_url=wire.base_url,
            # 指向不存在的 Secret（凭据库空，resolve 必失败）
            credential_ref="secret://tenant-a/missing-cred",
        )
        runtime = _runtime(pg_store, secrets)
        with pytest.raises(Exception) as exc_info:
            await runtime.run(_run_request())
        assert "provider_credential_unresolvable" in str(exc_info.value)
        assert wire.requests == [], "fail-closed 后不得发起任何出站调用"
        assert wire.request_headers == []
        await runtime.close()


@pytest.mark.asyncio
async def test_bs03_override_priority_user_tenant_spec(pg_store: RegistryStore) -> None:
    """B-S-03：override 优先级 User > Tenant > spec；binding 不构成运行前提。"""
    secrets = LocalEncryptedSecretStore(master_key=b"m" * 32)
    spec_ref = await secrets.put(TENANT, "spec-cred", "spec-secret")
    tenant_ref = await secrets.put(TENANT, "tenant-cred", "tenant-secret")
    user_ref = await secrets.put(TENANT, "user-cred", "user-secret")

    async with openai_wire_server(
        [openai_final_response("user"), openai_final_response("tenant"), openai_final_response("spec")]
    ) as wire:
        await _seed_agent_chain(
            pg_store,
            provider_id="wire-provider",
            base_url=wire.base_url,
            credential_ref=spec_ref,
        )

        async def _put_binding(
            subject_type: str, subject_id: str, credential_ref: str
        ) -> None:
            await pg_store.put_binding(
                ResourceBinding(
                    binding_id=f"bind-{subject_type}-{subject_id}",
                    tenant_id=TENANT,
                    subject_type=subject_type,
                    subject_id=subject_id,
                    resource_type=ResourceKind.MODEL_PROVIDER,
                    resource_id="wire-provider",
                    resource_version_selector="1",
                    credential_ref=credential_ref,
                )
            )

        # 三层齐备 → User override 生效
        await _put_binding("tenant", TENANT, tenant_ref)
        await _put_binding("user", "user-a", user_ref)
        runtime = _runtime(pg_store, secrets)
        assert (await runtime.run(_run_request())).output == "user"
        assert wire.request_headers[0]["authorization"] == "Bearer user-secret"

        # 仅 Tenant + spec → Tenant override 生效
        runtime2 = _runtime(pg_store, secrets)
        result2 = await runtime2.run(_run_request(user_id="user-b"))
        assert result2.output == "tenant"
        assert wire.request_headers[1]["authorization"] == "Bearer tenant-secret"

        # 无任何 binding（仅 spec）→ spec 默认真相源
        # （user-c 无 user binding；tenant 有 binding 但 user 无——这里显式验证
        #  仅 spec：用独立 provider 避免 tenant binding 干扰）
        await _seed_provider(
            pg_store,
            provider_id="spec-only-provider",
            base_url=wire.base_url,
            credential_ref=spec_ref,
        )
        await seed_model_definition(
            pg_store, tenant_id=TENANT, provider_id="spec-only-provider"
        )
        await publish_resource(
            pg_store,
            tenant_id=TENANT,
            kind=ResourceKind.AGENT_DEFINITION,
            resource_id="spec-agent",
            version="1",
            spec={
                "name": "spec-agent",
                "system_prompt": "p",
                "owner": "fixture",
                "model_policy": {
                    "primary_model_ref": {
                        "id": "model.spec-only-provider",
                        "version": "1",
                    }
                },
            },
        )
        runtime3 = _runtime(pg_store, secrets)
        result3 = await runtime3.run(
            RunRuntimeRequest(
                tenant_id=TENANT,
                user_id="user-d",
                agent_definition_id="spec-agent",
                runtime_profile_id="assistant",
                session_id="session-spec",
                input_message="hello",
            )
        )
        assert result3.output == "spec"
        assert wire.request_headers[2]["authorization"] == "Bearer spec-secret"

        await runtime.close()
        await runtime2.close()
        await runtime3.close()


@pytest.mark.asyncio
async def test_be03_binding_matches_logical_id_ignoring_version_selector(
    pg_store: RegistryStore,
) -> None:
    """B-E-03：Binding 绑定逻辑 Provider ID 跨版本继承，selector 不参与匹配。

    provider 发布 v2（当前 published），binding 的 resource_version_selector
    仍填精确 "1"——运行时仍应命中 binding（逻辑 ID 匹配，版本不敏感）。
    """
    secrets = LocalEncryptedSecretStore(master_key=b"m" * 32)
    spec_ref = await secrets.put(TENANT, "spec-cred", "spec-secret")
    override_ref = await secrets.put(TENANT, "override-cred", "override-secret")

    async with openai_wire_server([openai_final_response("override")]) as wire:
        await _seed_provider(
            pg_store,
            provider_id="versioned-provider",
            base_url=wire.base_url,
            credential_ref=spec_ref,
        )
        # 发布 v2（当前 published 版本）
        await publish_resource(
            pg_store,
            tenant_id=TENANT,
            kind=ResourceKind.MODEL_PROVIDER,
            resource_id="versioned-provider",
            version="2",
            spec={
                "protocol": "openai-compatible",
                "base_url": wire.base_url,
                "credential_ref": spec_ref,
                "default_model": "wire-model",
                "request_timeout_ms": 2_000,
                "max_retries": 0,
            },
        )
        await publish_resource(
            pg_store,
            tenant_id=TENANT,
            kind=ResourceKind.RUNTIME_PROFILE,
            resource_id="assistant",
            version="1",
            spec={"request_timeout_ms": 30_000, "max_retries": 1, "default": True},
        )
        await seed_model_definition(
            pg_store, tenant_id=TENANT, provider_id="versioned-provider"
        )
        await publish_resource(
            pg_store,
            tenant_id=TENANT,
            kind=ResourceKind.AGENT_DEFINITION,
            resource_id="assistant",
            version="1",
            spec={
                "name": "assistant",
                "system_prompt": "p",
                "owner": "fixture",
                "model_policy": {
                    "primary_model_ref": {
                        "id": "model.versioned-provider",
                        "version": "1",
                    }
                },
            },
        )
        # binding 填精确版本 "1"，但 provider 当前 published 是 v2
        await pg_store.put_binding(
            ResourceBinding(
                binding_id="bind-versioned",
                tenant_id=TENANT,
                subject_type="user",
                subject_id="user-a",
                resource_type=ResourceKind.MODEL_PROVIDER,
                resource_id="versioned-provider",
                resource_version_selector="1",
                credential_ref=override_ref,
            )
        )
        runtime = _runtime(pg_store, secrets)
        result = await runtime.run(_run_request())
        # override 生效（binding 命中逻辑 ID），而非回退 spec
        assert result.output == "override"
        assert wire.request_headers[0]["authorization"] == "Bearer override-secret"
        await runtime.close()


@pytest.mark.asyncio
async def test_bs02_spec_credential_only_succeeds_without_binding(
    pg_store: RegistryStore,
) -> None:
    """B-S-02：仅配置 ProviderDefinition.credential_ref（无任何 binding）→ 成功。"""
    secrets = LocalEncryptedSecretStore(master_key=b"m" * 32)
    spec_ref = await secrets.put(TENANT, "spec-only", "spec-only-secret")

    async with openai_wire_server([openai_final_response("spec only answer")]) as wire:
        await _seed_agent_chain(
            pg_store,
            provider_id="wire-provider",
            base_url=wire.base_url,
            credential_ref=spec_ref,
        )
        # 显式断言：无任何 MODEL_PROVIDER binding
        bindings = await pg_store.list_bindings(
            subject_type="user",
            subject_id="user-a",
            tenant_id=TENANT,
            resource_type=ResourceKind.MODEL_PROVIDER,
        )
        tenant_bindings = await pg_store.list_bindings(
            subject_type="tenant",
            subject_id=TENANT,
            tenant_id=TENANT,
            resource_type=ResourceKind.MODEL_PROVIDER,
        )
        assert bindings == [] and tenant_bindings == []

        runtime = _runtime(pg_store, secrets)
        result = await runtime.run(_run_request())
        assert result.output == "spec only answer"
        assert wire.request_headers[0]["authorization"] == "Bearer spec-only-secret"
        await runtime.close()
