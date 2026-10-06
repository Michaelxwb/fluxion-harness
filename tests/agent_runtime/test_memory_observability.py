"""[RULE-log-001 / RULE-07] 记忆链路的日志脱敏、指标接线与审计取证。

不得 Mock 的真实边界：
- 真实 PostgreSQL（`runtime.user_memory` 写入与注入、`runtime.tool_call_audit` 审计行）；
- 真实 logging-kit 出口（`caplog` 采集的是真 logger 的 record，不是替身）；
- 真实 api-kit 进程内注册表（`render_metrics()` 与 `GET /metrics` 同源同数据）。

RULE-07 的审计行复用既有 `runtime.tool_call_audit`（`tool_name='remember'`），**不新建表、不加迁移**：
本文件把这一点钉死，防止日后有人为了"更完整"再开一张记忆专用审计表。
"""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import sqlalchemy as sa
from muad_agent_core.model import ModelMessage, ModelRole, text_of
from muad_agent_core.tools import ToolDefinition, ToolRegistry
from muad_agent_runtime.application.attachments.tool_results import ArtifactResultWriter
from muad_agent_runtime.application.context_builder import DbBackedContextBuilder
from muad_agent_runtime.application.executor import ExecutorRunContext, ToolCallRecorder
from muad_agent_runtime.application.memory_service import (
    SOURCE_AGENT_INFERRED,
    SOURCE_USER_EXPLICIT,
    MemoryService,
)
from muad_agent_runtime.application.memory_tools import (
    RECALL_TOOL,
    REMEMBER_TOOL,
    MemoryScope,
    MemoryToolSet,
)
from muad_agent_runtime.infrastructure.audit_writer import RuntimeAuditWriter
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import CanonicalEvent, ToolCallAudit, UserMemory
from muad_api import AppError, render_metrics

TENANT = f"memobs-{uuid.uuid4()}"
USER_ID = uuid.uuid4()
RUN_ID = uuid.uuid4()
CONV_ID = uuid.uuid4()
#: 哨兵串：只要它出现在任何一条日志里，就说明 `value` 全文被写进了日志。
VALUE_SENTINEL = "SENTINEL-VALUE-MUST-NOT-BE-LOGGED-9f3a"

MEMORY_WRITE_METRIC = "memory_write_total"
MEMORY_INJECT_METRIC = "memory_inject_total"
MEMORY_RECALL_METRIC = "memory_recall_total"
MEMORY_RECALL_BYTES_METRIC = "memory_recall_bytes_total"


@pytest.fixture(autouse=True)
async def _cleanup() -> AsyncIterator[None]:
    yield
    async with get_session_factory()() as session:
        # 清扫范围必须覆盖本文件**写入**的每一张表：少一张就会"每跑一次残留几行"永久累积
        # （2026-10-01 在 `test_context_memory.py` 上实测踩过）。
        for table in (UserMemory.__table__, ToolCallAudit.__table__, CanonicalEvent.__table__):
            await session.execute(table.delete().where(table.c.tenant_id == TENANT))
        await session.commit()


def _scope() -> MemoryScope:
    return MemoryScope(tenant_id=TENANT, user_id=USER_ID, run_id=RUN_ID)


def _tool_set(service: MemoryService | None = None) -> MemoryToolSet:
    return MemoryToolSet(service=service or MemoryService(), scope=_scope())


def _handler(definition: ToolDefinition) -> Any:
    handler = definition.handler
    assert handler is not None
    return handler


def _definition(tool_set: MemoryToolSet, name: str) -> ToolDefinition:
    registry = ToolRegistry()
    tool_set.register(registry)
    return registry.get(name)


async def _close_round(recorder: ToolCallRecorder, *, call_id: str, content: str) -> str:
    """回合收口（design ADR-04）：整批判定、落盘与审计行都在这一步发生。

    逐条调用只做缓冲，所以直连 recorder 的用例必须自己收口 —— 生产里这一步由 `AgentRunner`
    在回合末调用。
    """
    messages = await recorder.finish_round(
        (ModelMessage(role=ModelRole.TOOL, content=content, tool_call_id=call_id),)
    )
    return text_of(messages[0].content)


