

from __future__ import annotations

import uuid
from typing import Any

from httpx import AsyncClient
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import RunRecord, RuntimeSnapshot
from muad_contracts import ResolveDefinitionResponse

from agent_runtime.conftest import FakeResolveClient, TenantContext, parse_sse

# ---- B-104（08 TASK-004）：Snapshot 非密钥 + 稳定 hash + 认证隔离 ----

def test_b104_snapshot_hash_stable_across_key_rotation() -> None:
    """[B-104] api_key 不参与 content_hash：仅密钥轮换时 hash 稳定。"""
    from muad_agent_runtime.application.run_service import _snapshot_hash
    from muad_contracts import (
        ResolvedAgent,
        ResolveDefinitionResponse,
        ResolvedModel,
    )

    agent_id, model_id = uuid.uuid4(), uuid.uuid4()

    def resolved(api_key: str) -> ResolveDefinitionResponse:
        return ResolveDefinitionResponse(
            agent=ResolvedAgent(id=agent_id, key="k", revision=1, instructions="i", runtime_config={}),
            model=ResolvedModel(
                id=model_id, revision=1, model_id="gpt-4o-mini",
                base_url="https://api.example.com/v1", api_key=api_key,
            ),
            skills=[],
        )

    assert _snapshot_hash(resolved("key-old"), {}) == _snapshot_hash(resolved("key-new"), {})


def test_b104_snapshot_model_json_excludes_api_key() -> None:
    """[B-104] Snapshot 落库的 model_json 不含 api_key（认证只走 API-09）。"""
    from muad_agent_runtime.application.run_service import _snapshot_model
    from muad_contracts import ResolvedModel

    dump = _snapshot_model(
        ResolvedModel(
            id=uuid.uuid4(), revision=1, model_id="gpt-4o-mini",
            base_url="https://api.example.com/v1", api_key="secret-value",
        )
    )
    assert "api_key" not in dump
    assert "secret-value" not in str(dump)
    assert dump["model_id"] == "gpt-4o-mini"  # 冻结的 model 标识保留


# ---- [RULE-snapshot-001] 定义变更只影响后续新 Run：旧 Snapshot 行逐列冻结（真实 PG） ----


def _headers(tenant: TenantContext) -> dict[str, str]:
    return {"X-Tenant-Id": tenant.tenant_id}


def _payload(tenant: TenantContext, text: str = "freeze baseline") -> dict[str, Any]:
    return {
        "agent_id": str(tenant.agent_id),
        "platform_user_id": str(tenant.platform_user_id),
        "channel": {"type": "WECOM", "bot_id": "bot-1", "external_conversation_id": "ext-1"},
        "message": {"id": f"msg-{uuid.uuid4()}", "type": "text", "text": text},
    }


async def _snapshot_row(run_id: uuid.UUID) -> dict[str, Any]:
    """Run 的冻结 Snapshot 行（含请求路径上落库的全部列，用于逐列前后对照）。"""
    async with get_session_factory()() as session:
        run = await session.get(RunRecord, run_id)
        assert run is not None
        assert run.snapshot_id is not None
        snapshot = await session.get(RuntimeSnapshot, run.snapshot_id)
        assert snapshot is not None
        assert snapshot.run_id == run_id
        return {
            "snapshot_id": snapshot.id,
            "run_id": snapshot.run_id,
            "schema_version": snapshot.schema_version,
            "agent_revision": snapshot.agent_revision,
            "model_revision": snapshot.model_revision,
            "agent_json": snapshot.agent_json,
            "model_json": snapshot.model_json,
            "skill_catalog_json": snapshot.skill_catalog_json,
            "mcp_catalog_json": snapshot.mcp_catalog_json,
            "policy_json": snapshot.policy_json,
            "prompt_template_version": snapshot.prompt_template_version,
            "content_hash": snapshot.content_hash,
        }


