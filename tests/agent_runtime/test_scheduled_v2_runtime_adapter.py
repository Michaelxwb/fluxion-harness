"""TASK-018：Runtime 生产路径绑定 TASK-009 的中立 Kernel 端口。

真实边界（不 mock 适配器本身）：

- **真实 PostgreSQL**（隔离克隆库）：`MemoryConfirmationService` 真的落 `RunInterrupt`、
  canonical 事件、`RunContinuation` 检查点，并真的把 Run 置 `WAITING_INPUT`、释放租约；
- **真实生产工厂**：`default_executor_factory`（`main` 装配根注入的就是它）与真实 HTTP 模型端点，
  证明装配出来的执行器用的是同一份中立端口，而不是另一条宿主私有旁路；
- **共享契约**：`tests.kernel_adapter_contract` 的同一组用例在 Runtime 适配器上跑一遍——
  TASK-019 的 Worker 适配器要跑同一组，宿主差异只允许出现在适配器里。

模型传输在共享契约里是脚本化 provider（Kernel 契约不测 HTTP）；真实 HTTP 边界由生产工厂
用例覆盖。压缩/整轮外置的降级由 TASK-009 的 Kernel 契约与 `test_context_compaction_*` 覆盖。
"""

from __future__ import annotations

import ast
import functools
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import httpx
import pytest
from muad_agent_core.agent import (
    AgentPolicy,
    AgentRunner,
    AgentRunRequest,
    AgentRunStatus,
    RunnerCancelled,
    SuspensionKind,
)
from muad_agent_core.agent.continuation import RunnerCheckpoint
from muad_agent_core.hooks import HookEvent, HookPipeline
from muad_agent_core.model import ModelMessage, ModelRole, ModelToolCall
from muad_agent_core.tools import ToolCall, ToolDefinition, ToolEffect, ToolRegistry, ToolResult
from muad_agent_core.tools.pipeline import ToolExecutionPipeline
from muad_agent_runtime.application import executor as executor_module
from muad_agent_runtime.application.async_tools.continuation_service import ExecutionIdentity
from muad_agent_runtime.application.async_tools.event_port import DurableRuntimeEventPort
from muad_agent_runtime.application.execution_checkpoint import checkpoint_of
from muad_agent_runtime.application.human_question import HumanQuestion
from muad_agent_runtime.application.memory_confirmation import MemoryConfirmationService
from muad_agent_runtime.infrastructure.models.async_tools import RunContinuation
from muad_contracts.memory import RememberMemoryInput
from muad_contracts.platform_settings import CompactionSettings
from sqlalchemy import select

from tests.agent_runtime.memory_confirmation_support import (
    candidate,
    round_callbacks,
    round_recorder,
    source,
    state,
)
from tests.agent_runtime.memory_context_support import executor_request
from tests.agent_runtime.memory_executor_support import execute, tool_call
from tests.kernel_adapter_contract import (
    CONTRACT_CASES,
    Observation,
    RoundPlan,
    ScriptedModelProvider,
    text_reply,
    tool_call_reply,
)

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]


@dataclass
class _RecordingSuspension:
    """真实适配器的装饰器：只记录 Kernel 交来的 checkpoint，挂起仍由真实服务落库。"""

    inner: Any
    checkpoints: list[RunnerCheckpoint] = field(default_factory=list)
    order: list[str] | None = None
    staged: Any = None

    @property
    def suspend_request(self) -> Any:
        return self.inner.suspend_request

    async def suspend(self, progress: RunnerCheckpoint) -> None:
        self.staged = self.inner.suspend_request
        if self.order is not None:
            self.order.append("suspend")
        self.checkpoints.append(progress)
        await self.inner.suspend(progress)