def _render_record(record: logging.LogRecord) -> str:
    """把一条日志 record 完整摊平成字符串（message + 结构化 extras）。

    只查 `record.getMessage()` 不够：`value` 若被塞进 `extra`，渲染出来的 message 里看不到，
    但照样落进了日志文件。
    """
    parts = [record.getMessage()]
    for key, value in record.__dict__.items():
        if key in logging.LogRecord.__dict__ or key.startswith("_"):
            continue
        parts.append(f"{key}={value!r}")
    return " ".join(parts)


def _metric_value(name: str, labels: dict[str, str] | None = None) -> float:
    """从真实注册表读某条时间序列的值（`render_metrics()` 与 HTTP `/metrics` 同源）。

    无 label 的指标在 Prometheus 文本里是裸的 `name value`（没有 `{}`），与带 label 的
    `name{k="v"} value` 是两种形态，都要认。
    """
    text = render_metrics()
    wanted = dict(labels or {})
    total = 0.0
    for line in text.splitlines():
        if not line.startswith(name):
            continue
        rest = line[len(name):]
        if rest.startswith("{"):
            raw_labels, separator, raw_value = rest.partition("} ")
            if not separator:
                continue
            parsed = dict(re.findall(r'(\w+)="([^"]*)"', raw_labels))
            if len(parsed) != len(wanted) or any(parsed.get(k) != v for k, v in wanted.items()):
                continue
        elif rest.startswith(" ") and not wanted:
            raw_value = rest.strip()
        else:
            continue
        total += float(raw_value)
    return total


async def _seed_memory(
    value: str = "中文回答",
    source_type: str = SOURCE_USER_EXPLICIT,
    memory_key: str = "reply.language",
) -> None:
    await MemoryService().upsert(
        tenant_id=TENANT,
        user_id=USER_ID,
        category="PREFERENCE",
        memory_key=memory_key,
        content_json={"value": value},
        source_type=source_type,
    )


async def test_rule_log_001_memory_value_is_never_written_to_logs(caplog: pytest.LogCaptureFixture) -> None:
    """[RULE-log-001][integration] 写入/注入/检索三条链路都不得把 `value` 全文落进日志。

    真实边界：真实 PostgreSQL 的写入与注入 + 真实 logger 出口。
    断言用**哨兵串**而不是"日志里没有 value 这个词"：只有哨兵能证明全文没被带出去。
    """
    tool_set = _tool_set()
    remember = _definition(tool_set, REMEMBER_TOOL)
    recall = _definition(tool_set, RECALL_TOOL)

    with caplog.at_level(logging.INFO):
        await _handler(remember)(
            {
                "memory_key": "reply.language",
                "value": VALUE_SENTINEL,
                "category": "PREFERENCE",
                "source_type": SOURCE_USER_EXPLICIT,
            },
            call_id="call-log-1",
        )
        await _handler(recall)({}, call_id="call-log-2")
        builder = DbBackedContextBuilder(session_factory=get_session_factory)
        await builder.load_history(
            tenant_id=TENANT,
            conversation_id=CONV_ID,
            user_id=USER_ID,
            budget_messages=10,
        )

    memory_records = [r for r in caplog.records if r.name.startswith("muad_agent_runtime")]
    rendered = [_render_record(record) for record in memory_records]
    leaked = [text for text in rendered if VALUE_SENTINEL in text]
    assert not leaked, f"记忆 value 全文被写进日志：{leaked}"

    # 三条链路各有可观测出口，且都带结构化字段（key/来源/长度），而不是只打一句话
    joined = "\n".join(rendered)
    assert "memory_write" in joined, f"写入链路缺 INFO 日志：{joined}"
    assert "memory_inject" in joined, f"注入链路缺 INFO 日志：{joined}"
    assert "memory_recall" in joined, f"检索链路缺 INFO 日志：{joined}"
    fields = {key for record in memory_records for key in record.__dict__}
    assert "memory_key" in fields
    assert "value_length" in fields


