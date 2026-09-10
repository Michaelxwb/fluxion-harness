"""TASK-004 Product API `/studio/*` 验收测试。

- BE-S-01（E2E）：POST /studio/agents → :publish → GET 列表可见；
  spec 为 typed model 形态；真实链 API→Service→Store（无 mock）。
- BE-S-07（E2E）：POST /studio/model-providers（api_key 走 SecretRef）→ GET 列表
  → schema 端点可驱动表单。
- BE-E-01：缺 model_policy/owner → 4xx + `agent_definition_invalid`，message 定位字段。
- BE-E-02：重复 (resource_id, version) → 409 `version_conflict`。
"""

from __future__ import annotations

import pytest

from fluxion.resources import ResourceKind
from tests.console_helpers import console_stack, tenant_headers
from tests.runtime_helpers import publish_resource, seed_model_definition


def _agent_spec() -> dict[str, object]:
    return {
        "name": "Support Agent",
        "description": "客服助手",
        "system_prompt": "You are a support agent.",
        "owner": "builder-1",
        "model_policy": {
            "primary_model_ref": {"id": "model.provider-1", "version": "1"}
        },
        "runtime_profile_ref": {"id": "profile-1", "version": "1"},
        "capabilities": [
            {"capability_ref": "skill-1", "version_pin": "1", "type": "skill"},
            {"capability_ref": "mcp-1", "version_pin": "1", "type": "mcp"},
        ],
        "instructions": "Answer concisely.",
    }


@pytest.mark.asyncio
async def test_be_s_01_studio_agent_create_publish_and_list() -> None:
    async with console_stack() as stack:
        # ADR-A008：publish 校验会解析 model_policy.primary_model_ref → 需先
        # seed 对应 ModelDefinition（tenant 与 agent 一致）。
        await seed_model_definition(
            stack.store,
            tenant_id="tenant-a",
            model_id="model.provider-1",
            provider_id="provider-1",
        )
        # RULE-04/S-04：publish 引用完整性同样解析 capabilities——skill/mcp
        # 需已定义（skill required_capabilities 为空，不触发闭包缺失）。
        await publish_resource(
            stack.store,
            tenant_id="tenant-a",
            kind=ResourceKind.SKILL,
            resource_id="skill-1",
            version="1",
            spec={"name": "skill-1"},
        )
        await publish_resource(
            stack.store,
            tenant_id="tenant-a",
            kind=ResourceKind.MCP,
            resource_id="mcp-1",
            version="1",
            spec={"name": "mcp-1", "url": "https://mcp.example.com/mcp"},
        )
        # ADR-A010（TASK-002）：显式 runtime_profile_ref 的存在/发布校验——
        # profile-1 需先发布，否则 publish fail-closed。
        await publish_resource(
            stack.store,
            tenant_id="tenant-a",
            kind=ResourceKind.RUNTIME_PROFILE,
            resource_id="profile-1",
            version="1",
            # V2（105 P1-01 方案A / TASK-004）：仅有效字段。
            spec={"max_rounds": 8, "default": True},
        )
        created = await stack.client.post(
            "/studio/agents",
            json={"resource_id": "agent-1", "version": "1", "spec": _agent_spec()},
            headers=tenant_headers(request_id="req-be-s01-create"),
        )
        assert created.status_code == 200, created.text
        payload = created.json()
        assert payload["code"] == 0 and payload["request_id"]
        assert payload["data"]["status"] == "draft"

        published = await stack.client.post(
            "/studio/agents/agent-1/versions/1:publish",
            headers=tenant_headers(request_id="req-be-s01-publish"),
        )
        assert published.status_code == 200, published.text
        assert published.json()["data"]["status"] == "published"

        listing = await stack.client.get(
            "/studio/agents", headers=tenant_headers(request_id="req-be-s01-list")
        )
        assert listing.status_code == 200
        body = listing.json()
        assert body["code"] == 0
        ids = [item["resource_id"] for item in body["data"]["items"]]
        assert "agent-1" in ids
        # spec 经 typed model 校验落库（引用而非内嵌 persona/model；ADR-A008 后
        # 模型引用形态为 model_policy → ModelDefinition，legacy model_ref 不落库）。
        raw = await stack.store.get(
            ResourceKind.AGENT_DEFINITION, "agent-1", tenant_id="tenant-a", version="1"
        )
        assert raw is not None and raw.status.value == "published"
        assert "prompt" not in raw.spec_json and "model_ref" not in raw.spec_json
        assert raw.spec_json["model_policy"] == {
            "primary_model_ref": {"id": "model.provider-1", "version": "1"}
        }


