"""STP-01 / STP-02（integration）：单 Pod Execution Control 验收。

- STP-01：RUNNING→CANCELLING→CANCELLED 全链路（真 Runtime + 真 PG + 阻塞模型）。
- STP-02：CAS 语义——完成与 stop 竞争首次终态获胜；重复 /stop 幂等；
  并发双消息 `session_busy`；`/stop` + `/status` 命令映射。

先写测试记 RED：ExecutionControlStore、内部 API、/stop、/status 在
TASK-005 实现前不存在。
"""

from __future__ import annotations

import asyncio
from contextlib import suppress

import pytest

from fluxion.plugins.channel_adapters import StubImChannelAdapter
from fluxion.plugins.contracts import ModelRequest, ModelResponse
from fluxion.plugins.model_provider import ModelProviderRegistry
from fluxion.protocols.channel import ExternalChannelMessage
from fluxion.registry import PostgreSQLRegistryStore
from fluxion.resources import ResourceKind
from fluxion.services.channel_app import ChannelApplicationService
from fluxion.services.runtime_app import (
    RunRuntimeRequest,
    RuntimeApplicationService,
)
from tests.runtime_helpers import TEST_POSTGRES_DSN, publish_resource, seed_agent_definition


class BlockingProvider:
    """首个 complete 阻塞到放行/取消（模拟长任务模型调用）。"""

    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def complete(self, request: ModelRequest) -> ModelResponse:
        del request
        self.entered.set()
        await self.release.wait()
        return ModelResponse(provider_id="test", content="done")


async def _setup() -> tuple[PostgreSQLRegistryStore, RuntimeApplicationService, BlockingProvider]:
    store = PostgreSQLRegistryStore(TEST_POSTGRES_DSN, reset_on_initialize=True)
    await store.initialize()
    providers = ModelProviderRegistry()
    blocking = BlockingProvider()
    providers.register("test", blocking)
    service = RuntimeApplicationService(store, model_providers=providers)
    # 注意顺序：service.initialize() 会触发 reset 建库，必须先 initialize 再播种。
    await service.initialize()
    await publish_resource(
        store,
        tenant_id="tenant-a",
        kind=ResourceKind.RUNTIME_PROFILE,
        resource_id="assistant",
        version="1",
        spec={"max_rounds": 8, "default": True},
    )
    await seed_agent_definition(store, system_prompt="你是测试代理。")
    return store, service, blocking


def _run_request(execution_id: str, session_id: str = "sess_stop_1") -> RunRuntimeRequest:
    from uuid import uuid4

    return RunRuntimeRequest(
        tenant_id="tenant-a",
        user_id="user-a",
        runtime_profile_id="assistant",
        session_id=session_id,
        input_message="long task",
        agent_definition_id="assistant",
        request_id=f"req_{uuid4().hex}",
        trace_id=f"trace_{uuid4().hex}",
        execution_id=execution_id,
    )


def _exec_id() -> str:
    from uuid import uuid4

    return f"exec_{uuid4().hex}"


@pytest.mark.asyncio
async def test_STP01_stop_cancels_running_execution() -> None:
    from fluxion.services.execution_control_service import ExecutionControlService

    store, service, blocking = await _setup()
    try:
        control = ExecutionControlService(store, service_instance_id=service.service_instance_id)
        request = _run_request(_exec_id())
        task = asyncio.ensure_future(service.run(request))
        try:
            await asyncio.wait_for(blocking.entered.wait(), timeout=10)
            active = await control.get_active_for_session("tenant-a", "user-a", "assistant", "sess_stop_1")
            assert active is not None
            assert active.state == "running"

            result = await service.cancel_active_execution(
                tenant_id="tenant-a",
                user_id="user-a",
                agent_definition_id="assistant",
                session_id="sess_stop_1",
            )
            assert result.code == "stop_requested"

            with pytest.raises(asyncio.CancelledError):
                await task
            finished = await control.get("tenant-a", request.execution_id)
            assert finished is not None
            assert finished.state == "cancelled"

            again = await service.cancel_active_execution(
                tenant_id="tenant-a",
                user_id="user-a",
                agent_definition_id="assistant",
                session_id="sess_stop_1",
            )
            assert again.code == "nothing_to_stop"
        finally:
            blocking.release.set()
            if not task.done():
                task.cancel()
            with suppress(asyncio.CancelledError, Exception):  # 取消/收尾残留即预期
                await task
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_STP02_completed_execution_cannot_be_stopped() -> None:
    from fluxion.services.execution_control_service import ExecutionControlService

    store, service, blocking = await _setup()
    try:
        control = ExecutionControlService(store, service_instance_id=service.service_instance_id)
        blocking.release.set()
        request = _run_request(_exec_id(), session_id="sess_done_1")
        result = await service.run(request)
        assert result.output == "done"
        finished = await control.get("tenant-a", request.execution_id)
        assert finished is not None
        assert finished.state == "completed"

        stop = await service.cancel_active_execution(
            tenant_id="tenant-a",
            user_id="user-a",
            agent_definition_id="assistant",
            session_id="sess_done_1",
        )
        assert stop.code == "nothing_to_stop"
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_STP02_second_message_while_running_is_busy() -> None:
    from fluxion.services.runtime_contracts import RuntimeApplicationError

    _store, service, blocking = await _setup()
    del _store
    try:
        task = asyncio.ensure_future(service.run(_run_request(_exec_id(), session_id="sess_busy_1")))
        try:
            await asyncio.wait_for(blocking.entered.wait(), timeout=10)
            with pytest.raises(RuntimeApplicationError) as captured:
                await service.run(_run_request(_exec_id(), session_id="sess_busy_1"))
            assert captured.value.code == "session_busy"
        finally:
            blocking.release.set()
            task.cancel()
            with suppress(asyncio.CancelledError, Exception):  # 取消/收尾残留即预期
                await task
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_STP02_repeated_stop_is_idempotent() -> None:
    _store, service, blocking = await _setup()
    del _store
    try:
        task = asyncio.ensure_future(service.run(_run_request(_exec_id(), session_id="sess_rep_1")))
        try:
            await asyncio.wait_for(blocking.entered.wait(), timeout=10)
            first = await service.cancel_active_execution(
                tenant_id="tenant-a", user_id="user-a",
                agent_definition_id="assistant", session_id="sess_rep_1",
            )
            assert first.code == "stop_requested"
            second = await service.cancel_active_execution(
                tenant_id="tenant-a", user_id="user-a",
                agent_definition_id="assistant", session_id="sess_rep_1",
            )
            assert second.code in ("stop_already_requested", "nothing_to_stop")
        finally:
            blocking.release.set()
            task.cancel()
            with suppress(asyncio.CancelledError, Exception):  # 取消/收尾残留即预期
                await task
    finally:
        await service.close()