async def test_memory_metrics_cover_write_inject_and_recall() -> None:
    """[RULE-log-001] 三个指标真实接线：写入（来源/状态）、注入（条数）、检索（条数与字节）。

    计数值放在**amount**而不是 label：label 里放计数值会产生无界时间序列
    （`metrics.py` 的目录声明只允许 Agent/Skill/工具/provider/status 这类小集合）。
    """
    tool_set = _tool_set()
    remember = _definition(tool_set, REMEMBER_TOOL)
    recall = _definition(tool_set, RECALL_TOOL)
    await _seed_memory("先记住的一条")

    write_before = _metric_value(MEMORY_WRITE_METRIC, {"source_type": SOURCE_USER_EXPLICIT, "status": "OK"})
    inject_before = _metric_value(MEMORY_INJECT_METRIC)
    recall_before = _metric_value(MEMORY_RECALL_METRIC, {"status": "OK"})
    bytes_before = _metric_value(MEMORY_RECALL_BYTES_METRIC)

    await _handler(remember)(
        {
            "memory_key": "reply.style",
            "value": "简短一点",
            "category": "WORK_STYLE",
            "source_type": SOURCE_USER_EXPLICIT,
        },
        call_id="call-metric-1",
    )
    payload = await _handler(recall)({}, call_id="call-metric-2")
    # 历史要给够：注入上限是 `min(MAX_INJECTED_BYTES, memory.budget_ratio × 装配出的历史字节)`，
    # 历史太短时比例先触顶（只走"至少一条"的下限），本用例要验的"两条都注入"就轮不到。
    async with get_session_factory()() as session:
        session.add(
            CanonicalEvent(
                tenant_id=TENANT,
                conversation_id=CONV_ID,
                seq=1,
                event_type="USER_MESSAGE",
                payload_json={"text": "历史" * 2500},
            )
        )
        await session.commit()
    builder = DbBackedContextBuilder(session_factory=get_session_factory)
    await builder.load_history(
        tenant_id=TENANT,
        conversation_id=CONV_ID,
        user_id=USER_ID,
        budget_messages=10,
    )

    write_labels = {"source_type": SOURCE_USER_EXPLICIT, "status": "OK"}
    assert _metric_value(MEMORY_WRITE_METRIC, write_labels) == write_before + 1
    assert _metric_value(MEMORY_INJECT_METRIC) == inject_before + 2  # 两条 USER_EXPLICIT 记忆
    assert _metric_value(MEMORY_RECALL_METRIC, {"status": "OK"}) == recall_before + 1
    # 字节口径与 `MAX_RECALL_BYTES` 一致：统计**返回体**字节（上限约束的就是它），不是单条 value
    assert _metric_value(MEMORY_RECALL_BYTES_METRIC) == bytes_before + len(payload.encode("utf-8"))
    # 只有注入面统计注入量：AGENT_INFERRED 不注入，注入计数不该因它增加（见下一条用例）


async def test_rule_07_remember_write_is_audited_in_tool_call_audit(tmp_path) -> None:
    """[RULE-07][integration] 成功写入在既有 `tool_call_audit` 里可读出 key 与来源。

    真实边界：真实 `ToolCallRecorder` + 真实 PostgreSQL 审计表（**复用既有表，无新迁移**）。
    这条用例在实现前就成立（审计由执行链统一写），它的价值是**钉住"不要另开审计表"**。
    """
    tool_set = _tool_set()
    remember = _definition(tool_set, REMEMBER_TOOL)
    recorder = ToolCallRecorder(
        context=ExecutorRunContext(
            tenant_id=TENANT, run_id=RUN_ID, conversation_id=CONV_ID, user_id=USER_ID
        ),
        audit_writer=RuntimeAuditWriter(
            tenant_id=TENANT,
            run_id=RUN_ID,
            task_id=None,
            conversation_id=CONV_ID,
            user_id=USER_ID,
            session_factory=get_session_factory,
        ),
        artifact_writer=ArtifactResultWriter(tmp_path),
    )

    content = await recorder(
        remember,
        {
            "memory_key": "reply.language",
            "value": "中文回答",
            "category": "PREFERENCE",
            "source_type": SOURCE_USER_EXPLICIT,
        },
        handler=_handler(remember),
        call_id="call-audit-1",
    )
    await _close_round(recorder, call_id="call-audit-1", content=content)

    async with get_session_factory()() as session:
        row = (
            await session.execute(
                sa.select(ToolCallAudit).where(ToolCallAudit.tenant_id == TENANT)
            )
        ).scalar_one()
    assert row.tool_name == REMEMBER_TOOL
    assert row.tool_call_id == "call-audit-1"
    assert row.status == "OK"
    preview = row.args_preview_json
    assert preview["memory_key"] == "reply.language"
    assert preview["source_type"] == SOURCE_USER_EXPLICIT