@pytest.mark.asyncio
async def test_be_s_07_studio_models_crud_with_secret_ref_schema() -> None:
    async with console_stack() as stack:
        created = await stack.client.post(
            "/studio/model-providers",
            json={
                "resource_id": "provider-a",
                "version": "1",
                "spec": {
                    "protocol": "openai-compatible",
                    "base_url": "https://api.example.com/v1",
                    "credential_ref": "secret://tenant-a/api-key",
                    "default_model": "deepseek-chat",
                },
            },
            headers=tenant_headers(request_id="req-be-s07-create"),
        )
        assert created.status_code == 200, created.text

        # B-E-02：publish 校验要求 credential_ref 指向已定义的 SECRET 资源。
        await publish_resource(
            stack.store,
            tenant_id="tenant-a",
            kind=ResourceKind.SECRET,
            resource_id="api-key",
            version="1",
            spec={"name": "api-key", "secret_ref": "secret://tenant-a/api-key@1"},
        )

        # Product API v1 列表沿用仓库既定语义：published-only（store
        # _list_published_resources）；draft 浏览为独立 UI 任务，不在本场景。
        published = await stack.client.post(
            "/studio/model-providers/provider-a/versions/1:publish",
            headers=tenant_headers(request_id="req-be-s07-publish"),
        )
        assert published.status_code == 200, published.text

        listed = await stack.client.get(
            "/studio/model-providers", headers=tenant_headers(request_id="req-be-s07-list")
        )
        assert listed.status_code == 200
        items = listed.json()["data"]["items"]
        assert any(item["resource_id"] == "provider-a" for item in items)
        # 凭据只允许 SecretRef 引用形态出现。
        assert all("sk-" not in str(item) for item in items)

        schema = await stack.client.get(
            "/api/v1/resources/model_provider/schema", headers=tenant_headers()
        )
        assert schema.status_code == 200
        properties = set(schema.json()["data"]["schema"]["properties"])
        assert {"protocol", "base_url", "credential_ref", "default_model"} <= properties


@pytest.mark.asyncio
async def test_be_e_01_agent_without_required_field_rejected() -> None:
    async with console_stack() as stack:
        bad_spec = _agent_spec()
        del bad_spec["model_policy"]

        response = await stack.client.post(
            "/studio/agents",
            json={"resource_id": "agent-bad", "version": "1", "spec": bad_spec},
            headers=tenant_headers(request_id="req-be-e01"),
        )
        assert response.status_code == 422
        payload = response.json()
        assert payload["code"] != 0
        assert "model_policy" in payload["message"] or "Field required" in payload["message"]


@pytest.mark.asyncio
async def test_be_e_02_duplicate_agent_version_conflicts() -> None:
    async with console_stack() as stack:
        body = {"resource_id": "agent-dup", "version": "1", "spec": _agent_spec()}
        first = await stack.client.post(
            "/studio/agents", json=body, headers=tenant_headers(request_id="req-be-e02a")
        )
        second = await stack.client.post(
            "/studio/agents", json=body, headers=tenant_headers(request_id="req-be-e02b")
        )
        assert first.status_code == 200
        assert second.status_code == 409
        payload = second.json()
        assert payload["code"] != 0


@pytest.mark.asyncio
async def test_bs09_product_endpoints_server_side_id_and_audit() -> None:
    """B-S-09（TASK-007）：产品端点服务端生成 id + 统一 envelope + 高影响操作入 AuditLog。"""
    async with console_stack() as stack:
        # 1) secrets：不传 resource_id → 服务端生成 id + envelope
        secret = await stack.client.post(
            "/studio/secrets",
            json={"spec": {"name": "api-key", "secret_ref": "secret://tenant-a/api-key@1"}},
            headers=tenant_headers(request_id="req-bs09-secret"),
        )
        assert secret.status_code == 200, secret.text
        secret_body = secret.json()
        assert secret_body["code"] == 0 and secret_body["request_id"] == "req-bs09-secret"
        secret_id = secret_body["data"]["resource_id"]
        assert secret_id  # 服务端生成，非空
        assert secret_id != "api-key"  # 非用户给定 id

        # 2) workflows：新白名单 + 服务端 id（TASK-007 收口缺口）
        workflow = await stack.client.post(
            "/studio/workflows",
            json={
                "spec": {
                    "name": "weekly-report",
                    "description": "每周报表",
                    "engine_ref": "workflow-engine://primary",
                    "steps": [
                        {
                            "id": "collect",
                            "capability_ref": "collect",
                            "depends_on": [],
                            "input": {"period": "last-week"},
                        }
                    ],
                }
            },
            headers=tenant_headers(request_id="req-bs09-workflow"),
        )
        assert workflow.status_code == 200, workflow.text
        assert workflow.json()["data"]["resource_id"]

        # 3) 高影响操作（发布）入 AuditLog
        await stack.client.post(
            f"/studio/secrets/{secret_id}/versions/1:publish",
            headers=tenant_headers(request_id="req-bs09-publish"),
        )
        audit = await stack.client.get(
            "/api/v1/audit", headers=tenant_headers(request_id="req-bs09-audit")
        )
        assert audit.status_code == 200
        actions = [item.get("action") for item in audit.json()["data"]["items"]]
        assert "publish" in actions, f"AuditLog 应记录 publish 高影响操作: {actions}"


