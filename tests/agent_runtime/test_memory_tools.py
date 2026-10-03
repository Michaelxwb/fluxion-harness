"""[TASK-002] 记忆工具 remember / recall 与写入开关（真实 PostgreSQL）。"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Mapping
from dataclasses import replace
from functools import partial
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from muad_agent_core.agent import AgentPolicy, AgentRunner
from muad_agent_core.hooks import HookPipeline
from muad_agent_core.model import ModelRequest, ModelResponse, ModelRole, ModelToolCall
from muad_agent_core.tools import ToolRegistry
from muad_agent_runtime.application.attachments.tool_results import ArtifactResultWriter
from muad_agent_runtime.application.executor import (
    AgentRunnerExecutor,
    ExecutorRequest,
    ExecutorRunContext,
    ToolCallRecorder,
    build_registry,
)
from muad_agent_runtime.application.memory_service import (
    SOURCE_AGENT_INFERRED,
    SOURCE_USER_EXPLICIT,
    MemoryService,
)
from muad_agent_runtime.application.memory_tools import (
    MAX_RECALL_BYTES,
    RECALL_TOOL,
    REMEMBER_TOOL,
    MemoryScope,
    MemoryToolSet,
)
from muad_agent_runtime.infrastructure.audit_writer import RuntimeAuditWriter
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import ToolCallAudit, UserMemory
from muad_api import AppError
from muad_artifact_store import NfsArtifactStore, SkillArtifactCache
from muad_contracts import ResolvedAgent, ResolvedModel

TENANT = f"memtool-{uuid.uuid4()}"
RUN_ID = uuid.uuid4()
CONV_ID = uuid.uuid4()
USER_ID = uuid.uuid4()
OTHER_USER_ID = uuid.uuid4()


@pytest.fixture(autouse=True)
async def _cleanup() -> AsyncIterator[None]:
    yield
    async with get_session_factory()() as session:
        for model in (UserMemory, ToolCallAudit):
            await session.execute(model.__table__.delete().where(model.tenant_id == TENANT))
        await session.commit()


def _scope(user_id: uuid.UUID = USER_ID) -> MemoryScope:
    return MemoryScope(tenant_id=TENANT, user_id=user_id, run_id=RUN_ID)


def _tool_set(service: MemoryService | None = None, *, write_enabled: bool = True) -> MemoryToolSet:
    return MemoryToolSet(
        service=service or MemoryService(), scope=_scope(), write_enabled=write_enabled
    )


async def _call(tool_set: MemoryToolSet, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    definition = tool_set._definitions()
    handler = next(item.handler for item in definition if item.name == name)
    assert handler is not None
    return json.loads(await handler(dict(arguments), call_id=f"call-{uuid.uuid4()}"))


async def _upsert(key: str, value: str, source_type: str = SOURCE_USER_EXPLICIT) -> None:
    await MemoryService().upsert(
        tenant_id=TENANT, user_id=USER_ID, category="PREFERENCE",
        memory_key=key, content_json={"value": value}, source_type=source_type,
    )


async def _memory_rows(user_id: uuid.UUID) -> list[UserMemory]:
    async with get_session_factory()() as session:
        return (
            await session.execute(
                sa.select(UserMemory).where(
                    UserMemory.tenant_id == TENANT, UserMemory.user_id == user_id
                )
            )
        ).scalars().all()


def _request(runtime_config: dict[str, Any] | None = None) -> ExecutorRequest:
    return ExecutorRequest(
        agent=ResolvedAgent(
            id=uuid.uuid4(), key="demo-agent", revision=1, instructions="be helpful",
            runtime_config=runtime_config or {},
        ),
        model=ResolvedModel(
            id=uuid.uuid4(), revision=1, model_id="gpt-4o-mini", base_url="https://llm.test/v1"
        ),
        input_text="remember this",
        is_cancel_requested=_never_cancelled,
        run_context=ExecutorRunContext(
            tenant_id=TENANT, run_id=RUN_ID, conversation_id=CONV_ID, user_id=USER_ID
        ),
    )


async def _never_cancelled() -> bool:
    return False


def _cache(tmp_path: Path) -> SkillArtifactCache:
    return SkillArtifactCache(NfsArtifactStore(tmp_path / "artifacts"), tmp_path / "cache")


def _writer() -> RuntimeAuditWriter:
    return RuntimeAuditWriter(
        tenant_id=TENANT, run_id=RUN_ID, task_id=None,
        conversation_id=CONV_ID, user_id=USER_ID, session_factory=get_session_factory,
    )


def _recording_registry(tmp_path: Path, tool_set: MemoryToolSet) -> ToolRegistry:
    """把工具集包进生产同款 `ToolCallRecorder`（审计 + 大结果外置判定都走真实链路）。"""
    registry = ToolRegistry()
    tool_set.register(registry)
    recorder = ToolCallRecorder(
        context=ExecutorRunContext(
            tenant_id=TENANT, run_id=RUN_ID, conversation_id=CONV_ID, user_id=USER_ID
        ),
        audit_writer=_writer(),
        artifact_writer=ArtifactResultWriter(tmp_path),
    )
    wrapped = ToolRegistry()
    for definition in registry.list():
        handler = definition.handler
        assert handler is not None
        wrapped.register(replace(definition, handler=partial(recorder, definition, handler=handler)))
    return wrapped


async def _call_registry(registry: ToolRegistry, name: str, arguments: Mapping[str, Any]) -> str:
    definition = registry.get(name)
    assert definition.handler is not None
    return await definition.handler(dict(arguments), call_id=f"call-{uuid.uuid4()}")


# --------------------------------------------------------------------------- B-01


async def test_b01_memory_key_format_is_enforced_without_raising() -> None:
    """[B-01][unit] `memory_key` 边界：64 字符合法；65 字符/含大写/含空格拒绝并回错误码。"""
    tool_set = _tool_set()
    valid = "a" + "b" * 63
    assert len(valid) == 64

    ok = await _call(tool_set, REMEMBER_TOOL, _remember_args(memory_key=valid))
    assert ok["saved"] is True

    for bad in ("a" + "b" * 64, "Reply.Language", "reply language", "", "-leading"):
        payload = await _call(tool_set, REMEMBER_TOOL, _remember_args(memory_key=bad))
        assert payload["saved"] is False
        assert payload["error_code"] == "MEMORY_KEY_INVALID"

    assert len(await _memory_rows(USER_ID)) == 1  # 只有合法那条落库


# --------------------------------------------------------------------------- E-02 / E-03


async def test_e02_missing_source_type_is_rejected_without_writing() -> None:
    """[E-02][unit] 缺 `source_type` → 错误码、库内无写入、对话不中断（返回字符串而非抛异常）。"""
    tool_set = _tool_set()
    arguments = _remember_args()
    del arguments["source_type"]

    payload = await _call(tool_set, REMEMBER_TOOL, arguments)

    assert payload["saved"] is False
    assert payload["error_code"] == "MEMORY_SOURCE_TYPE_INVALID"
    assert await _memory_rows(USER_ID) == []


async def test_e03_disallowed_category_is_rejected() -> None:
    """[E-03][unit] 非白名单 category（策略/授权类）→ 拒绝写入且错误码可读。"""
    tool_set = _tool_set()

    payload = await _call(tool_set, REMEMBER_TOOL, _remember_args(category="SYSTEM_POLICY"))

    assert payload["saved"] is False
    assert payload["error_code"] == "MEMORY_CATEGORY_NOT_ALLOWED"
    assert await _memory_rows(USER_ID) == []


# --------------------------------------------------------------------------- B-03


async def test_b03_recall_limit_bounds() -> None:
    """[B-03][unit] `limit`：0 / 21 越界拒绝，缺省取 10，1 / 20 合法。"""
    tool_set = _tool_set()
    for index in range(25):
        await _upsert(f"key.{index:02d}", f"value-{index}")

    for bad in (0, 21, -1):
        payload = await _call(tool_set, RECALL_TOOL, {"limit": bad})
        assert payload["error_code"] == "MEMORY_LIMIT_INVALID"
        assert payload["items"] == []

    default_limit = await _call(tool_set, RECALL_TOOL, {})
    assert len(default_limit["items"]) == 10

    for value in (1, 20):
        payload = await _call(tool_set, RECALL_TOOL, {"limit": value})
        assert "error_code" not in payload
        assert len(payload["items"]) == value


# --------------------------------------------------------------------------- E-01


async def test_e01_cross_user_write_is_structurally_impossible() -> None:
    """[E-01][integration] 处理器入参里塞他人 `user_id` 也只写当前 Run 的用户。

    工具 schema 不接受该参数，故以直接调处理器的方式验证：入参中的身份信息被**忽略**，
    写入始终归属 `MemoryScope`（来自 Run 上下文）。
    """
    tool_set = _tool_set()

    payload = await _call(
        tool_set,
        REMEMBER_TOOL,
        {**_remember_args(memory_key="who.am.i"), "user_id": str(OTHER_USER_ID), "tenant_id": "other"},
    )

    assert payload["saved"] is True
    mine = await _memory_rows(USER_ID)
    assert [row.memory_key for row in mine] == ["who.am.i"]
    assert await _memory_rows(OTHER_USER_ID) == []


# --------------------------------------------------------------------------- E-04


async def test_e04_memory_write_switch_controls_remember_registration(tmp_path: Path) -> None:
    """[E-04][integration] `memory_write=false` 时注册表里没有 `remember`；缺配置（默认开）时有。"""
    disabled = build_registry(
        request=_request({"memory_write": False}),
        cache=_cache(tmp_path),
        audit_writer=None,
        mcp_adapter=None,
    )
    names = [definition.name for definition in disabled.list()]
    assert REMEMBER_TOOL not in names
    assert RECALL_TOOL in names  # 读侧不随写开关变化

    enabled = build_registry(
        request=_request(), cache=_cache(tmp_path), audit_writer=None, mcp_adapter=None
    )
    default_names = [definition.name for definition in enabled.list()]
    assert REMEMBER_TOOL in default_names

    explicit_true = build_registry(
        request=_request({"memory_write": True}),
        cache=_cache(tmp_path),
        audit_writer=None,
        mcp_adapter=None,
    )
    assert REMEMBER_TOOL in [item.name for item in explicit_true.list()]


# --------------------------------------------------------------------------- E-05


class _FailingService(MemoryService):
    async def upsert(self, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("database is unreachable")


async def test_e05_write_failure_degrades_without_breaking_conversation(tmp_path: Path) -> None:
    """[E-05][integration] 写失败 → 审计 `status=ERROR` + 错误码、库内无半截行、**对话不中断**。

    写失败**必须抛出**而不是被处理器吞成 `{"saved": false}`：吞掉之后 `tool_call_audit` 会记成
    `OK`，失败在审计与 `memory_write_total{status=error}` 指标上彻底消失（`load_skill` 事故的
    教训正是"没有任何报错"）。不中断对话由 AgentRunner 的工具异常兜底承载 —— 见下一个用例。
    """
    registry = _recording_registry(tmp_path, _tool_set(_FailingService()))

    with pytest.raises(AppError):
        await _call_registry(registry, REMEMBER_TOOL, _remember_args(memory_key="will.fail"))

    assert await _memory_rows(USER_ID) == []

    async with get_session_factory()() as session:
        audit = (
            await session.execute(
                sa.select(ToolCallAudit).where(ToolCallAudit.tenant_id == TENANT)
            )
        ).scalar_one()
    assert audit.tool_name == REMEMBER_TOOL
    assert audit.status == "ERROR"
    assert audit.error_code == "COMMON_INTERNAL_ERROR"


async def test_e05_write_failure_keeps_the_model_turn_going(tmp_path: Path) -> None:
    """[E-05][integration] 写失败后**模型回合仍继续**：Runner 把工具异常转成工具失败消息喂回模型。

    真实边界：真实 `AgentRunner` + 生产同款 recorder 包装的注册表（异常穿过 recorder 的审计
    `finally` 后被 Runner 捕获）。断言最终回合产出答复，且模型看到的工具消息表明失败 ——
    因此模型不会宣称"已记住"。
    """
    registry = _recording_registry(tmp_path, _tool_set(_FailingService()))
    provider = _RememberThenAnswerProvider()
    executor = AgentRunnerExecutor(
        runner=AgentRunner(provider=provider, registry=registry, hooks=HookPipeline()),
        request=_request(),
    )

    events = [event async for event in executor.run()]

    final_text = "".join(str(event.data.get("delta", "")) for event in events)
    assert final_text == "final answer"
    assert len(provider.requests) == 2  # 工具失败没有吃掉后续模型回合
    tool_message = provider.requests[1].messages[-1]
    assert tool_message.role is ModelRole.TOOL
    # 工具失败必须以**结构化错误体**回给模型（与 skill/task/memory 工具自己返回的
    # `{"error": {"code", "message"}}` 同形），而不是一句散文 —— 模型据此知道"没记住"。
    # 2026-10-03 前这里断言的是 `"tool failed" in content`：那是抛出型工具独有的散文形状，
    # 与返回型工具的说法不一致，且模型拿不到错误码。
    payload = json.loads(str(tool_message.content))
    assert payload["error"]["code"] == "COMMON_INTERNAL_ERROR"


class _RememberThenAnswerProvider:
    """先调一次 `remember`（会失败），再给出最终答复。"""

    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if len(self.requests) == 1:
            return ModelResponse(
                content="",
                finish_reason="tool_calls",
                tool_calls=(
                    ModelToolCall(id="call-mem", name=REMEMBER_TOOL, arguments=_remember_args()),
                ),
            )
        return ModelResponse(content="final answer", finish_reason="stop")


# --------------------------------------------------------------------------- B-04


async def test_b04_recall_result_is_not_truncated_by_artifact_externalization(tmp_path: Path) -> None:
    """[B-04][integration] 20 条满配 recall 必须整段到达模型，不得被 8KB 外置规则换成预览。

    真实边界：Runtime 工具结果链路（`ToolCallRecorder` + 真实 PostgreSQL 审计 + 真实 Artifact 写盘）。
    纯 ASCII 20×512 ≈11KB、中文 ≈31KB，均远超通用外置阈值 —— 若 `recall` 未声明
    `externalizable_result=False`，返回体会变成 `{"artifact": ...}` 加 200 字符预览，
    模型读不到任何记忆且**没有任何报错**（`load_skill` 同款事故）。
    """
    tool_set = _tool_set()
    for index in range(20):
        await _upsert(f"bulk.{index:02d}", "x" * 512)
    registry = _recording_registry(tmp_path, tool_set)

    content = await _call_registry(
        registry, RECALL_TOOL, {"limit": 20, "prefix": "bulk."}
    )

    assert "artifact" not in content
    payload = json.loads(content)
    assert payload["items"], "recall 必须至少带回 1 条"
    assert all(len(item["value"]) == 512 for item in payload["items"])
    assert len(content.encode("utf-8")) <= MAX_RECALL_BYTES
    # 声明面必须单独断言：`MAX_RECALL_BYTES`(4096) 本就低于 8KB 外置阈值，故"返回体没有 artifact 键"
    # 在**没有**豁免声明时也成立 —— 只断言行为会让这条用例恒真（扰动实测：把 `externalizable_result`
    # 改回 True，行为断言照样绿）。豁免声明是对"上界被改大到 8KB 以上"的防线，必须直接钉住。
    definition = tool_set._definitions()
    recall_definition = next(item for item in definition if item.name == RECALL_TOOL)
    assert recall_definition.externalizable_result is False

    async with get_session_factory()() as session:
        audit = (
            await session.execute(
                sa.select(ToolCallAudit).where(ToolCallAudit.tenant_id == TENANT)
            )
        ).scalar_one()
    assert audit.artifact_id is None


async def test_recall_carries_source_and_non_instruction_notice() -> None:
    """`recall` 是 `AGENT_INFERRED` 进入上下文的唯一通道，返回体必须自带来源与非指令措辞。"""
    tool_set = _tool_set()
    await _upsert("reply.language", "中文", SOURCE_USER_EXPLICIT)
    await _upsert("work.style", "concise", SOURCE_AGENT_INFERRED)

    payload = await _call(tool_set, RECALL_TOOL, {})

    assert "非指令" in payload["notice"]
    by_key = {item["memory_key"]: item["source_type"] for item in payload["items"]}
    assert by_key == {"reply.language": SOURCE_USER_EXPLICIT, "work.style": SOURCE_AGENT_INFERRED}


async def test_recall_returns_at_least_one_item_when_over_budget() -> None:
    """字节上限先到先得，但**至少返回 1 条**：上限若小于单条会让模型把"不可用"读成"没有记忆"。"""
    tool_set = _tool_set()
    await _upsert("huge.one", "中" * 512)

    payload = await _call(tool_set, RECALL_TOOL, {"limit": 20})

    assert len(payload["items"]) == 1


# --------------------------------------------------------------------------- RULE-auth-001


async def test_rule_auth_001_memory_tools_expose_no_identity_and_need_no_grant(tmp_path: Path) -> None:
    """[RULE-auth-001][integration] 记忆不是可授权资源：工具 schema 无身份字段，注册不依赖 Grant。

    本需求不参与 User→Agent / Agent→Skill/MCP 三层授权模型：`remember`/`recall` 的注册只由
    agent policy（`memory_write`）决定，上面那条 `build_registry` 未创建任何 Grant/Binding 也能注册。
    """
    registry = build_registry(
        request=_request(), cache=_cache(tmp_path), audit_writer=None, mcp_adapter=None
    )

    for name in (REMEMBER_TOOL, RECALL_TOOL):
        schema = registry.get(name).input_schema
        assert "user_id" not in schema["properties"]
        assert "tenant_id" not in schema["properties"]
        assert schema["additionalProperties"] is False

    assert AgentPolicy.from_runtime_config({}).deadline_ms > 0


def _remember_args(
    *,
    memory_key: str = "reply.language",
    value: str = "中文",
    category: str = "PREFERENCE",
    source_type: str = SOURCE_USER_EXPLICIT,
) -> dict[str, Any]:
    return {
        "memory_key": memory_key,
        "value": value,
        "category": category,
        "source_type": source_type,
    }