class RuntimeAdapterHarness:
    """把真实 Runtime 适配器绑到共享 Kernel 契约上（宿主差异只在这里）。"""

    label = "runtime"
    kind = SuspensionKind.HUMAN_INPUT
    reason = "MEMORY_CONFIRMATION"
    expected_status = AgentRunStatus.WAITING_INPUT
    pending: tuple[Any, ...] = ()

    def __init__(
        self,
        factory: Any,
        run: Any,
        identity: ExecutionIdentity,
        submission_id: Any,
    ) -> None:
        self._factory = factory
        self._run = run
        self._identity = identity
        self._submission_id = submission_id
        self._suspension = _RecordingSuspension(inner=None)
        self._history: tuple[ModelMessage, ...] = (ModelMessage(ModelRole.USER, "记住蓝色"),)

    @property
    def factory(self) -> Any:
        return self._factory

    @property
    def run_record(self) -> Any:
        return self._run

    @property
    def prompt(self) -> str:
        assert self._suspension.staged is not None, "适配器还没产生挂起请求"
        return str(self._suspension.staged.prompt)

    def staging_call(self, call_id: str = "call-1") -> ModelToolCall:
        arguments = candidate().model_dump(mode="json")
        return ModelToolCall(call_id, "remember", arguments)

    def _registry(self, observation: Observation) -> ToolRegistry:
        memory = MemoryConfirmationService(
            self._factory, self._identity, submission_id=self._submission_id
        )
        self._suspension.inner = memory
        self._suspension.checkpoints = observation.checkpoints
        self._suspension.order = observation.order

        async def remember(arguments: dict[str, Any], *, call: ToolCall) -> ToolResult:
            observation.executed.append(call.call_id)
            receipt = await memory.propose(
                RememberMemoryInput.model_validate(arguments), call_id=call.call_id
            )
            return ToolResult(receipt.model_dump_json())

        async def echo(arguments: dict[str, Any], *, call: ToolCall) -> ToolResult:
            observation.executed.append(call.call_id)
            return ToolResult("ok")

        registry = ToolRegistry()
        registry.register(
            ToolDefinition(
                name="remember",
                description="stages a memory confirmation",
                input_schema=RememberMemoryInput.model_json_schema(),
                effect=ToolEffect.WRITE,
                handler=remember,
                externalizable_result=False,
            )
        )
        registry.register(
            ToolDefinition(
                name="echo",
                description="echo",
                input_schema={"type": "object"},
                effect=ToolEffect.READ,
                handler=echo,
            )
        )
        return registry

    async def run(self, plan: RoundPlan) -> Observation:
        return await self._execute(plan)

    async def resume(self) -> Observation:
        prior = await self.run(RoundPlan((tool_call_reply(self.staging_call()),)))
        assert prior.status is self.expected_status
        checkpoint = await self._persisted_checkpoint()
        call = self.staging_call()
        history = (
            ModelMessage(ModelRole.USER, "记住蓝色"),
            ModelMessage(ModelRole.ASSISTANT, "", tool_calls=(call,)),
            ModelMessage(ModelRole.TOOL, prior.tool_results[0][3], tool_call_id=call.id),
        )
        # 生产的恢复先由 RunService 解决中断/接管租约；契约只证明同一 Kernel 读回宿主
        # 自己落库的 checkpoint，不重跑已核实的工具，因此这里不再触发事件端口写检查点。
        return await self._execute(
            RoundPlan((text_reply("已确认"),)), history=history, checkpoint=checkpoint,
            runtime_events=False,
        )

    async def _persisted_checkpoint(self) -> RunnerCheckpoint:
        async with self._factory() as session:
            row = await session.scalar(
                select(RunContinuation).where(RunContinuation.run_id == self._run.id)
            )
        assert row is not None, "挂起必须把 Kernel checkpoint 落进 RunContinuation"
        return checkpoint_of(row)

    async def _execute(
        self,
        plan: RoundPlan,
        *,
        history: tuple[ModelMessage, ...] | None = None,
        checkpoint: RunnerCheckpoint | None = None,
        runtime_events: bool = True,
    ) -> Observation:
        observation = Observation()
        provider = ScriptedModelProvider(plan.script)
        registry = self._registry(observation)
        recorder = round_recorder(self._factory, self._run)
        hooks = HookPipeline()

        async def on_interrupt(context: Any) -> None:
            observation.interrupts.append(dict(context.payload))

        hooks.register(HookEvent.ON_INTERRUPT, on_interrupt)
        assistant, completed = round_callbacks(self._factory, self._run)

        async def on_assistant(turn) -> None:
            observation.order.append("assistant")
            await assistant(turn)

        async def on_tool_prepared(prepared) -> None:
            observation.order.append("tool_started")

        async def on_tool_completed(event) -> None:
            observation.order.append("tool_completed")
            observation.tool_results.append(
                (event.tool_call_id, event.tool_name, event.status, event.content)
            )
            await completed(event)

        runner = AgentRunner(
            provider=provider,
            registry=registry,
            hooks=hooks,
            tool_pipeline=ToolExecutionPipeline(registry=registry, audit=recorder, hooks=hooks),
            tool_round_results=recorder,
            runtime_events=(
                DurableRuntimeEventPort(self._factory, self._identity) if runtime_events else None
            ),
            tool_suspension=self._suspension,
        )
        policy = (
            AgentPolicy() if plan.max_tool_calls is None
            else AgentPolicy(max_tool_calls=plan.max_tool_calls)
        )
        try:
            result = await runner.run(
                AgentRunRequest(
                    model_id="probe",
                    instructions="",
                    messages=history or self._history,
                    policy=policy,
                ),
                is_cancelled=(lambda: True) if plan.cancel else None,
                on_assistant_turn=on_assistant,
                on_tool_prepared=on_tool_prepared,
                on_tool_completed=on_tool_completed,
                checkpoint=checkpoint,
            )
        except RunnerCancelled:
            observation.cancelled = True
        else:
            observation.status = result.status
            observation.final_text = result.final_text
        observation.model_calls = provider.call_count
        return observation


