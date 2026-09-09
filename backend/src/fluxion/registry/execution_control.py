"""Durable Execution Control 持久化（ADR-A017 §3-§4）：PG 单库实现。

全部状态转换使用 SQL 条件更新（WHERE state IN ... + rowcount 判定），
禁止"先 get 再无条件 update"。首次终态获胜（rowcount==0 即已有终态）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import insert, select, update
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from fluxion.registry.schema import runtime_executions


@dataclass(frozen=True, slots=True)
class ExecutionRecord:
    execution_id: str
    tenant_id: str
    platform_user_id: str
    agent_id: str
    session_id: str
    state: str
    request_id: str
    trace_id: str
    owner_instance_id: str
    requested_skill_id: str | None
    started_at: datetime
    cancel_requested_at: datetime | None
    cancel_reason: str | None
    finished_at: datetime | None
    error_code: str | None
    revision: int


class ActiveExecutionExists(RuntimeError):
    """同一 Session 已有 active execution（调用方转 session_busy）。"""

    code = "session_busy"


_ACTIVE_STATES = ("created", "running", "cancelling")


def _record_from_row(row: RowMapping) -> ExecutionRecord:
    return ExecutionRecord(
        execution_id=str(row["execution_id"]),
        tenant_id=str(row["tenant_id"]),
        platform_user_id=str(row["platform_user_id"]),
        agent_id=str(row["agent_id"]),
        session_id=str(row["session_id"]),
        state=str(row["state"]),
        request_id=str(row["request_id"]),
        trace_id=str(row["trace_id"]),
        owner_instance_id=str(row["owner_instance_id"]),
        requested_skill_id=row["requested_skill_id"],
        started_at=row["started_at"],
        cancel_requested_at=row["cancel_requested_at"],
        cancel_reason=row["cancel_reason"],
        finished_at=row["finished_at"],
        error_code=row["error_code"],
        revision=int(row["revision"]),
    )


async def get_execution(
    engine: AsyncEngine, *, tenant_id: str, execution_id: str
) -> ExecutionRecord | None:
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                select(runtime_executions).where(
                    runtime_executions.c.tenant_id == tenant_id,
                    runtime_executions.c.execution_id == execution_id,
                )
            )
        ).mappings().first()
    if row is None:
        return None
    return _record_from_row(row)


async def get_active_for_session(
    engine: AsyncEngine,
    *,
    tenant_id: str,
    platform_user_id: str,
    agent_id: str,
    session_id: str,
) -> ExecutionRecord | None:
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                select(runtime_executions).where(
                    runtime_executions.c.tenant_id == tenant_id,
                    runtime_executions.c.platform_user_id == platform_user_id,
                    runtime_executions.c.agent_id == agent_id,
                    runtime_executions.c.session_id == session_id,
                    runtime_executions.c.state.in_(_ACTIVE_STATES),
                )
            )
        ).mappings().first()
    if row is None:
        return None
    return _record_from_row(row)


async def create_execution(engine: AsyncEngine, record: ExecutionRecord) -> ExecutionRecord:
    """创建 CREATED 记录。Session 已有 active 即 ActiveExecutionExists
    （先查后插 + partial unique index 双保险，并发插入冲突转译同错）。"""
    existing = await get_active_for_session(
        engine,
        tenant_id=record.tenant_id,
        platform_user_id=record.platform_user_id,
        agent_id=record.agent_id,
        session_id=record.session_id,
    )
    if existing is not None:
        raise ActiveExecutionExists(f"session {record.session_id} 已有 active execution")
    try:
        async with engine.begin() as connection:
            await connection.execute(insert(runtime_executions).values(**_record_values(record)))
    except IntegrityError as exc:
        raise ActiveExecutionExists(f"session {record.session_id} 已有 active execution") from exc
    return record


async def mark_running(
    engine: AsyncEngine, *, tenant_id: str, execution_id: str
) -> ExecutionRecord | None:
    """CREATED → RUNNING。返回更新后记录；已被终结/取消则返回 None。"""
    async with engine.begin() as connection:
        result = await connection.execute(
            update(runtime_executions)
            .where(
                runtime_executions.c.tenant_id == tenant_id,
                runtime_executions.c.execution_id == execution_id,
                runtime_executions.c.state == "created",
            )
            .values(state="running")
        )
        if result.rowcount != 1:
            return None
        row = (
            await connection.execute(
                select(runtime_executions).where(
                    runtime_executions.c.tenant_id == tenant_id,
                    runtime_executions.c.execution_id == execution_id,
                )
            )
        ).mappings().first()
    return _record_from_row(row) if row is not None else None


async def request_cancel(
    engine: AsyncEngine,
    *,
    tenant_id: str,
    platform_user_id: str,
    agent_id: str,
    session_id: str,
    reason: str,
    now: datetime,
) -> ExecutionRecord | None:
    """RUNNING → CANCELLING（/stop 持久化语义）。非 RUNNING 返回 None（调用方
    按当前态区分 already_requested / nothing_to_stop）。"""
    async with engine.begin() as connection:
        result = await connection.execute(
            update(runtime_executions)
            .where(
                runtime_executions.c.tenant_id == tenant_id,
                runtime_executions.c.platform_user_id == platform_user_id,
                runtime_executions.c.agent_id == agent_id,
                runtime_executions.c.session_id == session_id,
                runtime_executions.c.state == "running",
            )
            .values(state="cancelling", cancel_requested_at=now, cancel_reason=reason)
        )
        if result.rowcount != 1:
            return None
        row = (
            await connection.execute(
                select(runtime_executions).where(
                    runtime_executions.c.tenant_id == tenant_id,
                    runtime_executions.c.platform_user_id == platform_user_id,
                    runtime_executions.c.agent_id == agent_id,
                    runtime_executions.c.session_id == session_id,
                    runtime_executions.c.state == "cancelling",
                )
            )
        ).mappings().first()
    return _record_from_row(row) if row is not None else None


async def finish_execution(
    engine: AsyncEngine,
    *,
    tenant_id: str,
    execution_id: str,
    state: str,
    now: datetime,
    error_code: str | None = None,
) -> ExecutionRecord | None:
    """active → 终态（首次终态获胜：已有终态时 rowcount==0，返回现记录）。"""
    async with engine.begin() as connection:
        result = await connection.execute(
            update(runtime_executions)
            .where(
                runtime_executions.c.tenant_id == tenant_id,
                runtime_executions.c.execution_id == execution_id,
                runtime_executions.c.state.in_(_ACTIVE_STATES),
            )
            .values(state=state, finished_at=now, error_code=error_code)
        )
        if result.rowcount != 1:
            row = (
                await connection.execute(
                    select(runtime_executions).where(
                        runtime_executions.c.tenant_id == tenant_id,
                        runtime_executions.c.execution_id == execution_id,
                    )
                )
            ).mappings().first()
            return _record_from_row(row) if row is not None else None
        row = (
            await connection.execute(
                select(runtime_executions).where(
                    runtime_executions.c.tenant_id == tenant_id,
                    runtime_executions.c.execution_id == execution_id,
                )
            )
        ).mappings().first()
    return _record_from_row(row) if row is not None else None


def _record_values(record: ExecutionRecord) -> dict[str, object]:
    return {
        "execution_id": record.execution_id,
        "tenant_id": record.tenant_id,
        "platform_user_id": record.platform_user_id,
        "agent_id": record.agent_id,
        "session_id": record.session_id,
        "state": record.state,
        "request_id": record.request_id,
        "trace_id": record.trace_id,
        "owner_instance_id": record.owner_instance_id,
        "requested_skill_id": record.requested_skill_id,
        "started_at": record.started_at,
        "cancel_requested_at": record.cancel_requested_at,
        "cancel_reason": record.cancel_reason,
        "finished_at": record.finished_at,
        "error_code": record.error_code,
        "revision": record.revision,
    }


async def list_stale_cancelling(
    engine: AsyncEngine, *, before: datetime, limit: int = 100
) -> list[ExecutionRecord]:
    """列出 cancel_requested_at 早于 before 的 CANCELLING 记录（orphan 认领输入）。"""
    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                select(runtime_executions)
                .where(
                    runtime_executions.c.state == "cancelling",
                    runtime_executions.c.cancel_requested_at.is_not(None),
                    runtime_executions.c.cancel_requested_at < before,
                )
                .order_by(runtime_executions.c.cancel_requested_at)
                .limit(limit)
            )
        ).mappings().all()
    return [_record_from_row(row) for row in rows]