async def test_rule_07_write_failure_is_visible_in_audit_and_metric(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """[RULE-07b] 写失败必须**可分辨**：审计落 `ERROR` + 错误码，指标计入 `status=error`。

    故障注入方式：让服务层 upsert 抛错（受控故障），审计与指标仍走真实链路。
    这是 `load_skill` 事故的直接教训 —— 失败若被吞成"成功"，审计与指标面就彻底看不见。
    """
    async def _boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("database is unreachable")

    monkeypatch.setattr(MemoryService, "upsert", _boom)
    tool_set = _tool_set()
    remember = _definition(tool_set, REMEMBER_TOOL)
    recorder = ToolCallRecorder(
        context=ExecutorRunContext(
            tenant_id=TENANT, run_id=RUN_ID, conversation_id=CONV_ID, user_id=USER_ID
        ),
        audit_writer=RuntimeAuditWriter(
            tenant_id=TENANT,
            run_id=RUN_ID,
            task_id=None,
            conversation_id=CONV_ID,
            user_id=USER_ID,
            session_factory=get_session_factory,
        ),
        artifact_writer=ArtifactResultWriter(tmp_path),
    )
    error_labels = {"source_type": SOURCE_USER_EXPLICIT, "status": "ERROR"}
    error_before = _metric_value(MEMORY_WRITE_METRIC, error_labels)

    with pytest.raises(AppError):
        await recorder(
            remember,
            {
                "memory_key": "reply.language",
                "value": "中文回答",
                "category": "PREFERENCE",
                "source_type": SOURCE_USER_EXPLICIT,
            },
            handler=_handler(remember),
            call_id="call-audit-2",
        )
    # 生产里 Runner 把工具异常转成一条 tool 消息继续走回合；这里照同一形态收口
    await _close_round(
        recorder, call_id="call-audit-2", content="tool failed: RuntimeError: database is unreachable"
    )

    async with get_session_factory()() as session:
        row = (
            await session.execute(
                sa.select(ToolCallAudit).where(ToolCallAudit.tenant_id == TENANT)
            )
        ).scalar_one()
    assert row.status == "ERROR"
    assert row.error_code  # 错误码可读，不是空
    assert _metric_value(MEMORY_WRITE_METRIC, error_labels) == error_before + 1


async def test_agent_inferred_write_is_not_counted_as_injected() -> None:
    """注入指标只统计**真正进入上下文**的记忆：`AGENT_INFERRED` 写入了也不算注入。

    两条腿缺一不可：只断言"AGENT_INFERRED 不增加"是恒真断言（指标压根没接线时也成立），
    必须同时断言"`USER_EXPLICIT` 会增加"，才证明这条链真的跑起来了。
    """
    await _seed_memory("模型自行归纳", source_type=SOURCE_AGENT_INFERRED)
    before = _metric_value(MEMORY_INJECT_METRIC)
    builder = DbBackedContextBuilder(session_factory=get_session_factory)
    await builder.load_history(
        tenant_id=TENANT,
        conversation_id=CONV_ID,
        user_id=USER_ID,
        budget_messages=10,
    )
    assert _metric_value(MEMORY_INJECT_METRIC) == before  # 腿一：推断类不注入

    await _seed_memory("用户明确要求", source_type=SOURCE_USER_EXPLICIT, memory_key="reply.tone")
    await builder.load_history(
        tenant_id=TENANT,
        conversation_id=CONV_ID,
        user_id=USER_ID,
        budget_messages=10,
    )
    assert _metric_value(MEMORY_INJECT_METRIC) == before + 1  # 腿二：显式类注入，且只注这一条