@pytest.mark.asyncio
async def test_bs09_credential_create_plaintext_write_only() -> None:
    """TASK-009：POST /api/v1/credentials 明文只写不回显（规则 17）。"""
    async with console_stack() as stack:
        created = await stack.client.post(
            "/api/v1/credentials",
            json={"name": "openai-key", "secret": "sk-plaintext-secret", "purpose": "模型供应商"},
            headers=tenant_headers(request_id="req-cred-create"),
        )
        assert created.status_code == 200, created.text
        body = created.json()
        assert body["code"] == 0 and body["request_id"] == "req-cred-create"
        # 明文不回显：响应 payload 不含明文 secret
        assert "sk-plaintext-secret" not in str(body)
        secret_id = body["data"]["resource_id"]
        # Secret 元数据落档，spec 只保存 SecretRef（非明文）
        raw = await stack.store.get(
            ResourceKind.SECRET, secret_id, tenant_id="tenant-a", version="1"
        )
        assert raw is not None
        assert "sk-plaintext-secret" not in str(raw.spec_json)
        assert raw.spec_json["secret_ref"] == f"secret://tenant-a/{secret_id}@1"


async def test_t009_credential_rotate_versions_secret_and_audits() -> None:
    """TASK-009 行操作·轮换：store 层生成新 SecretRef 版本 + working draft 落档
    + 高影响操作入 AuditLog（规则 24）+ 明文不回显（规则 17）。"""
    async with console_stack() as stack:
        created = await stack.client.post(
            "/api/v1/credentials",
            json={"name": "rotate-key", "secret": "sk-old", "purpose": "模型供应商"},
            headers=tenant_headers(request_id="req-cred-rotate"),
        )
        assert created.status_code == 200, created.text
        created_body = created.json()
        credential_id = created_body["data"]["resource_id"]
        old_ref = created_body["data"]["spec"]["secret_ref"]

        rotated = await stack.client.post(
            f"/api/v1/credentials/{credential_id}:rotate",
            json={"secret": "sk-new"},
            headers=tenant_headers(request_id="req-cred-rotate-2"),
        )
        assert rotated.status_code == 200, rotated.text
        body = rotated.json()
        assert body["code"] == 0 and body["request_id"] == "req-cred-rotate-2"
        new_ref = body["data"]["spec"]["secret_ref"]
        assert new_ref != old_ref and new_ref.endswith("@2")
        # 明文不回显
        assert "sk-new" not in str(body)

        # Registry 落档：working draft spec 指向新 ref
        raw = await stack.store.get(
            ResourceKind.SECRET,
            credential_id,
            tenant_id="tenant-a",
            version=body["data"]["version"],
        )
        assert raw is not None
        assert raw.spec_json["secret_ref"] == new_ref

        # 旧版本 ref 仍可解析（版本化保留），新 ref 解析到新明文
        secret_store = stack.service._secret_store
        assert secret_store is not None
        assert (await secret_store.resolve(new_ref)).value == "sk-new"
        assert (await secret_store.resolve(old_ref)).value == "sk-old"

        # 审计：credential.rotate 入 AuditLog
        audits, _total = await stack.store.list_audit(tenant_id="tenant-a", offset=0, limit=50)
        assert any(
            record.action == "credential.rotate" and record.target_id == credential_id
            for record in audits
        )


async def test_t009_credential_disable_revokes_and_fails_closed() -> None:
    """TASK-009 行操作·禁用：store revoke 后 resolve fail-closed（secret_revoked），
    spec 标记 revoked 供 UI 呈现，审计入 AuditLog。"""
    async with console_stack() as stack:
        created = await stack.client.post(
            "/api/v1/credentials",
            json={"name": "disable-key", "secret": "sk-live", "purpose": "模型供应商"},
            headers=tenant_headers(request_id="req-cred-disable"),
        )
        assert created.status_code == 200, created.text
        credential_id = created.json()["data"]["resource_id"]
        ref = created.json()["data"]["spec"]["secret_ref"]

        disabled = await stack.client.post(
            f"/api/v1/credentials/{credential_id}:disable",
            headers=tenant_headers(request_id="req-cred-disable-2"),
        )
        assert disabled.status_code == 200, disabled.text
        body = disabled.json()
        assert body["code"] == 0
        assert body["data"]["spec"]["revoked"] is True

        # store 层 fail-closed：revoked 后 resolve 拒绝（运行期不会再裸调旧值）
        from fluxion.runtime.secrets import SecretProviderError

        secret_store = stack.service._secret_store
        assert secret_store is not None
        with pytest.raises(SecretProviderError) as excinfo:
            await secret_store.resolve(ref)
        assert excinfo.value.code == "secret_revoked"

        # 审计：credential.disable 入 AuditLog
        audits, _total = await stack.store.list_audit(tenant_id="tenant-a", offset=0, limit=50)
        assert any(
            record.action == "credential.disable" and record.target_id == credential_id
            for record in audits
        )