class StubGateway:
    """Channel 层 /stop + /status 映射测试替身（执行面由 STP-01/02 真测）。"""

    def __init__(self) -> None:
        self.cancel_code = "nothing_to_stop"
        self.status = StubStatus()

    async def run(self, request: RunRuntimeRequest):  # pragma: no cover
        raise AssertionError("not used")

    def stream(self, request: RunRuntimeRequest):  # pragma: no cover
        raise AssertionError("not used")

    async def cancel_active_execution(self, **kwargs: object):
        from fluxion.services.runtime_contracts import CancelExecutionResult

        del kwargs
        return CancelExecutionResult(code=self.cancel_code)

    async def get_session_status(self, **kwargs: object):
        del kwargs
        return self.status


class StubStatus:
    def __init__(self) -> None:
        self.state = "idle"
        self.session_id = "sess_x"
        self.execution_id: str | None = None
        self.agent_id = "assistant"
        self.requested_skill_id: str | None = None


def _im(content: str, message_id: str) -> ExternalChannelMessage:
    return ExternalChannelMessage(
        tenant_id="tenant-a",
        channel_user_id="im-1",
        conversation_id="conv-im-1",
        message_id=message_id,
        content=content,
        agent_id="assistant",
    )


@pytest.mark.asyncio
async def test_STP_stop_and_status_command_mapping() -> None:
    from tests.channel_helpers import verified_identity

    store = PostgreSQLRegistryStore(TEST_POSTGRES_DSN, reset_on_initialize=True)
    await store.initialize()
    try:
        gateway = StubGateway()
        service = ChannelApplicationService(store, gateway)  # type: ignore[arg-type]
        await service.create_platform_user("tenant-a", "user-a")
        await publish_resource(
            store, tenant_id="tenant-a", kind=ResourceKind.RUNTIME_PROFILE,
            resource_id="assistant", version="1",
            spec={"max_rounds": 8, "default": True},
        )
        await seed_agent_definition(store, system_prompt="你是测试代理。")
        issued = await service.issue_bind_code("tenant-a", "user-a")
        bound = await service.handle(
            StubImChannelAdapter(), _im(f"/bind {issued.code}", "m-bind"), verified=None
        )
        assert bound.kind == "bound"

        me = verified_identity("im-1")
        idle = await service.handle(StubImChannelAdapter(), _im("/stop", "m-stop-1"), verified=me)
        assert idle.to_payload()["code"] == "nothing_to_stop"

        gateway.cancel_code = "stop_requested"
        stopped = await service.handle(StubImChannelAdapter(), _im("/stop", "m-stop-2"), verified=me)
        assert stopped.to_payload()["code"] == "stop_requested"

        gateway.status.state = "running"
        gateway.status.execution_id = "exec_live"
        status = await service.handle(StubImChannelAdapter(), _im("/status", "m-status"), verified=me)
        payload = status.to_payload()
        assert payload["command"] == "status"
        assert "运行中" in payload["output"]
        assert "exec_live" in payload["output"]
    finally:
        await store.close()
