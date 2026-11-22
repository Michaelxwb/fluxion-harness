"""Agentic 回合的审计事件（`task.task_event`）。

三条口径：

- **一条事实一行**：每次模型调用一条 `MODEL_CALL`，每个 assistant 回合一条 `ASSISTANT_TURN`，
  每次工具调用一条 `TOOL_CALL`（开始与结束各一条，`state` 区分）；payload 使用共享 v2 契约。
- **`tool_call_id` 稳定**：它是模型回合里的调用 id，工具重试/恢复后仍是同一个 id；事件与
  checkpoint 的待定水位按同一个键对齐。
- **写失败即失败**：恢复要按这些事件重建历史，静默丢一条等于把"当时发生了什么"抹掉。写库
  异常照常上抛，由 WorkerLoop 的失败/重试路径收口。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from muad_agent_core.model import text_of
from muad_agent_core.tools import AssistantTurn, PreparedCall, ToolCompleted
from muad_contracts import (
    AssistantToolCallEvent,
    AssistantTurnEvent,
    PreparedCallEvent,
    ToolCompletedEvent,
    ToolEventArtifact,
    ToolEventFollowUp,
    ToolEventOutcome,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..task_events import EntityType, TaskEventSeed, TaskEventType, append_events


class TaskEventSink:
    """把 Kernel 的回合回调写成 `task_event`（实体 = 本次 Execution）。"""

    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        *,
        tenant_id: str,
        execution_id: UUID,
        model_id: str,
    ) -> None:
        self._factory = factory
        self._tenant_id = tenant_id
        self._execution_id = execution_id
        self._model_id = model_id

    async def on_model_started(self) -> None:
        await self._append(
            TaskEventType.MODEL_CALL,
            {"state": "STARTED", "model_id": self._model_id},
        )

    async def on_model_completed(self) -> None:
        await self._append(
            TaskEventType.MODEL_CALL,
            {"state": "COMPLETED", "model_id": self._model_id},
        )

    async def on_assistant_turn(self, turn: AssistantTurn) -> None:
        """原样记下 assistant 回合：恢复要按它重建"带 tool_calls 的那一轮"。"""
        message = turn.message
        calls_by_id = {call.call_id: call for call in turn.calls}
        await self._append(
            TaskEventType.ASSISTANT_TURN,
            AssistantTurnEvent(
                text=text_of(message.content),
                reasoning_content=message.reasoning_content,
                tool_calls=[
                    AssistantToolCallEvent(
                        id=call.id,
                        name=call.name,
                        registry_name=calls_by_id[call.id].name
                        if call.id in calls_by_id
                        else None,
                        arguments=dict(call.arguments),
                    )
                    for call in message.tool_calls
                ],
            ).model_dump(mode="json"),
        )

    async def on_tool_prepared(self, prepared: PreparedCall) -> None:
        await self._append(
            TaskEventType.TOOL_CALL,
            PreparedCallEvent(
                tool_call_id=prepared.call.call_id,
                name=prepared.call.name,
                model_name=prepared.call.model_name or prepared.call.name,
                args_hash=prepared.args_hash,
                args_preview=dict(prepared.args_preview),
                source=prepared.source,
                skill_id=str(prepared.skill_id) if prepared.skill_id is not None else None,
                skill_key=prepared.skill_key,
                tool_name=prepared.tool_name,
                execution_mode=prepared.execution_mode,
                retry_safety=str(prepared.retry_safety),
            ).model_dump(mode="json"),
        )

    async def on_tool_completed(self, completed: ToolCompleted) -> None:
        await self._append(
            TaskEventType.TOOL_CALL,
            ToolCompletedEvent(
                tool_call_id=completed.tool_call_id,
                tool_name=completed.tool_name,
                status=completed.status,
                outcome=ToolEventOutcome(
                    business_status=str(completed.outcome.business_status),
                    failure_layer=str(completed.outcome.failure_layer),
                    error_code=completed.outcome.error_code,
                    retryable=completed.outcome.retryable,
                    evidence_preview=completed.outcome.evidence.preview,
                    exit_code=completed.outcome.exit_code,
                ),
                content=completed.content,
                artifacts=[
                    ToolEventArtifact(
                        artifact_id=item.artifact_id,
                        role=str(item.role),
                        media_type=item.media_type,
                        filename=item.filename,
                        checksum=item.checksum,
                        size=item.size,
                    )
                    for item in completed.artifacts
                ],
                follow_ups=[
                    ToolEventFollowUp(
                        kind=item.kind,
                        artifact_id=item.artifact_id,
                        media_type=item.media_type,
                        text=item.text,
                    )
                    for item in completed.follow_ups
                ],
            ).model_dump(mode="json"),
        )

    async def _append(self, event_type: TaskEventType, payload: dict[str, Any]) -> None:
        async with self._factory() as session, session.begin():
            await append_events(
                session,
                [
                    TaskEventSeed(
                        tenant_id=self._tenant_id,
                        entity_type=EntityType.EXECUTION,
                        entity_id=self._execution_id,
                        event_type=event_type,
                        payload=payload,
                        occurred_at=datetime.now(UTC),
                    )
                ],
            )


__all__ = ["TaskEventSink"]
