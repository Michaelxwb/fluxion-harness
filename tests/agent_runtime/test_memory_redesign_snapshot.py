"""B-22 and B-12 auxiliary: real settings, snapshots, owned reads and tool history."""

import json
from dataclasses import asdict, replace
from uuid import uuid4

import pytest
import sqlalchemy as sa
from muad_agent_runtime.application.context_builder import DbBackedContextBuilder
from muad_agent_runtime.application.executor import build_registry
from muad_agent_runtime.application.memory_queries import MemoryQueries, MemoryReadScope
from muad_agent_runtime.application.memory_tools import FORGET_TOOL
from muad_agent_runtime.application.run_service import RunService, execution_defaults_of, snapshot_policy
from muad_agent_runtime.infrastructure.db import get_engine, get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import (
    CanonicalEvent,
    Conversation,
    MemoryConsolidationJob,
)
from muad_console_platform.application.platform_settings_service import (
    MemoryQuotaReductionRequiresMaintenance,
)
from muad_contracts.platform_settings import MemoryPolicySettings, PlatformSettingsError

from tests.agent_runtime.memory_redesign_write_support import context, seed_facts
from tests.agent_runtime.test_execution_defaults_settings import (
    _cache,
    _CapturingExecutorFactory,
    _PgSettingsClient,
    _policy_json,
    _save_settings,
    _start_run,
)
from tests.agent_runtime.test_run_service import _seed_waiting_run_with_snapshot

pytestmark = pytest.mark.integration


async def test_b22_memory_redesign_old_run_reads_250_and_forgets_after_quota_increase(
    tenant,
    fake_resolve,
    tmp_path,
):
    capture, client = _CapturingExecutorFactory(), _PgSettingsClient()
    old = await _start_run(tenant, fake_resolve, capture, client)
    before = await _policy_json(old)
    await _save_settings(tenant.tenant_id, memory=MemoryPolicySettings(max_entries_per_user=300))
    new = await _start_run(tenant, fake_resolve, _CapturingExecutorFactory(), client)
    assert (await _policy_json(new))["memory"]["max_entries_per_user"] == 300
    assert (await _policy_json(old)) == before
    ctx = context(tenant=tenant.tenant_id, user=tenant.platform_user_id)
    await seed_facts(get_engine(), ctx, 250)
    queries = MemoryQueries(get_session_factory())
    scope = MemoryReadScope(tenant_id=ctx.tenant_id, user_id=ctx.user_id)
    assert len((await queries.read_snapshot(scope)).records) == 250
    registry = build_registry(
        request=capture.requests[0], cache=_cache(tmp_path), audit_writer=None, mcp_adapter=None
    )
    result = json.loads(
        await registry.get(FORGET_TOOL).handler(
            {"memory_key": "seed.1", "expected_version": 1},
            call_id="old-run-forget",
        )
    )
    assert result["deleted"] and result["version"] == 2
    assert len((await queries.read_snapshot(scope)).records) == 249
    with pytest.raises(MemoryQuotaReductionRequiresMaintenance):
        await _save_settings(tenant.tenant_id, memory=MemoryPolicySettings(max_entries_per_user=200))
    assert await _policy_json(old) == before


@pytest.mark.parametrize("leaf,value", [("ttl_days_user", 2), ("consolidation_timeout_sec", 20)])
async def test_b12_single_memory_leaf_changes_only_new_run_and_job_uses_source_snapshot(
    tenant,
    fake_resolve,
    leaf,
    value,
):
    client = _PgSettingsClient()
    old = await _start_run(tenant, fake_resolve, _CapturingExecutorFactory(), client)
    before = await _policy_json(old)
    async with get_session_factory().begin() as session:
        from muad_agent_runtime.infrastructure.models.runtime import RunRecord

        run = await session.get(RunRecord, old)
        job = await session.scalar(
            sa.select(MemoryConsolidationJob).where(
                MemoryConsolidationJob.tenant_id == tenant.tenant_id, MemoryConsolidationJob.run_id == old
            )
        )
        assert job is not None and job.snapshot_id == run.snapshot_id and job.status == "PENDING"
    await _save_settings(tenant.tenant_id, memory=replace(MemoryPolicySettings(), **{leaf: value}))
    new = await _start_run(tenant, fake_resolve, _CapturingExecutorFactory(), client)
    after = await _policy_json(new)
    expected = {**before, "memory": {**before["memory"], leaf: value}}
    assert after == expected and await _policy_json(job.run_id) == before
    assert asdict(execution_defaults_of(before).memory_policy) == before["memory"]
    assert before["deadline_ms"] == after["deadline_ms"]
    from muad_agent_runtime.application.memory_jobs import MemoryJobs

    owners = MemoryJobs(get_session_factory())
    claim = await owners.claim("frozen-old-job")
    while claim is not None and claim.id != job.id:
        claim = await owners.claim("frozen-old-job")
    assert claim is not None and claim.id == job.id
    fixed = await owners.freeze_input(claim, credential_canaries=())
    assert fixed["through_seq"] == job.through_seq and fixed["input_scope_revision"] == 0
    assert await _policy_json(job.run_id) == before