async def test_b104_definition_change_only_affects_new_runs(
    client: AsyncClient,
    tenant: TenantContext,
    resolved: ResolveDefinitionResponse,
    fake_resolve: FakeResolveClient,
) -> None:
    """[B-104][RULE-snapshot-001] 定义变更只影响后续 Run：旧 Run 的 Snapshot 行逐列不变。

    两个 Run 都走真实 `POST /v1/runs`（落真实 PG 的 `runtime.runtime_snapshot`）；变更由
    `fake_resolve.response` 分**两条单腿**表达（先只换 agent，再只换 model），每腿都必须
    独立改变 `content_hash`——若 hash 未冻结其中任一腿，对应那一腿的断言就会红。旧 Run 的
    快照行在每次变更后重新逐列回读并与变更前直接对照（不以 resume 行为代替列级冻结证据）。
    """
    first = await client.post("/v1/runs", json=_payload(tenant), headers=_headers(tenant))
    assert first.status_code == 200, first.text
    run1 = uuid.UUID(str(parse_sse(first.text)[0]["run_id"]))
    before = await _snapshot_row(run1)
    assert before["content_hash"].startswith("sha256:")
    assert "api_key" not in before["model_json"]
    # policy_json 冻结**执行期策略**：`max_model_retries` 之外，压缩策略也一并冻结在此键
    # （RULE-snapshot-001 的"预算只属 snapshot 的 budget 键 / Run 侧等价载体是 policy_json"）。
    assert before["policy_json"]["max_model_retries"] == 3
    assert "compaction" in before["policy_json"]
    assert before["agent_revision"] == resolved.agent.revision
    assert before["model_revision"] == resolved.model.revision

    agent_changed = resolved.agent.model_copy(
        update={"revision": resolved.agent.revision + 1, "instructions": "changed agent"}
    )
    model_changed = resolved.model.model_copy(
        update={
            "revision": resolved.model.revision + 1,
            "base_url": "https://api.example.com/v2",
            "params": {"temperature": 0.9},
        }
    )

    # 第一腿：只换 agent（model 腿不动）⇒ hash 必须变
    fake_resolve.response = resolved.model_copy(update={"agent": agent_changed})
    second = await client.post(
        "/v1/runs", json=_payload(tenant, "after agent change"), headers=_headers(tenant)
    )
    assert second.status_code == 200, second.text
    run2 = uuid.UUID(str(parse_sse(second.text)[0]["run_id"]))
    after_agent = await _snapshot_row(run2)
    assert after_agent["run_id"] == run2
    assert after_agent["agent_json"]["instructions"] == "changed agent"
    assert after_agent["agent_json"] != before["agent_json"]
    assert after_agent["agent_revision"] == resolved.agent.revision + 1
    assert after_agent["model_json"] == before["model_json"], "Agent 腿不得改动 model 列"
    assert after_agent["model_revision"] == before["model_revision"]
    assert after_agent["content_hash"].startswith("sha256:")
    assert after_agent["content_hash"] != before["content_hash"], "只换 agent 也必须改变 hash"
    assert await _snapshot_row(run1) == before

    # 第二腿：再换 model（agent 保持第一腿的值）⇒ 相对第一腿 hash 必须再变
    fake_resolve.response = resolved.model_copy(
        update={"agent": agent_changed, "model": model_changed}
    )
    third = await client.post(
        "/v1/runs", json=_payload(tenant, "after model change"), headers=_headers(tenant)
    )
    assert third.status_code == 200, third.text
    run3 = uuid.UUID(str(parse_sse(third.text)[0]["run_id"]))
    after_model = await _snapshot_row(run3)
    assert after_model["run_id"] == run3
    assert after_model["model_json"]["base_url"] == "https://api.example.com/v2"
    assert after_model["model_json"]["params"] == {"temperature": 0.9}
    assert after_model["model_json"] != after_agent["model_json"]
    assert after_model["model_revision"] == resolved.model.revision + 1
    assert after_model["agent_json"] == after_agent["agent_json"], "Model 腿不得改动 agent 列"
    assert after_model["content_hash"] != after_agent["content_hash"], "只换 model 也必须改变 hash"
    assert await _snapshot_row(run1) == before
