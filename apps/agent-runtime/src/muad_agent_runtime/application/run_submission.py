"""Run 创建/resume 提交幂等：run_submission 表的记录与重放查询。

同键同指纹返回已提交记录（200 SSE 重放）；同键异指纹 IDEMPOTENCY_MISMATCH；
指纹包含 run_id（resume 语义）/message 文本/message.id。
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.db import SessionFactoryProvider
from ..infrastructure.models.runtime import RunSubmission


def submission_fingerprint(
    *,
    run_id: uuid.UUID | None = None,
    key_payload: dict[str, Any] | None = None,
    message_id: str | None = None,
) -> str:
    parts = [str(run_id) if run_id else "", str(key_payload or {}), message_id or ""]
    canonical = "|".join(parts)
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
        run_id: uuid.UUID,
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
            row = RunSubmission(
                tenant_id=tenant_id,
                idempotency_key=idempotency_key,
                endpoint=endpoint,
                request_fingerprint=request_fingerprint,
                actor_user_id=actor_user_id,
                run_id=run_id,
                conversation_id=conversation_id,
                first_seq=first_seq,
                last_seq=last_seq,
                status="CLOSED",
            )
            active.add(row)
            await active.commit()
            snap = self._snapshot(row)
            return snap
        finally:
            if owns:
                await active.close()

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
            return await self._find_replay(active, tenant_id, idempotency_key, endpoint, fingerprint)
        finally:
            if owns:
                await active.close()

    async def _find_replay(
        self,
        session: AsyncSession,
        tenant_id: str,
        idempotency_key: str,
        endpoint: str,
        fingerprint: str,
    ) -> dict[str, Any] | None:
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
            return self._snapshot(row)
        # unreachable helper body marker

    @staticmethod
    def _snapshot(row: RunSubmission) -> dict[str, Any]:
        return {
            "run_id": str(row.run_id) if row.run_id else None,
            "conversation_id": str(row.conversation_id) if row.conversation_id else None,
            "first_seq": row.first_seq,
            "last_seq": row.last_seq,
            "status": row.status,
        }


def require_resume_idempotency(
    *, idempotency_key: str | None, input_id: str | None
) -> str:
    """resume 幂等键：显式 Idempotency-Key 优先，缺省取 input.id；均缺失 → COMMON_VALIDATION_ERROR。"""
    from muad_api import AppError
    from muad_api.error_codes import ErrorCode

    resolved = idempotency_key or input_id
    if not resolved:
        raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
    return resolved