@pytest.mark.parametrize("missing", ["ttl_days_user", "consolidation_timeout_sec", "max_entries_per_user"])
def test_frozen_memory_snapshot_cannot_silently_fill_a_missing_policy_leaf(missing):
    policy = snapshot_policy()
    del policy["memory"][missing]
    with pytest.raises(PlatformSettingsError):
        execution_defaults_of(policy)


@pytest.mark.parametrize("limit", [0, 1, 8])
async def test_frozen_history_tool_budget_preserves_whole_rounds(async_tool_database, limit):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    factory = async_sessionmaker(async_tool_database, expire_on_commit=False)
    tenant, conversation, user = "frozen-tool-history", uuid4(), uuid4()
    async with factory.begin() as session:
        session.add(Conversation(id=conversation, tenant_id=tenant, user_id=user, agent_id=uuid4()))
        _seed_tool_rounds(session, tenant, conversation)
    messages = await DbBackedContextBuilder(session_factory=lambda: factory).load_history(
        tenant_id=tenant,
        conversation_id=conversation,
        user_id=user,
        max_history_tool_rounds=limit,
    )
    assert len(messages) == limit * 2
    assert [item.tool_call_id for item in messages if item.tool_call_id] == [
        f"call-{n}" for n in range(8 - limit, 8)
    ]


def _seed_tool_rounds(session, tenant, conversation):
    for index in range(8):
        call_id = f"call-{index}"
        session.add_all(
            [
                CanonicalEvent(
                    tenant_id=tenant,
                    conversation_id=conversation,
                    seq=index * 2 + 1,
                    event_type="ASSISTANT_TURN",
                    payload_json={"tool_calls": [{"id": call_id, "name": "probe", "arguments": {}}]},
                ),
                CanonicalEvent(
                    tenant_id=tenant,
                    conversation_id=conversation,
                    seq=index * 2 + 2,
                    event_type="TOOL_CALL",
                    payload_json={"tool_call_id": call_id, "tool_name": "probe", "preview": "ok"},
                ),
            ]
        )


async def test_b12_only_history_budget_leaf_changes_new_run_while_old_resume_keeps_six_rounds(
    tenant,
    resolved,
    fake_resolve,
):
    from muad_agent_runtime.application.async_tools.supervisor import ExecutionSupervisor
    from muad_contracts.platform_settings import default_compaction_settings

    conversation, old = await _seed_waiting_run_with_snapshot(tenant, resolved)
    before = await _policy_json(old)
    async with get_session_factory().begin() as session:
        row = await session.get(Conversation, conversation)
        row.last_seq = 16
        _seed_tool_rounds(session, tenant.tenant_id, conversation)
    await _save_settings(
        tenant.tenant_id, compaction=replace(default_compaction_settings(), max_history_tool_rounds=0)
    )
    capture, supervisor = _CapturingExecutorFactory(), ExecutionSupervisor()
    try:
        async with get_session_factory()() as session:
            started = await RunService(
                session,
                fake_resolve,
                "frozen-resume",
                settings_client=_PgSettingsClient(),
                executor_factory=capture,
                supervisor=supervisor,
            ).resume(
                old,
                "回答预算验证",
                tenant.tenant_id,
                platform_user_id=tenant.platform_user_id,
                input_id=uuid4().hex,
            )
            assert started.resumed
            events = [event async for event in started.events]
    finally:
        await supervisor.close()
    assert capture.requests, events
    assert len([message for message in capture.requests[0].history if message.tool_call_id]) == 6
    assert await _policy_json(old) == before
    new_capture = _CapturingExecutorFactory()
    new = await _start_run(tenant, fake_resolve, new_capture, _PgSettingsClient())
    assert (await _policy_json(new)) == {
        **before,
        "compaction": {**before["compaction"], "max_history_tool_rounds": 0},
        # 真实创建路径还冻结成员事实（B-224）：该夹具的模型没有成员 ⇒ 冻结为空列表；
        # `before` 来自直构快照（没有成员事实可冻结）因此没有这一键。断言仍是整份相等。
        "model_members": [],
    }
    assert not [message for message in new_capture.requests[0].history if message.tool_call_id]
