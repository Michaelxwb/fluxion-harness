"""Agentic 回合的审计事件（`task.task_event`）。

三条口径：

- **一条事实一行**：每次模型调用一条 `MODEL_CALL`，每个 assistant 回合一条 `ASSISTANT_TURN`，
  每次工具调用一条 `TOOL_CALL`（开始与结束各一条，`state` 区分）；凭据与工具参数里的密文由
  `append_event` 的 `redact_value` 统一脱敏，这里不另造脱敏。
- **`tool_call_id` 稳定**：它是模型回合里的调用 id，工具重试/恢复后仍是同一个 id；事件与
  checkpoint 的待定水位按同一个键对齐。
- **写失败即失败**：恢复要按这些事件重建历史，静默丢一条等于把"当时发生了什么"抹掉。写库
  异常照常上抛，由 WorkerLoop 的失败/重试路径收口。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from muad_agent_core.model import ModelMessage, ModelRole, text_of
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

    async def on_assistant_turn(self, message: ModelMessage) -> None:
        """原样记下 assistant 回合：恢复要按它重建"带 tool_calls 的那一轮"。"""
        if message.role is not ModelRole.ASSISTANT:
            raise ValueError("assistant turn event requires an assistant message")
        await self._append(
            TaskEventType.ASSISTANT_TURN,
            {
                "text": text_of(message.content),
                "reasoning_content": message.reasoning_content,
                "tool_calls": [
                    {
                        "id": call.id,
                        "name": call.name,
                        "arguments": dict(call.arguments),
                    }
                    for call in message.tool_calls
                ],
            },
        )

    async def on_tool_started(self, call_id: str, name: str) -> None:
        await self._append(
            TaskEventType.TOOL_CALL,
            {"state": "STARTED", "tool_call_id": call_id, "tool_name": name},
        )

    async def on_tool_completed(
        self,
        call_id: str,
        name: str,
        status: str,
        artifact_id: str | None,
        result_text: str | None,
    ) -> None:
        """`result_text` 只在结果未被外置时给出（外置后正文在产物里，引用记 `artifact_id`）。"""
        payload: dict[str, Any] = {
            "state": "COMPLETED",
            "tool_call_id": call_id,
            "tool_name": name,
            "status": status,
            "artifact_id": artifact_id,
        }
        if result_text is not None:
            payload["result"] = result_text
        await self._append(TaskEventType.TOOL_CALL, payload)

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
