"""B-19: frozen primary model, live API-09, independent timeout and resource close."""

import json
from importlib import import_module

import pytest
from muad_agent_runtime.infrastructure.models.runtime import RunRecord
from muad_contracts.platform_settings import MemoryPolicySettings

from tests.agent_runtime.memory_consolidation_support import TrackedProvider, model_stack, provider_factory

pytestmark = pytest.mark.integration


async def test_b19_memory_redesign_summary_disabled_uses_frozen_main_and_live_rotated_credentials(
    async_tool_database,
):
    module = import_module("muad_agent_runtime.application.memory_consolidation")
    async with model_stack(async_tool_database) as (
        factory,
        run,
        job,
        snapshot,
        client,
        requests,
        creds,
        key,
    ):
        assert snapshot.policy_json["compaction"]["summary"]["enabled"] is False
        before = json.dumps(snapshot.model_json, sort_keys=True)
        invoker = module.ConsolidationInvoker(factory, client, provider_factory=provider_factory)
        payload = {"messages": [{"role": "USER", "text": "first-live-credential 你好"}]}
        first = await invoker.generate(job, snapshot, payload)
        key[0] = "second-live-credential"
        second = await invoker.generate(job, snapshot, payload)
        assert first.candidates == second.candidates == []
        assert [body["model"] for body, _ in requests] == ["frozen-main-model"] * 2
        assert [auth for _, auth in requests] == [
            "Bearer first-live-credential",
            "Bearer second-live-credential",
        ]
        assert all(body["max_tokens"] == 1024 for body, _ in requests)
        assert "first-live-credential" not in json.dumps(requests[0][0])
        assert len(creds) == 2 and all(row["model_id"] == snapshot.model_json["id"] for row in creds)
        assert (
            json.dumps(snapshot.model_json, sort_keys=True) == before and "api_key" not in snapshot.model_json
        )
        assert all(provider.closed for provider in TrackedProvider.instances)
        async with factory() as session:
            assert (await session.get(RunRecord, run.id)).status == "COMPLETED"


@pytest.mark.parametrize("phase", ["credential", "model"])
async def test_b19_memory_redesign_independent_timeout_keeps_run_completed_and_closes_provider(
    async_tool_database,
    phase,
):
    module = import_module("muad_agent_runtime.application.memory_consolidation")
    delay = {"credential_delay": 2} if phase == "credential" else {"delay": 2}
    async with model_stack(
        async_tool_database, policy=MemoryPolicySettings(consolidation_timeout_sec=1), **delay
    ) as stack:
        factory, run, job, snapshot, client, _, _, _ = stack
        invoker = module.ConsolidationInvoker(factory, client, provider_factory=provider_factory)
        with pytest.raises(TimeoutError):
            await invoker.generate(job, snapshot, {"messages": []})
        assert all(provider.closed for provider in TrackedProvider.instances)
        async with factory() as session:
            assert (await session.get(RunRecord, run.id)).status == "COMPLETED"


@pytest.mark.parametrize("case", ["oversized_input", "rejected_output"])
async def test_b19_memory_redesign_rejection_closes_provider_and_preserves_completed_run(
    async_tool_database,
    case,
):
    module = import_module("muad_agent_runtime.application.memory_consolidation")
    from muad_agent_runtime.application.memory_consolidation_output import ConsolidationRejected

    kwargs = (
        {"policy": MemoryPolicySettings(consolidation_max_input_bytes=1024)}
        if case == "oversized_input"
        else {"output": '{"candidates":[{"memory_key":"x","author":"USER"}],"task_completed":false}'}
    )
    async with model_stack(async_tool_database, **kwargs) as stack:
        factory, run, job, snapshot, client, requests, _, _ = stack
        invoker = module.ConsolidationInvoker(factory, client, provider_factory=provider_factory)
        error = module.ConsolidationInputTooLarge if case == "oversized_input" else ConsolidationRejected
        with pytest.raises(error):
            await invoker.generate(job, snapshot, {"messages": ["不截断事实" * 1000]})
        assert len(requests) == int(case == "rejected_output")
        assert all(provider.closed for provider in TrackedProvider.instances)
        async with factory() as session:
            assert (await session.get(RunRecord, run.id)).status == "COMPLETED"
