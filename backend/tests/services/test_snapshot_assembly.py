"""TASK-005（golden-path-closure）B-E-04：composition root 注入验收。

- credential_resolver 注入 → snapshot.credential_versions 为真实解析版本（非占位 "1"）；
- memory_retriever 注入 → snapshot.memory_manifest 非 unavailable 占位。

真实边界：真实 SQLite RegistryStore + LocalEncryptedSecretStore + 真实
PersonalMemoryRetriever(PgVectorSemanticStore) + 真实 ContextResolver 十段管线。
"""

from __future__ import annotations

import pytest

from fluxion.memory.domain.personal_memory import PersonalMemoryRetriever
from fluxion.plugins.providers.pgvector_semantic import PgVectorSemanticStore
from fluxion.registry import RegistryStore
from fluxion.resources import ResourceBinding, ResourceKind
from fluxion.runtime.secrets import CredentialResolver, LocalEncryptedSecretStore
from fluxion.services.context_resolver import ContextResolver, ResolverSelector
from fluxion.services.runtime_app import RuntimeApplicationService
from fluxion.services.runtime_contracts import RunRuntimeRequest
from tests.runtime_helpers import publish_resource, seed_model_definition

TENANT = "tenant-a"


@pytest.mark.asyncio
async def test_be04_composition_root_injects_credential_and_memory(sqlite_store: RegistryStore) -> None:
    """B-E-04：注入 credential_resolver + memory_retriever 后，snapshot 的
    credential_versions 为真实版本、memory_manifest 非 unavailable 占位。"""
    secrets = LocalEncryptedSecretStore(master_key=b"m" * 32)
    credential_ref = await secrets.put(TENANT, "agent-cred", "agent-secret")
    # rotate 到 v2——credential_versions 必须解析出真实 version "2"（而非占位 "1"）
    credential_ref = await secrets.rotate(credential_ref, "agent-secret-v2")
    credential_resolver = CredentialResolver(secrets)

    memory_provider = PgVectorSemanticStore(sqlite_store.engine)
    await memory_provider.initialize()
    memory_retriever = PersonalMemoryRetriever(memory_provider)

    runtime = RuntimeApplicationService.create_dev_bundle(
        sqlite_store,
        credential_resolver=credential_resolver,
        memory_retriever=memory_retriever,
    )

    # seed 模型链 + 默认 runtime_profile + agent + user credential binding
    await publish_resource(
        sqlite_store,
        tenant_id=TENANT,
        kind=ResourceKind.RUNTIME_PROFILE,
        resource_id="assistant",
        version="1",
        spec={"request_timeout_ms": 30_000, "max_retries": 1, "default": True},
    )
    await seed_model_definition(sqlite_store, tenant_id=TENANT, provider_id="dev.echo")
    await publish_resource(
        sqlite_store,
        tenant_id=TENANT,
        kind=ResourceKind.AGENT_DEFINITION,
        resource_id="assistant",
        version="1",
        spec={
            "name": "assistant",
            "system_prompt": "p",
            "owner": "fixture",
            "model_policy": {
                "primary_model_ref": {"id": "model.dev.echo", "version": "1"}
            },
        },
    )
    # user credential binding（MODEL_PROVIDER 逻辑 ID 绑定；credential_ref 指向真实 Secret）
    await sqlite_store.put_binding(
        ResourceBinding(
            binding_id="bind-cred",
            tenant_id=TENANT,
            subject_type="user",
            subject_id="user-a",
            resource_type=ResourceKind.MODEL_PROVIDER,
            resource_id="dev.echo",
            credential_ref=credential_ref,
        )
    )

    result = await runtime.run(
        RunRuntimeRequest(
            tenant_id=TENANT,
            user_id="user-a",
            agent_definition_id="assistant",
            runtime_profile_id="assistant",
            session_id="session-be04",
            input_message="hello",
        )
    )

    trace = await runtime.trace_store.get(result.trace_id)
    assert trace is not None
    snapshot = trace.snapshot
    # credential_versions 真实化：等于 Secret 的真实 version "2"（非占位 "1"）
    assert snapshot.credential_versions is not None
    assert credential_ref in snapshot.credential_versions
    assert snapshot.credential_versions[credential_ref] == "2"
    # memory manifest 非 unavailable 占位（recall 成功，即使空也非 unavailable）
    assert snapshot.memory_manifest is not None
    assert snapshot.memory_manifest.content_hash != "unavailable"
    await runtime.close()


@pytest.mark.asyncio
async def test_bs07_snapshot_freezes_provider_credential_selection(sqlite_store: RegistryStore) -> None:
    """B-S-07：Snapshot 构建期冻结 provider credential 选择——新增 binding 后，
    旧 snapshot 的 provider_credentials 不变（运行期按冻结 ref 解密，不重选）。"""
    secrets = LocalEncryptedSecretStore(master_key=b"m" * 32)
    spec_ref = await secrets.put(TENANT, "spec-cred", "spec-secret")
    override_ref = await secrets.put(TENANT, "override-cred", "override-secret")
    credential_resolver = CredentialResolver(secrets)

    # seed provider（spec credential_ref）+ 默认 runtime_profile + agent
    await publish_resource(
        sqlite_store,
        tenant_id=TENANT,
        kind=ResourceKind.MODEL_PROVIDER,
        resource_id="wire-provider",
        version="1",
        spec={
            "protocol": "openai-compatible",
            "base_url": "http://127.0.0.1:9999/v1",
            "credential_ref": spec_ref,
            "default_model": "wire-model",
            "request_timeout_ms": 2_000,
            "max_retries": 0,
        },
    )
    await publish_resource(
        sqlite_store,
        tenant_id=TENANT,
        kind=ResourceKind.RUNTIME_PROFILE,
        resource_id="assistant",
        version="1",
        spec={"request_timeout_ms": 30_000, "max_retries": 1, "default": True},
    )
    await seed_model_definition(sqlite_store, tenant_id=TENANT, provider_id="wire-provider")
    await publish_resource(
        sqlite_store,
        tenant_id=TENANT,
        kind=ResourceKind.AGENT_DEFINITION,
        resource_id="assistant",
        version="1",
        spec={
            "name": "assistant",
            "system_prompt": "p",
            "owner": "fixture",
            "model_policy": {
                "primary_model_ref": {"id": "model.wire-provider", "version": "1"}
            },
        },
    )

    resolver = ContextResolver(sqlite_store, credential_resolver=credential_resolver)
    selector = ResolverSelector(tenant_id=TENANT, agent_id="assistant", user_id="user-a")

    # 第一次 resolve：无 binding → provider_credentials 冻结 spec credential_ref
    first = await resolver.resolve(selector, session_id="s-1")
    assert first.snapshot.provider_credentials["wire-provider"] == spec_ref

    # 新增 user binding（override）→ 第二次 resolve 冻结 override；第一次不受影响
    await sqlite_store.put_binding(
        ResourceBinding(
            binding_id="bind-override",
            tenant_id=TENANT,
            subject_type="user",
            subject_id="user-a",
            resource_type=ResourceKind.MODEL_PROVIDER,
            resource_id="wire-provider",
            credential_ref=override_ref,
        )
    )
    second = await resolver.resolve(selector, session_id="s-2")
    assert first.snapshot.provider_credentials["wire-provider"] == spec_ref
    assert second.snapshot.provider_credentials["wire-provider"] == override_ref

