"""[E-04] 压缩配置随 execution snapshot 冻结（FEAT-07）。

真实边界：真实 PostgreSQL 的 `runtime.runtime_snapshot.policy_json`——Run 通过真实
`POST /v1/runs` 创建，冻结行的 `policy_json["compaction"]` 逐键回读比对。

语言约定：`policy_json` 是 Run 侧 execution snapshot 里"预算/执行期策略"的等价载体
（`harness-snapshot#RULE-snapshot-001`），压缩配置与 `max_model_retries` 一并冻结在此。
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

# design §2.3 字段约束表的逐键快照。写死期望值是刻意的：默认值若被静默改动，用例必须红。
DEFAULT_COMPACTION: dict[str, Any] = {
    "snip": {
        "enabled": True,
        "max_groups": 50,
        "keep_head_groups": 3,
        "keep_tail_groups": 20,
    },
    "tool_result": {
        "persist_threshold_bytes": 8192,
        "round_budget_bytes": 200_000,
        "preview_head_bytes": 2000,
        "preview_tail_bytes": 2000,
    },
    "micro": {"enabled": False, "keep_recent_tool_groups": 3},
    "summary": {"enabled": False, "threshold_bytes": 50_000, "model_ref": None},
    "history_budget_messages": 40,
    "memory": {"budget_ratio": 0.2},
}


def _headers(tenant: TenantContext) -> dict[str, str]:
    return {"X-Tenant-Id": tenant.tenant_id}


def _payload(tenant: TenantContext, text: str = "compaction config baseline") -> dict[str, Any]:
    return {
        "agent_id": str(tenant.agent_id),
        "platform_user_id": str(tenant.platform_user_id),
        "channel": {"type": "WECOM", "bot_id": "bot-1", "external_conversation_id": "ext-1"},
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


# ---- 冻结：新 Run 的 policy_json 带 design 的默认压缩配置 ----


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
    assert policy["compaction"] == DEFAULT_COMPACTION, "压缩默认值必须与 design 字段约束表逐键一致"
    assert row["content_hash"].startswith("sha256:")


# ---- RULE-snapshot-001：配置变更只影响后续新 Run，旧 Snapshot 行逐列冻结（逐腿归因） ----


async def test_e04_config_change_only_affects_new_runs(
    client: AsyncClient,
    tenant: TenantContext,
    resolved: Any,
    fake_resolve: FakeResolveClient,
) -> None:
    """两个 Run 都走真实 POST /v1/runs（落真实 PG），变更分**两条单腿**表达：

    腿一：只改 Agent 的压缩配置（`runtime_config.budget.compaction`）⇒ 新 Run 的
    `policy_json` 必须变，而 `model_json` 必须一字不动；
    腿二：只改 model 的 `params` ⇒ `model_json` 变，而 `policy_json` 必须一字不动。
    每腿都在变更后重新逐列回读**旧 Run** 的快照行并与变更前直接对照。
    """
    first = await _start_run(client, tenant, "before override")
    before = await _snapshot_row(first)
    assert before["policy_json"]["compaction"] == DEFAULT_COMPACTION

    # 腿一：只改压缩配置（缩紧整轮预算与触发阈值），不动 model。
    tightened = dict(DEFAULT_COMPACTION)
    tightened["tool_result"] = {**DEFAULT_COMPACTION["tool_result"], "round_budget_bytes": 12345}
    tightened["snip"] = {**DEFAULT_COMPACTION["snip"], "max_groups": 30}
    agent_overridden = resolved.agent.model_copy(
        update={"runtime_config": {"temperature": 0.0, "budget": {"compaction": tightened}}}
    )
    fake_resolve.response = resolved.model_copy(update={"agent": agent_overridden})

    second = await _start_run(client, tenant, "after compaction override")
    after_override = await _snapshot_row(second)
    assert after_override["policy_json"]["compaction"]["tool_result"]["round_budget_bytes"] == 12345
    assert after_override["policy_json"]["compaction"]["snip"]["max_groups"] == 30
    # 未覆盖的键回落默认（覆盖是逐键合并，不是整块替换）。
    assert after_override["policy_json"]["compaction"]["micro"] == DEFAULT_COMPACTION["micro"]
    assert after_override["policy_json"]["max_model_retries"] == 3
    assert after_override["model_json"] == before["model_json"], "压缩腿不得改动 model 列"
    # 旧 Run 的冻结行必须逐列不变（在跑的 Run 用旧值）。
    assert await _snapshot_row(first) == before

    # 腿二：只改 model，压缩配置保持腿一的值。
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


# ---- 非法配置显式报错，不静默回落 ----


@pytest.mark.parametrize(
    ("override", "reason"),
    [
        ({"snip": {"max_groups": 5}}, "max_groups 必须 ≥ keep_head_groups + keep_tail_groups + 1"),
        ({"snip": {"keep_head_groups": 0}}, "keep_head_groups 必须 ≥ 1"),
        ({"snip": {"keep_tail_groups": 0}}, "keep_tail_groups 必须 ≥ 1"),
        ({"summary": {"enabled": True}}, "summary.enabled 必须同时给出 model_ref"),
        (
            {"tool_result": {"round_budget_bytes": 1024}},
            "round_budget_bytes 不得小于 persist_threshold_bytes",
        ),
        ({"history_budget_messages": 0}, "history_budget_messages 必须 ≥ 1"),
        ({"memory": {"budget_ratio": 1.5}}, "memory.budget_ratio 必须落在 (0, 1]"),
        ({"snip": {"enabld": True}}, "未知配置键必须显式报错"),
    ],
)
def test_e04_invalid_compaction_config_is_explicitly_rejected(
    override: Mapping[str, Any], reason: str
) -> None:
    from muad_contracts.platform_settings import CompactionConfigError, parse_compaction_settings

    with pytest.raises(CompactionConfigError) as excinfo:
        parse_compaction_settings(override)
    assert str(excinfo.value), reason  # 报错必须带可读原因，不能是空消息


# ---- 设置读取缝：平台快照由调用方显式传入，Agent 覆盖优先（无进程内缓存）----


def test_e04_resolve_uses_explicit_platform_overrides_and_agent_override_wins() -> None:
    from muad_agent_runtime.application.context_settings import resolve_compaction_settings

    settings = resolve_compaction_settings(
        {"budget": {"compaction": {"snip": {"max_groups": 60}}}},
        platform_overrides={"micro": {"enabled": True}, "snip": {"max_groups": 40}},
    )
    assert settings.micro.enabled is True, "平台快照生效"
    assert settings.snip.max_groups == 60, "Agent 覆盖优先于平台快照"
    assert settings.snip.keep_head_groups == 3, "未覆盖的键回落 schema 默认"


def test_e04_resolve_without_platform_overrides_uses_schema_defaults() -> None:
    from muad_agent_runtime.application.context_settings import resolve_compaction_settings

    settings = resolve_compaction_settings(None, platform_overrides=None)
    assert settings.snip.max_groups == 50
    assert settings.micro.enabled is False


# ---- `history_budget_messages` 不是没人读的字段：条数真的按它裁 ----


async def test_e04_history_budget_from_frozen_config_actually_trims(
    tenant: TenantContext,
    database_guard: None,
) -> None:
    """真实 PG 的 `runtime.canonical_event` 种 5 条 USER_MESSAGE，按冻结配置里的预算裁历史。

    断言三侧：
    ① **装配不裁**（`load_history` 把 5 条全给出来）——压缩只有一处，装配侧再压一遍就是两种口径；
    ② **压缩层按同一个冻结值裁**（`trim_history` 拿到 2 就把对话区裁到 2 条）；
    ③ resume 侧从 `policy_json` 取预算的读法（缺键回落 None ⇒ 两边各自用默认值，不会因为老 Run
       没有这个键而炸）。
    """
    from muad_agent_core.context.compactor import compact_history
    from muad_agent_runtime.application.context_builder import DbBackedContextBuilder
    from muad_agent_runtime.application.run_service import history_budget_of
    from muad_agent_runtime.infrastructure.models.runtime import CanonicalEvent
    from muad_contracts.platform_settings import default_compaction_settings

    conversation_id = uuid.uuid4()
    async with get_session_factory()() as session:
        for index in range(5):
            session.add(
                CanonicalEvent(
                    tenant_id=tenant.tenant_id,
                    conversation_id=conversation_id,
                    seq=index + 1,
                    event_type="USER_MESSAGE",
                    payload_json={"text": f"第 {index} 条诉求"},
                )
            )
        await session.commit()

    builder = DbBackedContextBuilder(session_factory=get_session_factory)
    history = await builder.load_history(
        tenant_id=tenant.tenant_id,
        conversation_id=conversation_id,
        user_id=None,
        budget_messages=2,
    )
    assert len(history) == 5, "装配侧不裁历史：裁剪只有压缩层一处口径"

    frozen = history_budget_of({"compaction": {"history_budget_messages": 2}})
    defaults = default_compaction_settings()
    compacted, layers = compact_history(
        history,
        snip_settings=defaults.snip,
        micro_settings=defaults.micro,
        history_budget_messages=frozen,
    )
    assert len(compacted) == 2, "冻结预算 2 必须把 5 条历史裁到 2 条"
    assert [(layer.layer, layer.fired) for layer in layers if layer.fired] == [("budget", True)]

    assert frozen == 2
    assert history_budget_of({"compaction": {}}) is None
    assert history_budget_of({}) is None
