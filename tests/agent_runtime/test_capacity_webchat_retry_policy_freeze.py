"""B-237：[integration] 工具可信重试元数据冻结到操作策略（design §3.6「重试与外部副作用」）。

真实边界（不 mock 存储或租约）：
- 真实 Runtime 业务服务：`SkillToolSet` 的工具注册/处理器 + `DurableTaskSubmitter`（真实
  `tool_operation`/`tool_control_outbox` 写入）+ 真实 `ControlDispatcher` 经真实 Internal HTTP
  （Worker ASGI）提交；
- 隔离 PostgreSQL（空库 + `alembic upgrade head`，跑完即 drop，不触碰共享 dev 库）：Run/冻结
  快照/工具操作/控制出箱/Worker Task 只落在本次独立库；
- 冻结证据全部取自 PG 行事实：Runtime 侧
  `tool_operation.submission_json.execution_snapshot.budget` 与 Worker 侧
  `task_execution.execution_snapshot_json`。

关键断言（B-237 契约）：
1. ToolRegistry 的 Owner 侧声明（`ToolDefinition.retry_safety`）在操作提交时冻结进操作策略
   （`budget.retry_safety`），Worker 读取口径（`task_retry_safety`）与冻结面一致；
2. 未知/未声明/非法取值一律 NON_RETRYABLE；MCP `readOnlyHint` 派生的 effect 永不参与，
   agent runtime_config 混入的同名键也不能改写冻结值；
3. 一次冻结：同一 call_id 的重放不重新解析、不刷新——当前声明已变也读回原值；
4. 恢复只读冻结值（`frozen_operation_retry_safety`），不重新 tools/list、不猜任何提示字段；
5. Worker 读取的业务幂等键与 Run 操作一致（`runtime-op:<operation_id>:submit`），重放不变。
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
import sqlalchemy as sa
from muad_agent_core.tools import ToolEffect, ToolRegistry
from muad_agent_runtime.application.async_tools.control_dispatcher import ControlDispatcher
from muad_agent_runtime.application.async_tools.operations import (
    DurableTaskSubmitter,
    frozen_operation_retry_safety,
    reserve_operation,
)
from muad_agent_runtime.application.mcp_runtime_adapter import (
    McpRuntimeAdapter,
    McpServerDefinition,
    McpToolDefinition,
)
from muad_agent_runtime.application.skill_tools import EXECUTE_SKILL_TOOL, SkillToolSet
from muad_agent_runtime.application.task_client import TaskSubmissionContext
from muad_agent_runtime.infrastructure import db as runtime_db
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.async_tools import ToolControlOutbox, ToolOperation
from muad_agent_worker.infrastructure import db as worker_db
from muad_agent_worker.infrastructure.models.task import TaskExecution
from muad_agent_worker.main import app as worker_app
from muad_artifact_store import NfsArtifactStore, SkillArtifactCache
from muad_contracts import (
    DEFAULT_TOOL_RETRY_SAFETY,
    CompletionMode,
    ResolvedAgent,
    ResolveDefinitionResponse,
    ResolvedModel,
    ResolvedSkill,
    SkillExecutionMode,
    ToolRetrySafety,
    snapshot_hash,
)
from muad_contracts.tool_runtime import frozen_retry_safety, task_retry_safety
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.async_tool_submissions import seed_submission

pytestmark = pytest.mark.integration

TOKEN = "retry-policy-freeze-internal-token"
CALL_ID = "call-freeze-1"


# --------------------------------------------------------------------------- #
# 隔离数据存储：本任务边界
# --------------------------------------------------------------------------- #


@pytest.fixture
async def isolated_stack(
    async_tool_datastore: dict[str, str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """隔离 PostgreSQL/Redis 号位 + Runtime/Worker 进程环境；跑完 drop，不触碰共享 dev 库。"""
    monkeypatch.setenv("DATABASE_URL", async_tool_datastore["database_url"])
    monkeypatch.setenv("REDIS_URL", async_tool_datastore["redis_url"])
    monkeypatch.setenv("ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", TOKEN)
    await runtime_db.dispose_engine()
    await worker_db.dispose_engine()
    try:
        yield get_session_factory()
    finally:
        # 退出时清缓存：后续套件按已还原的环境重建 engine，不指向已 drop 的库。
        await runtime_db.dispose_engine()
        await worker_db.dispose_engine()


# --------------------------------------------------------------------------- #
# 真实来源：RUNNING Run + 冻结快照（可注入 agent runtime_config 的 budget）
# --------------------------------------------------------------------------- #


def _async_skill() -> ResolvedSkill:
    return ResolvedSkill(
        skill_id=uuid.uuid4(),
        artifact_id=uuid.uuid4(),
        key="async_effect",
        name="Async Effect",
        description="",
        version="1",
        checksum="sha256:" + "a" * 64,
        storage_key="skills/async_effect.zip",
        execution_mode=SkillExecutionMode.ASYNC,
    )


async def _seed_run(
    factory: async_sessionmaker[AsyncSession],
    *,
    injected_budget: dict[str, Any] | None = None,
) -> tuple[TaskSubmissionContext, ResolvedSkill, uuid.UUID]:
    """真实 Run 来源；`injected_budget` 模拟 agent runtime_config 里混入的 budget 键。"""
    runtime_config = {"budget": dict(injected_budget)} if injected_budget is not None else {}
    resolved = ResolveDefinitionResponse(
        agent=ResolvedAgent(
            id=uuid.uuid4(), key="freeze-agent", revision=1, instructions="", runtime_config=runtime_config
        ),
        model=ResolvedModel(
            id=uuid.uuid4(), revision=1, model_id="probe", base_url="http://model.invalid/v1", params={}
        ),
        skills=[_async_skill()],
        mcp_servers=[],
    )
    context, skill, run = await seed_submission(factory, pending_limit=4, resolved=resolved)
    return context, skill, run.id


def _tool_set(
    *,
    context: TaskSubmissionContext,
    factory: async_sessionmaker[AsyncSession],
    client: httpx.AsyncClient,
    tmp_path: Path,
    declaration: ToolRetrySafety = DEFAULT_TOOL_RETRY_SAFETY,
) -> SkillToolSet:
    """真实工具边界：声明落在 ToolRegistry，操作提交走真实 DurableTaskSubmitter。"""
    store = NfsArtifactStore(tmp_path / "nfs")
    store.root.mkdir(parents=True, exist_ok=True)
    cache = SkillArtifactCache(store, tmp_path / "cache")
    submitter = DurableTaskSubmitter(
        factory,
        ControlDispatcher(factory, client, "http://worker", service_token=TOKEN),
    )
    return SkillToolSet(
        cache=cache,
        skills=[context.skills[0]],
        timeout_sec=10.0,
        task_client=submitter,
        task_context=context,
        async_retry_safety=declaration,
    )


async def _operation(
    factory: async_sessionmaker[AsyncSession], operation_id: uuid.UUID
) -> ToolOperation:
    async with factory() as session:
        row = await session.get(ToolOperation, operation_id)
    assert row is not None, f"operation {operation_id} must exist"
    return row


async def _task(factory: async_sessionmaker[AsyncSession], task_id: uuid.UUID) -> TaskExecution:
    async with factory() as session:
        row = await session.get(TaskExecution, task_id)
    assert row is not None, f"task {task_id} must exist"
    return row


async def _tenant_rows(
    factory: async_sessionmaker[AsyncSession], model: type[Any], tenant_id: str
) -> int:
    """租户内计数（不跨租户取全局 count）。"""
    async with factory() as session:
        return int(
            await session.scalar(
                sa.select(sa.func.count())
                .select_from(model)
                .where(model.tenant_id == tenant_id)
            )
            or 0
        )


def _frozen_budget(operation: ToolOperation) -> dict[str, Any]:
    snapshot = operation.submission_json["execution_snapshot"]
    assert isinstance(snapshot, dict)
    budget = snapshot["budget"]
    assert isinstance(budget, dict)
    return budget


# --------------------------------------------------------------------------- #
# 1. 可信声明随 execution policy 冻结：Runtime 操作策略 + Worker Task 快照一致
# --------------------------------------------------------------------------- #


async def test_b237_trusted_declaration_freezes_into_operation_policy_and_worker_snapshot(
    isolated_stack: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    factory = isolated_stack
    context, skill, _run_id = await _seed_run(
        factory, injected_budget={"retry_safety": "READ_ONLY", "readOnlyHint": True}
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=worker_app)) as client:
        tool_set = _tool_set(
            context=context,
            factory=factory,
            client=client,
            tmp_path=tmp_path,
            declaration=ToolRetrySafety.IDEMPOTENT,
        )
        definition = tool_set.registry().get(EXECUTE_SKILL_TOOL)
        assert definition.retry_safety is ToolRetrySafety.IDEMPOTENT, "可信声明在 ToolRegistry（Owner 侧）"
        receipt = json.loads(
            await definition.handler({"skill_key": skill.key, "input": {"x": 1}}, call_id=CALL_ID)
        )

    operation = await _operation(factory, uuid.UUID(receipt["operation_id"]))
    snapshot = operation.submission_json["execution_snapshot"]
    # ① 冻结进操作策略：只认 Owner 侧声明，覆盖 runtime_config 混入的同名键与提示字段
    assert _frozen_budget(operation)["retry_safety"] == "IDEMPOTENT"
    assert frozen_retry_safety(_frozen_budget(operation)) is ToolRetrySafety.IDEMPOTENT
    assert task_retry_safety(snapshot) is ToolRetrySafety.IDEMPOTENT, "Worker 读取口径与冻结面一致"
    # 冻结值被快照哈希覆盖：Worker 用同一个 hash 核对 ack
    assert snapshot_hash(snapshot) == operation.task_snapshot_hash
    # ② 冻结进 Worker Task（真实 Internal HTTP）：业务幂等键与 Run 操作一致
    assert operation.task_id is not None
    task = await _task(factory, operation.task_id)
    assert task.idempotency_key == f"runtime-op:{operation.id}:submit"
    assert task.source_operation_id == operation.id
    assert task.source_tool_call_id == CALL_ID
    assert task.snapshot_hash == operation.task_snapshot_hash
    assert task_retry_safety(task.execution_snapshot_json) is ToolRetrySafety.IDEMPOTENT


# --------------------------------------------------------------------------- #
# 2. 未知/未声明/非法/提示：一律 NON_RETRYABLE
# --------------------------------------------------------------------------- #


async def test_b237_undeclared_mcp_hint_and_invalid_values_freeze_as_non_retryable(
    isolated_stack: async_sessionmaker[AsyncSession],
) -> None:
    factory = isolated_stack
    context, skill, _run_id = await _seed_run(
        factory, injected_budget={"retry_safety": "READ_ONLY", "readOnlyHint": True}
    )
    adapter = McpRuntimeAdapter(audit_writer=None)
    registry = ToolRegistry()
    adapter.register_catalog(
        registry=registry,
        tenant_id=context.tenant_id,
        run_id=cast(uuid.UUID, context.source_run_id),
        servers=[
            McpServerDefinition(
                mcp_server_id=uuid.uuid4(),
                key="srv",
                endpoint="http://mcp.invalid/mcp",
                catalog_revision=1,
                tools=[
                    McpToolDefinition(
                        name="read_thing",
                        description="",
                        input_schema={"type": "object", "properties": {}},
                        effect="READ",
                    )
                ],
            )
        ],
    )
    try:
        mcp_tool = registry.get("mcp::srv::read_thing")
        assert mcp_tool.effect is ToolEffect.READ, "目录 effect 来自 readOnlyHint 归一化"
        assert mcp_tool.retry_safety is ToolRetrySafety.NON_RETRYABLE, "提示派生的 effect 不授权重试"
        # MCP 目录工具（未声明重试安全）+ 注入的 READ_ONLY → 冻结仍是 NON_RETRYABLE
        mcp_operation = await reserve_operation(
            factory,
            context,
            skill=skill,
            input_data={"x": 1},
            call_id="call-mcp",
            completion_mode=CompletionMode.JOIN,
            retry_safety=mcp_tool.retry_safety,
        )
        assert _frozen_budget(mcp_operation)["retry_safety"] == "NON_RETRYABLE"
        assert frozen_operation_retry_safety(mcp_operation) is ToolRetrySafety.NON_RETRYABLE
        # 未知工具（调用方未给出可信声明）→ NON_RETRYABLE
        unknown_operation = await reserve_operation(
            factory,
            context,
            skill=skill,
            input_data={"x": 2},
            call_id="call-unknown",
            completion_mode=CompletionMode.JOIN,
        )
        assert _frozen_budget(unknown_operation)["retry_safety"] == "NON_RETRYABLE"
        # 非法取值（冻结面不得把它当成合法枚举）→ NON_RETRYABLE
        invalid_operation = await reserve_operation(
            factory,
            context,
            skill=skill,
            input_data={"x": 3},
            call_id="call-invalid",
            completion_mode=CompletionMode.JOIN,
            retry_safety=cast(ToolRetrySafety, "read_only"),
        )
        assert _frozen_budget(invalid_operation)["retry_safety"] == "NON_RETRYABLE"
    finally:
        await adapter.aclose()


# --------------------------------------------------------------------------- #
# 3. 一次冻结：重放不刷新、不重新解析；恢复只读冻结值
# --------------------------------------------------------------------------- #


async def test_b237_freeze_happens_once_and_replay_never_refreshes(
    isolated_stack: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    factory = isolated_stack
    context, skill, _run_id = await _seed_run(factory)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=worker_app)) as client:
        tool_set = _tool_set(
            context=context,
            factory=factory,
            client=client,
            tmp_path=tmp_path,
            declaration=ToolRetrySafety.IDEMPOTENT,
        )
        definition = tool_set.registry().get(EXECUTE_SKILL_TOOL)
        first = json.loads(
            await definition.handler({"skill_key": skill.key, "input": {"x": 1}}, call_id=CALL_ID)
        )
    operation = await _operation(factory, uuid.UUID(first["operation_id"]))
    assert task_retry_safety(operation.submission_json["execution_snapshot"]) is ToolRetrySafety.IDEMPOTENT

    # 重放：同一 call_id + 同一输入，但「当前声明」已变（等价于工具目录/配置变更）——
    # 冻结值必须原样复用，不重新解析、不刷新。
    replay = await reserve_operation(
        factory,
        context,
        skill=skill,
        input_data={"x": 1},
        call_id=CALL_ID,
        completion_mode=CompletionMode.JOIN,
        retry_safety=ToolRetrySafety.READ_ONLY,
    )
    assert replay.id == operation.id
    recovered = await _operation(factory, operation.id)
    assert task_retry_safety(recovered.submission_json["execution_snapshot"]) is ToolRetrySafety.IDEMPOTENT
    # 恢复读取只认冻结值：不重新 tools/list、不猜任何提示字段
    assert frozen_operation_retry_safety(recovered) is ToolRetrySafety.IDEMPOTENT
    # 业务幂等键与 Run 操作一致，重放不变
    assert recovered.submission_json["idempotency_key"] == f"runtime-op:{operation.id}:submit"
    assert replay.task_snapshot_hash == operation.task_snapshot_hash
    # 没有第二条操作、也没有第二条提交指令
    assert await _tenant_rows(factory, ToolOperation, context.tenant_id) == 1
    assert await _tenant_rows(factory, ToolControlOutbox, context.tenant_id) == 1
