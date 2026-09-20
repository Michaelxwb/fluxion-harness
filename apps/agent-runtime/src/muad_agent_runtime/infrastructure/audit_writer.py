"""tool/egress/model 三类审计写入 port 的 DB 实现（runtime schema 三张审计表）。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.models.runtime import (
    EgressAudit,
    ModelInvocationAudit,
    ToolCallAudit,
)
from ..infrastructure.db import get_session_factory


class RuntimeAuditWriter:
    """注入执行链的审计 port；每个事件独立短事务，不携带凭据字段。"""

    def __init__(
        self,
        *,
        tenant_id: str,
        run_id: uuid.UUID,
        task_id: uuid.UUID | None,
        conversation_id: uuid.UUID,
        user_id: uuid.UUID,
        session_factory=None,
    ) -> None:
        self._tenant_id = tenant_id
        self._run_id = run_id
        self._task_id = task_id
        self._conversation_id = conversation_id
        self._user_id = user_id
        self._session_factory = session_factory or get_session_factory

    async def record_tool_call(
        self,
        *,
        tool_call_id: str,
        tool_name: str,
        tool_kind: str,
        prepared_args_hash: str,
        args_preview_json: dict,
        status: str,
        start_time,
        end_time=None,
        latency_ms: int | None = None,
        error_code: str | None = None,
        artifact_id: uuid.UUID | None = None,
    ) -> None:
        async with self._session_factory()() as session:
            session.add(
                ToolCallAudit(
                    tenant_id=self._tenant_id,
                    run_id=self._run_id,
                    task_id=self._task_id,
                    conversation_id=self._conversation_id,
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    tool_kind=tool_kind,
                    prepared_args_hash=prepared_args_hash,
                    args_preview_json=args_preview_json,
                    status=status,
                    start_time=start_time,
                    end_time=end_time,
                    latency_ms=latency_ms,
                    error_code=error_code,
                    artifact_id=artifact_id,
                )
            )
            await session.commit()

    async def record_egress(
        self,
        *,
        target_type: str,
        target: str,
        policy_decision: str,
        operation: str = "",
        method: str | None = None,
        status_code: int | None = None,
        result_status: str = "OK",
        latency_ms: int | None = None,
        error_code: str | None = None,
    ) -> None:
        async with self._session_factory()() as session:
            session.add(
                EgressAudit(
                    tenant_id=self._tenant_id,
                    run_id=self._run_id,
                    task_id=self._task_id,
                    user_id=self._user_id,
                    target_type=target_type,
                    target=target,
                    operation=operation,
                    method=method,
                    policy_decision=policy_decision,
                    status_code=status_code,
                    result_status=result_status,
                    latency_ms=latency_ms,
                    error_code=error_code,
                )
            )
            await session.commit()

    async def record_model_invocation(
        self,
        *,
        provider: str,
        model: str,
        attempt: int,
        retry_reason: str | None,
        input_tokens: int | None,
        output_tokens: int | None,
        latency_ms: int | None,
        status: str,
        error_code: str | None = None,
    ) -> None:
        async with self._session_factory()() as session:
            session.add(
                ModelInvocationAudit(
                    tenant_id=self._tenant_id,
                    run_id=self._run_id,
                    task_id=self._task_id,
                    provider=provider,
                    model=model,
                    attempt=attempt,
                    retry_reason=retry_reason,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    latency_ms=latency_ms,
                    status=status,
                    error_code=error_code,
                )
            )
            await session.commit()
