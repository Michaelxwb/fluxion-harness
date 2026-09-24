"""Run 创建/resume 提交幂等：run_submission 表的记录与重放查询。

同键同指纹返回已提交记录（200 SSE 重放）；同键异指纹 IDEMPOTENCY_MISMATCH；
指纹包含 run_id（resume 语义）/message 文本/message.id。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

import sqlalchemy as sa
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.db import SessionFactoryProvider
from ..infrastructure.models.runtime import RunSubmission

SUBMISSION_OPEN = "OPEN"
SUBMISSION_CLOSED = "CLOSED"
ENDPOINT_CREATE_RUN = "create-run"
ENDPOINT_RESUME_RUN = "resume-run"
ENDPOINT_CREATE_CONVERSATION = "create-conversation"


def submission_fingerprint(
    *,
    endpoint: str = "",
    run_id: uuid.UUID | None = None,
    key_payload: dict[str, Any] | None = None,
    message_id: str | None = None,
) -> str:
    """规范化 JSON 指纹：键序无关，同一逻辑请求稳定。"""
    canonical = json.dumps(
        {
            "endpoint": endpoint,
            "run_id": str(run_id) if run_id else None,
            "payload": key_payload or {},
            "message_id": message_id or "",
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class RunSubmissionService:
    def __init__(self, session_factory_provider: SessionFactoryProvider) -> None:
        # 与其余服务一致：传「返回 sessionmaker 的工厂」（如 get_session_factory），而非 sessionmaker 实例
        self._session_factory_provider: SessionFactoryProvider = session_factory_provider

    async def record_submission(
        self,
        *,
        tenant_id: str,
        idempotency_key: str,
        endpoint: str,
        actor_user_id: uuid.UUID,
        run_id: uuid.UUID | None,
        conversation_id: uuid.UUID,
        request_fingerprint: str,
        first_seq: int | None = None,
        last_seq: int | None = None,
        session: AsyncSession | None = None,
    ) -> dict[str, Any]:
        """与 Run/Snapshot/首事件同事务提交；无 session 时用独立短事务。"""
        owns = session is None
        active: AsyncSession = self._session_factory_provider()() if session is None else session
        try:
            row = await self.record_in(
                active,
                tenant_id=tenant_id,
                idempotency_key=idempotency_key,
                endpoint=endpoint,
                actor_user_id=actor_user_id,
                run_id=run_id,
                conversation_id=conversation_id,
                request_fingerprint=request_fingerprint,
                first_seq=first_seq,
                last_seq=last_seq,
            )
            if owns:
                await active.commit()
            return self._snapshot(row)
        finally:
            if owns:
                await active.close()

    async def record_in(
        self,
        session: AsyncSession,
        *,
        tenant_id: str,
        idempotency_key: str,
        endpoint: str,
        actor_user_id: uuid.UUID,
        run_id: uuid.UUID | None,
        conversation_id: uuid.UUID,
        request_fingerprint: str,
        first_seq: int | None = None,
        last_seq: int | None = None,
        submission_id: uuid.UUID | None = None,
    ) -> RunSubmission:
        """事务内插入提交记录（不 commit）：与 Run/Snapshot/首事件原子提交。"""
        row = RunSubmission(
            id=submission_id or uuid.uuid4(),
            tenant_id=tenant_id,
            idempotency_key=idempotency_key,
            endpoint=endpoint,
            request_fingerprint=request_fingerprint,
            actor_user_id=actor_user_id,
            run_id=run_id,
            conversation_id=conversation_id,
            first_seq=first_seq,
            last_seq=last_seq,
            status=SUBMISSION_OPEN,
        )
        session.add(row)
        await session.flush()
        return row

    async def find_replay(
        self,
        *,
        tenant_id: str,
        idempotency_key: str,
        endpoint: str,
        fingerprint: str,
        session: AsyncSession | None = None,
    ) -> dict[str, Any] | None:
        owns = session is None
        active: AsyncSession = self._session_factory_provider()() if session is None else session
        try:
            row = await self.find_replay_in(
                active,
                tenant_id=tenant_id,
                idempotency_key=idempotency_key,
                endpoint=endpoint,
                fingerprint=fingerprint,
            )
            return self._snapshot(row) if row is not None else None
        finally:
            if owns:
                await active.close()

    async def find_replay_in(
        self,
        session: AsyncSession,
        *,
        tenant_id: str,
        idempotency_key: str,
        endpoint: str,
        fingerprint: str,
    ) -> RunSubmission | None:
        """事务内查询提交记录；同键异指纹抛 IDEMPOTENCY_MISMATCH。"""
        row = (
            await session.execute(
                select(RunSubmission).where(
                    RunSubmission.tenant_id == tenant_id,
                    RunSubmission.idempotency_key == idempotency_key,
                    RunSubmission.endpoint == endpoint,
                    RunSubmission.is_deleted.is_(False),
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        if row.request_fingerprint != fingerprint:
            raise AppError(ErrorCode.IDEMPOTENCY_MISMATCH)
        return row

    async def finalize_in(
        self,
        session: AsyncSession,
        submission_id: uuid.UUID,
        *,
        status: str,
        last_seq: int | None,
        terminal_result_json: dict[str, Any] | None = None,
    ) -> None:
        """终态：同一事务内关闭提交记录并记录 last_seq/终态结果。"""
        await session.execute(
            sa.update(RunSubmission)
            .where(RunSubmission.id == submission_id)
            .values(
                status=status,
                last_seq=last_seq,
                terminal_result_json=terminal_result_json,
                update_time=_now(),
            )
        )

    @staticmethod
    def _snapshot(row: RunSubmission) -> dict[str, Any]:
        return {
            "submission_id": str(row.id),
            "run_id": str(row.run_id) if row.run_id else None,
            "conversation_id": str(row.conversation_id) if row.conversation_id else None,
            "first_seq": row.first_seq,
            "last_seq": row.last_seq,
            "status": row.status,
        }


def _now() -> Any:
    from datetime import UTC, datetime

    return datetime.now(UTC)


def require_resume_idempotency(
    *, idempotency_key: str | None, input_id: str | None
) -> str:
    """resume 幂等键：显式 Idempotency-Key 优先，缺省取 input.id；均缺失 → COMMON_VALIDATION_ERROR。"""
    resolved = idempotency_key or input_id
    if not resolved:
        raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
    return resolved
