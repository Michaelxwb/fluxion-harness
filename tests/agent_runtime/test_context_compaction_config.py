"""[E-04/B-02] 压缩配置随 execution snapshot 冻结（FEAT-01）。

真实边界：真实 PostgreSQL 的 `runtime.runtime_snapshot.policy_json`——Run 通过真实
`POST /v1/runs` 创建，冻结行的 `policy_json["compaction"]` 逐键回读比对。
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

import pytest
from httpx import AsyncClient
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import RunRecord, RuntimeSnapshot

from agent_runtime.conftest import FakeResolveClient, TenantContext, parse_sse

ENTRY_ID = uuid.uuid4()

#: 压缩分组只允许这 4 个叶子；默认值被静默改动时用例必须红。
DEFAULT_COMPACTION: dict[str, Any] = {
    "history_budget_tokens": 16384,
    "tool_result": {"persist_threshold_bytes": 8192},
    "summary": {"enabled": False, "model_ref": None},
}


def _headers(tenant: TenantContext) -> dict[str, str]:
    return {"X-Tenant-Id": tenant.tenant_id}


def _payload(tenant: TenantContext, text: str = "compaction config baseline") -> dict[str, Any]:
    return {
        "agent_id": str(tenant.agent_id),
        "platform_user_id": str(tenant.platform_user_id),
        "channel": {
            "type": "WECOM",
            "entry_id": str(ENTRY_ID),
            "bot_id": "bot-1",
            "external_conversation_id": "ext-1",
        },
        "message": {"id": f"msg-{uuid.uuid4()}", "type": "text", "text": text},
    }


async def _snapshot_row(run_id: uuid.UUID) -> dict[str, Any]:
    async with get_session_factory()() as session:
        run = await session.get(RunRecord, run_id)
        assert run is not None and run.snapshot_id is not None
        snapshot = await session.get(RuntimeSnapshot, run.snapshot_id)
        assert snapshot is not None and snapshot.run_id == run_id
        return {
            "agent_json": snapshot.agent_json,
            "model_json": snapshot.model_json,
            "policy_json": snapshot.policy_json,
            "content_hash": snapshot.content_hash,
        }


async def _start_run(client: AsyncClient, tenant: TenantContext, text: str) -> uuid.UUID:
    response = await client.post("/v1/runs", json=_payload(tenant, text), headers=_headers(tenant))
    assert response.status_code == 200, response.text
    return uuid.UUID(str(parse_sse(response.text)[0]["run_id"]))


async def test_e04_run_snapshot_freezes_compaction_defaults(
    client: AsyncClient,
    tenant: TenantContext,
    resolved: Any,
    fake_resolve: FakeResolveClient,
) -> None:
    run_id = await _start_run(client, tenant, "defaults")

    row = await _snapshot_row(run_id)
    policy = row["policy_json"]
    assert policy["max_model_retries"] == 3
    assert policy["compaction"] == DEFAULT_COMPACTION, "压缩默认值必须与设计逐键一致"
    assert row["content_hash"].startswith("sha256:")


async def test_e04_config_change_only_affects_new_runs(
    client: AsyncClient,
    tenant: TenantContext,
    resolved: Any,
    fake_resolve: FakeResolveClient,
) -> None:
    """两条单腿：只改 Agent 压缩覆盖 / 只改 model；旧 Run 的冻结行逐列不变。"""
    first = await _start_run(client, tenant, "before override")
    before = await _snapshot_row(first)
    assert before["policy_json"]["compaction"] == DEFAULT_COMPACTION

    agent_overridden = resolved.agent.model_copy(
        update={"runtime_config": {"budget": {"compaction": {"history_budget_tokens": 12000}}}}
    )
    fake_resolve.response = resolved.model_copy(update={"agent": agent_overridden})

    second = await _start_run(client, tenant, "after compaction override")
    after_override = await _snapshot_row(second)
    assert after_override["policy_json"]["compaction"]["history_budget_tokens"] == 12000
    assert after_override["policy_json"]["compaction"]["tool_result"] == {
        "persist_threshold_bytes": 8192
    }
    assert after_override["model_json"] == before["model_json"], "压缩腿不得改动 model 列"
    assert await _snapshot_row(first) == before

    model_changed = resolved.model.model_copy(
        update={"params": {"temperature": 0.9}, "revision": resolved.model.revision + 1}
    )
    fake_resolve.response = resolved.model_copy(
        update={"agent": agent_overridden, "model": model_changed}
    )

    third = await _start_run(client, tenant, "after model change")
    after_model = await _snapshot_row(third)
    assert after_model["model_json"]["params"] == {"temperature": 0.9}
    assert after_model["model_json"] != after_override["model_json"]
    assert after_model["policy_json"] == after_override["policy_json"], "model 腿不得改动 policy_json"
    assert await _snapshot_row(first) == before
    assert await _snapshot_row(second) == after_override


@pytest.mark.parametrize(
    ("override", "reason"),
    [
        ({"history_budget_tokens": 0}, "历史预算必须是正整数"),
        ({"history_budget_tokens": 131073}, "历史预算不得超过 schema 上限"),
        ({"summary": {"enabled": True}}, "summary.enabled 必须同时给出 model_ref"),
        ({"tool_result": {"persist_threshold_bytes": -1}}, "外置阈值必须 ≥ 0"),
        ({"snip": {"max_groups": 5}}, "旧 snip 键必须显式拒绝"),
        ({"history_budget_messages": 40}, "旧条数键必须显式拒绝"),
        ({"memory": {"budget_ratio": 0.2}}, "旧 memory ratio 必须显式拒绝"),
        ({"snip": {"enabld": True}}, "未知配置键必须显式报错"),
    ],
)
def test_e04_invalid_compaction_config_is_explicitly_rejected(
    override: Mapping[str, Any], reason: str
) -> None:
    from muad_contracts.platform_settings import CompactionConfigError, parse_compaction_settings

    with pytest.raises(CompactionConfigError) as excinfo:
        parse_compaction_settings(override)
    assert str(excinfo.value), reason


def test_e04_resolve_uses_explicit_platform_overrides_and_agent_override_wins() -> None:
    from muad_agent_runtime.application.context_settings import resolve_compaction_settings

    settings = resolve_compaction_settings(
        {"budget": {"compaction": {"history_budget_tokens": 12000}}},
        platform_overrides={"history_budget_tokens": 20000},
    )
    assert settings.history_budget_tokens == 12000, "Agent 覆盖优先于平台快照"
    assert settings.tool_result.persist_threshold_bytes == 8192, "未覆盖的键回落 schema 默认"


def test_e04_resolve_without_platform_overrides_uses_schema_defaults() -> None:
    from muad_agent_runtime.application.context_settings import resolve_compaction_settings

    settings = resolve_compaction_settings(None, platform_overrides=None)
    assert settings.history_budget_tokens == 16384
    assert settings.summary.enabled is False


def test_e04_frozen_compaction_is_strict_and_has_no_old_keys() -> None:
    from muad_agent_runtime.application.context_settings import compaction_settings_of
    from muad_contracts.platform_settings import CompactionConfigError, default_compaction_settings

    frozen = {"compaction": {"history_budget_tokens": 12000}}
    with pytest.raises(CompactionConfigError):
        compaction_settings_of(frozen)
    with pytest.raises(CompactionConfigError):
        compaction_settings_of({"compaction": {**DEFAULT_COMPACTION, "snip": {}}})

    settings = compaction_settings_of({"compaction": DEFAULT_COMPACTION})
    assert settings.history_budget_tokens == 16384
    assert settings.tool_result == default_compaction_settings().tool_result
