from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.models.task_submission import TaskSubmission

ENDPOINT_CREATE_TASK = "create-task"
ENDPOINT_CREATE_SCHEDULE = "create-schedule"

IDEMPOTENCY_HEADER = "Idempotency-Key"
FINGERPRINT_PREFIX = "sha256:"


def resolve_idempotency_key(header_key: str | None, body_key: str | None) -> str:
    """合并 `Idempotency-Key` Header 与 body 幂等键（设计 §3.3.8 / API-01 / API-02）。

    两者同时出现时必须一致；只有其中一个时取该值。
    """
    if header_key and body_key and header_key != body_key:
        raise AppError(ErrorCode.IDEMPOTENCY_MISMATCH)
    resolved = header_key or body_key
    if not resolved:
        raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
    return resolved


def submission_fingerprint(endpoint: str, payload: BaseModel | dict[str, Any]) -> str:
    """规范化 JSON 指纹：键序无关、同一逻辑请求稳定。

    口径与模块 08 的 `runtime.run_submission` 一致（sort_keys + 紧凑分隔符 +
    `sha256:` 前缀）。body 已包含 tenant/actor 与关键参数。
    """
    body = payload.model_dump(mode="json") if isinstance(payload, BaseModel) else payload
    canonical = json.dumps(
        {"endpoint": endpoint, "payload": body},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return FINGERPRINT_PREFIX + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class TaskSubmissionService:
    """创建类 POST 的提交幂等记录与重放（不自行提交事务，由调用方掌控）。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_replay(
        self,
        *,
        tenant_id: str,
        idempotency_key: str,
        endpoint: str,
        fingerprint: str,
    ) -> TaskSubmission | None:
        """同键同指纹返回首次提交记录；同键异指纹抛 `IDEMPOTENCY_MISMATCH`。"""
        row = (
            await self._session.execute(
                select(TaskSubmission).where(
                    TaskSubmission.tenant_id == tenant_id,
                    TaskSubmission.idempotency_key == idempotency_key,
                    TaskSubmission.endpoint == endpoint,
                    TaskSubmission.is_deleted.is_(False),
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        if row.request_fingerprint != fingerprint:
            raise AppError(ErrorCode.IDEMPOTENCY_MISMATCH)
        return row

    async def record_in(
        self,
        *,
        tenant_id: str,
        idempotency_key: str,
        endpoint: str,
        actor_user_id: uuid.UUID,
        request_fingerprint: str,
        response: dict[str, Any],
        task_id: uuid.UUID | None = None,
        schedule_id: uuid.UUID | None = None,
    ) -> TaskSubmission:
        """与业务行同事务写入提交记录（不 commit）。"""
        row = TaskSubmission(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            idempotency_key=idempotency_key,
            endpoint=endpoint,
            request_fingerprint=request_fingerprint,
            actor_user_id=actor_user_id,
            task_id=task_id,
            schedule_id=schedule_id,
            response_json=response,
        )
        self._session.add(row)
        await self._session.flush()
        return row