@pytest.fixture
async def runtime_adapter(async_tool_database) -> RuntimeAdapterHarness:
    factory, run, identity, submission_id = await source(async_tool_database)
    return RuntimeAdapterHarness(factory, run, identity, submission_id)


@pytest.mark.parametrize("case", CONTRACT_CASES, ids=lambda case: case.__name__)
async def test_runtime_adapter_satisfies_the_shared_kernel_contract(
    runtime_adapter: RuntimeAdapterHarness, case: Any
) -> None:
    await case(runtime_adapter)


async def test_suspension_is_persisted_through_the_runtime_owner(
    runtime_adapter: RuntimeAdapterHarness,
) -> None:
    """挂起交由 Runtime 落库：interrupt、Run 状态/租约与 checkpoint 一次事务收口。"""
    observation = await runtime_adapter.run(
        RoundPlan((tool_call_reply(runtime_adapter.staging_call()),))
    )
    assert observation.status is AgentRunStatus.WAITING_INPUT
    current, interrupts, saved, canonical = await state(
        runtime_adapter.factory, runtime_adapter.run_record
    )
    assert current.status == "WAITING_INPUT" and current.lease_owner is None
    assert [row.interrupt_type for row in interrupts] == ["MEMORY_CONFIRMATION"]
    assert saved is not None and (saved.turns, saved.tool_calls) == (1, 1)
    # 恢复用冻结的那份 Snapshot：挂起不改写快照，检查点也钉在同一个 snapshot_id 上。
    assert saved.snapshot_id == runtime_adapter.run_record.snapshot_id
    assert current.snapshot_id == runtime_adapter.run_record.snapshot_id
    confirmation = [event for event in canonical if event.stream_type == "memory.confirmation_required"]
    assert len(confirmation) == 1
    # 事件封套与序号不漂移：等待阶段行先于确认事件，且确认事件带本项目冻结的封套字段。
    stage = [event for event in canonical if event.stream_type == "run.stage"]
    assert stage and max(event.seq for event in stage) < confirmation[0].seq
    payload = confirmation[0].payload_json
    assert set(payload) >= {"proposal_id", "prompt", "text_prompt", "expires_at", "confirmation"}
    assert payload["prompt"] == runtime_adapter.prompt


