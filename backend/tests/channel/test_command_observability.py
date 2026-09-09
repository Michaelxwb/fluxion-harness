"""OBS-01（integration）：命令可观测验收。

- 命令审计：command.executed / command.rejected 落 AuditLog（无 prompt 正文）。
- skill.explicitly_selected：只记 skill_id。
- execution.cancel_requested / cancelled：/stop 全链路审计。
- 指标：CommandMetrics 快照含命令计数与延迟。
- chaos：30s 模型调用中 /stop 秒级返回 + PG 即时 CANCELLING。

先写测试记 RED：审计/指标/span 接线在 TASK-007 实现前不存在。
"""

from __future__ import annotations

import asyncio
import time
from contextlib import suppress

import pytest
from sqlalchemy import select

from fluxion.plugins.channel_adapters import StubImChannelAdapter
from fluxion.plugins.contracts import ModelRequest, ModelResponse
from fluxion.plugins.model_provider import ModelProviderRegistry
from fluxion.protocols.channel import ExternalChannelMessage
from fluxion.registry import PostgreSQLRegistryStore
from fluxion.registry.schema import audit_logs
from fluxion.resources import ResourceKind
from fluxion.services.channel_app import ChannelApplicationService
from fluxion.services.runtime_app import RunRuntimeRequest, RuntimeApplicationService
from tests.channel_helpers import RecordingRuntime, verified_identity
from tests.runtime_helpers import TEST_POSTGRES_DSN, publish_resource, seed_agent_definition


async def _setup_channel() -> tuple[PostgreSQLRegistryStore, RecordingRuntime, ChannelApplicationService]:
    store = PostgreSQLRegistryStore(TEST_POSTGRES_DSN, reset_on_initialize=True)
    await store.initialize()
    runtime = RecordingRuntime()
    service = ChannelApplicationService(store, runtime)
    await service.create_platform_user("tenant-a", "user-a")
    await publish_resource(
        store, tenant_id="tenant-a", kind=ResourceKind.RUNTIME_PROFILE,
        resource_id="assistant", version="1", spec={"max_rounds": 8, "default": True},
    )
    await seed_agent_definition(store, system_prompt="你是测试代理。")
    issued = await service.issue_bind_code("tenant-a", "user-a")
    bound = await service.handle(
        StubImChannelAdapter(), _im(f"/bind {issued.code}", "m-bind"), verified=None
    )
    assert bound.kind == "bound"
    return store, runtime, service


def _im(content: str, message_id: str) -> ExternalChannelMessage:
    return ExternalChannelMessage(
        tenant_id="tenant-a", channel_user_id="im-1", conversation_id="conv-im-1",
        message_id=message_id, content=content, agent_id="assistant",
    )


async def _audit_actions(store: PostgreSQLRegistryStore, action: str) -> list[dict]:
    async with store.engine.connect() as connection:
        rows = (await connection.execute(select(audit_logs).where(audit_logs.c.action == action))).mappings().all()
    return [dict(row) for row in rows]


@pytest.mark.asyncio
async def test_OBS01_command_audit_and_metrics() -> None:
    store, runtime, service = await _setup_channel()
    try:
        me = verified_identity("im-1")
        await service.handle(StubImChannelAdapter(), _im("/help", "m-help"), verified=me)
        await service.handle(StubImChannelAdapter(), _im("/foo", "m-foo"), verified=me)

        executed = await _audit_actions(store, "command.executed")
        rejected = await _audit_actions(store, "command.rejected")
        assert any(r["after_json"].get("command") == "help" for r in executed)
        assert any(r["after_json"].get("command") == "foo" for r in rejected)
        # 审计不得含用户原文/secret。
        for row in (*executed, *rejected):
            blob = repr(row["after_json"])
            assert "sk-" not in blob

        snapshot = service.metrics.snapshot()
        assert snapshot["counters"].get(("help", "ok"), 0) >= 1
        assert snapshot["counters"].get(("foo", "unknown_command"), 0) >= 1
        assert runtime.requests == []
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_OBS01_skill_selection_audit_has_no_prompt() -> None:
    store, _runtime, service = await _setup_channel()
    del _runtime
    try:
        await publish_resource(
            store, tenant_id="tenant-a", kind=ResourceKind.SKILL, resource_id="s1",
            version="1",
            spec={"name": "s1", "instructions": "做事。", "required_capabilities": [], "visibility": "public"},
        )
        await seed_agent_definition(
            store, system_prompt="你是测试代理。", version="2",
            capabilities=[{"type": "skill", "capability_ref": "s1", "version_pin": "1"}],
        )
        me = verified_identity("im-1")
        result = await service.handle(
            StubImChannelAdapter(), _im("/skill s1 秘密任务内容", "m-skill"), verified=me
        )
        assert result.kind == "message"
        selected = await _audit_actions(store, "skill.explicitly_selected")
        assert len(selected) == 1
        assert selected[0]["after_json"].get("skill_id") == "s1"
        assert "秘密任务内容" not in repr(selected[0])
    finally:
        await store.close()


class BlockingProvider:
    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def complete(self, request: ModelRequest) -> ModelResponse:
        del request
        self.entered.set()
        await asyncio.sleep(30)
        return ModelResponse(provider_id="test", content="done")


@pytest.mark.asyncio
async def test_OBS01_chaos_stop_latency_bounded() -> None:
    """chaos：30s 模型调用中 /stop 必须秒级返回，PG 即时可见 CANCELLING。"""
    store = PostgreSQLRegistryStore(TEST_POSTGRES_DSN, reset_on_initialize=True)
    await store.initialize()
    blocking = BlockingProvider()
    providers = ModelProviderRegistry()
    providers.register("test", blocking)
    service = RuntimeApplicationService(store, model_providers=providers)
    await service.initialize()
    try:
        await publish_resource(
            store, tenant_id="tenant-a", kind=ResourceKind.RUNTIME_PROFILE,
            resource_id="assistant", version="1", spec={"max_rounds": 8, "default": True},
        )
        await seed_agent_definition(store, system_prompt="你是测试代理。")
        from uuid import uuid4

        exec_id = f"exec_{uuid4().hex}"
        task = asyncio.ensure_future(
            service.run(
                RunRuntimeRequest(
                    tenant_id="tenant-a", user_id="user-a", runtime_profile_id="assistant",
                    session_id="sess_chaos_1", input_message="hi", agent_definition_id="assistant",
                    request_id=f"req_{uuid4().hex}", trace_id=f"trace_{uuid4().hex}",
                    execution_id=exec_id,
                )
            )
        )
        try:
            await asyncio.wait_for(blocking.entered.wait(), timeout=10)
            started = time.perf_counter()
            result = await service.cancel_active_execution(
                tenant_id="tenant-a", user_id="user-a",
                agent_definition_id="assistant", session_id="sess_chaos_1",
            )
            stop_latency = time.perf_counter() - started
            assert result.code == "stop_requested"
            assert stop_latency < 5.0, f"/stop 返回 {stop_latency:.1f}s，超出 5s 上限"
            record = await store.get_execution(tenant_id="tenant-a", execution_id=exec_id)
            assert record is not None and record.state in ("cancelling", "cancelled")
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=10)
        finally:
            blocking.release.set()
            task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await task
    finally:
        await service.close()
        await store.close()