async def test_production_factory_assembles_the_neutral_runtime_adapter(
    async_tool_database, monkeypatch
) -> None:
    """`default_executor_factory`（装配根唯一入口）真的把中立端口接进 Kernel。"""
    factory, run, identity, submission_id = await source(async_tool_database)
    monkeypatch.setattr(executor_module, "get_session_factory", lambda: factory)

    async def cancelled() -> bool:
        return False

    request = executor_request(run, "http://127.0.0.1:1/v1", cancelled)
    request = replace(
        request,
        execution_identity=identity,
        compaction=CompactionSettings(),
        runtime_events=DurableRuntimeEventPort(factory, identity),
        run_context=replace(request.run_context, submission_id=submission_id),
    )
    executor = await executor_module.default_executor_factory(request)
    try:
        runner = executor._runner  # noqa: SLF001 —— 装配断言：执行器内部就是这一份 Kernel
        assert isinstance(runner._runtime_events, DurableRuntimeEventPort)  # noqa: SLF001
        assert isinstance(runner._tool_suspension, HumanQuestion)  # noqa: SLF001
        assert runner._tool_suspension.suspend_request is None  # noqa: SLF001
        # 清单第 2 条：模型池/压缩/结果外置/工具策略仍是这条生产路径的组成部分。
        assert runner._context_compactor is not None  # noqa: SLF001
        assert runner._round_results is not None  # noqa: SLF001
        assert runner._tool_pipeline is not None  # noqa: SLF001
    finally:
        await executor._close()  # noqa: SLF001


async def test_production_factory_run_pauses_on_the_neutral_port(
    async_tool_database, monkeypatch
) -> None:
    """生产工厂 + 真实 HTTP 模型：记忆确认走中立挂起，产出同一份等待事实。"""
    factory, run, identity, submission_id = await source(async_tool_database)
    arguments = candidate().model_dump(mode="json")
    events, captured = await execute(
        factory, run, identity, submission_id,
        [tool_call("remember", arguments)], monkeypatch,
    )
    assert len(captured) == 1
    assert events[-1].data["status"] == "WAITING_INPUT"
    current, interrupts, checkpoint, canonical = await state(factory, run)
    assert current.status == "WAITING_INPUT" and current.lease_owner is None
    assert [row.interrupt_type for row in interrupts] == ["MEMORY_CONFIRMATION"]
    assert checkpoint is not None and (checkpoint.turns, checkpoint.tool_calls) == (1, 1)
    assert any(event.stream_type == "memory.confirmation_required" for event in canonical)


async def test_bootstrap_builds_the_single_production_factory() -> None:
    """装配根只造一份执行器工厂：`main._build_executor_factory` 委托给生产工厂本身。"""
    from muad_agent_runtime import main
    from muad_agent_runtime.application.executor import default_executor_factory
    from muad_common import process_settings

    settings = process_settings()
    pools = type(
        "Pools",
        (),
        {
            "model": httpx.AsyncClient(),
            "mcp": httpx.AsyncClient(),
            "egress": httpx.AsyncClient(),
        },
    )()
    try:
        factory = main._build_executor_factory(settings, pools, object())  # noqa: SLF001
    finally:
        for client in (pools.model, pools.mcp, pools.egress):
            await client.aclose()
    assert isinstance(factory, functools.partial)
    assert factory.func is default_executor_factory
    assert factory.keywords["model_client"] is pools.model
    assert factory.keywords["mcp_client"] is pools.mcp


def test_runtime_production_has_no_second_agent_loop() -> None:
    """Runtime 生产代码只有一处 `(Agent)Runner(...)` 装配，且它接的是中立端口。"""
    calls: list[ast.Call] = []
    for path in sorted((ROOT / "apps/agent-runtime/src").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        calls.extend(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and (
                (isinstance(node.func, ast.Name) and node.func.id in {"AgentRunner", "Runner"})
                or (isinstance(node.func, ast.Attribute) and node.func.attr in {"AgentRunner"})
            )
        )
    assert len(calls) == 1, [call.func for call in calls]
    keywords = {keyword.arg for keyword in calls[0].keywords}
    assert {"tool_suspension", "runtime_events", "context_compactor", "tool_round_results"} <= keywords
