"""提交幂等台账：一条命令一个 `(tenant_id, idempotency_key, endpoint)` 记录（设计 §3.5.1）。

同一逻辑命令的所有入口共用本模块：`tasks` / `schedule-drafts` / `schedule-confirm` /
`schedule-actions` / `execution-cancel` / `human-respond`。协议固定为

1. `admit()`：先取 `pg_advisory_xact_lock((tenant_id, idempotency_key, endpoint))` 串行化同键，
   再查首次提交——同指纹返回重放记录，异指纹抛 `IDEMPOTENCY_MISMATCH`；
2. 调用方在**同一事务**里写业务行；
3. `record_in()`：写首次响应快照；partial unique 是并发插入的兜底。
"""

from __future__ import annotations

import hashlib
import uuid
from enum import StrEnum
from typing import Any

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts import NonCanonicalJsonError, canonical_json
from pydantic import BaseModel, JsonValue
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.models.task_submission import TaskSubmission

IDEMPOTENCY_HEADER = "Idempotency-Key"
FINGERPRINT_PREFIX = "sha256:"


class SubmissionEndpoint(StrEnum):
    """短标识固定枚举：不放完整 URL 或 UUID。"""

    TASKS = "tasks"
    SCHEDULE_DRAFTS = "schedule-drafts"
    SCHEDULE_CONFIRM = "schedule-confirm"
    SCHEDULE_ACTIONS = "schedule-actions"
    EXECUTION_CANCEL = "execution-cancel"
    HUMAN_RESPOND = "human-respond"


#: `api/tasks.py` 与 `api/schedules.py` 仍按旧名导入；两处调用点由各自的
#: API TASK 重写，值已经是 v2 的 endpoint 短标识。
ENDPOINT_CREATE_TASK = SubmissionEndpoint.TASKS
ENDPOINT_CREATE_SCHEDULE = SubmissionEndpoint.SCHEDULE_DRAFTS


def resolve_idempotency_key(header_key: str | None, body_key: str | None) -> str:
    """合并 `Idempotency-Key` Header 与 body 幂等键。

    两者同时出现时必须一致；只有其中一个时取该值。
    """
    if header_key and body_key and header_key != body_key:
        raise AppError(ErrorCode.IDEMPOTENCY_MISMATCH)
    resolved = header_key or body_key
    if not resolved:
        raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
    return resolved


def submission_fingerprint(
    *,
    endpoint: SubmissionEndpoint,
    tenant_id: str,
    actor_user_id: uuid.UUID,
    target_id: uuid.UUID | None = None,
    action: BaseModel | dict[str, Any] | None = None,
) -> str:
    """规范化 JSON 指纹：键序无关，同一逻辑命令稳定。

    判别键必须齐：`endpoint` + `tenant_id` + actor + 目标 id + 动作载荷。少任何一项，
    不同命令就可能撞成同一指纹而互相重放。
    """
    if isinstance(action, BaseModel):
        body: dict[str, Any] = action.model_dump(mode="json")
    else:
        body = dict(action or {})
    try:
        canonical = canonical_json(
            {
                "endpoint": str(endpoint),
                "tenant_id": tenant_id,
                "actor_user_id": str(actor_user_id),
                "target_id": str(target_id) if target_id is not None else None,
                "action": body,
            }
        )
    except NonCanonicalJsonError as exc:
        # 规范化 JSON 只认严格 JSON（见 `muad_contracts.canonical`）：非有限数/未知类型在
        # **请求边界**就翻成校验错，而不是等到写 jsonb 列才被 PG 拒。
        raise AppError(ErrorCode.COMMON_VALIDATION_ERROR) from exc
    return FINGERPRINT_PREFIX + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _advisory_key(tenant_id: str, idempotency_key: str, endpoint: SubmissionEndpoint) -> int:
    identity = canonical_json(["task_submission", tenant_id, idempotency_key, str(endpoint)])
    return int.from_bytes(hashlib.sha256(identity.encode()).digest()[:8], "big", signed=True)


class TaskSubmissionService:
    """提交幂等记录与重放（不自行提交事务，由调用方掌控）。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def admit(
        self,
        *,
        tenant_id: str,
        idempotency_key: str,
        endpoint: SubmissionEndpoint,
        fingerprint: str,
    ) -> TaskSubmission | None:
        """同键串行化入口：先 advisory 锁，再复查首次提交。

        返回既有记录 ⇒ 调用方重放首次持久化结果；返回 None ⇒ 本事务是首次提交。
        """
        await self._session.execute(
            select(func.pg_advisory_xact_lock(_advisory_key(tenant_id, idempotency_key, endpoint)))
        )
        return await self.find_replay(
            tenant_id=tenant_id,
            idempotency_key=idempotency_key,
            endpoint=endpoint,
            fingerprint=fingerprint,
        )

    async def find_replay(
        self,
        *,
        tenant_id: str,
        idempotency_key: str,
        endpoint: SubmissionEndpoint,
        fingerprint: str,
    ) -> TaskSubmission | None:
        """同键同指纹返回首次提交记录；同键异指纹抛 `IDEMPOTENCY_MISMATCH`。"""
        row = await self._find_row(tenant_id, idempotency_key, endpoint)
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
        endpoint: SubmissionEndpoint,
        actor_user_id: uuid.UUID,
        request_fingerprint: str,
        response: dict[str, JsonValue],
        task_id: uuid.UUID | None = None,
        schedule_id: uuid.UUID | None = None,
        schedule_draft_id: uuid.UUID | None = None,
    ) -> TaskSubmission:
        """与业务行同事务写入首次响应（不 commit）。

        partial unique 是 advisory 锁之外的兜底：并发落败者拿回赢家那一行，并按指纹判定
        重放还是冲突——绝不静默覆盖首次结果。
        """
        row = TaskSubmission(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            idempotency_key=idempotency_key,
            endpoint=str(endpoint),
            request_fingerprint=request_fingerprint,
            actor_user_id=actor_user_id,
            task_id=task_id,
            schedule_id=schedule_id,
            schedule_draft_id=schedule_draft_id,
            response_json=response,
        )
        try:
            async with self._session.begin_nested():
                self._session.add(row)
                await self._session.flush()
        except IntegrityError:
            winner = await self._find_row(tenant_id, idempotency_key, endpoint)
            if winner is None:
                raise
            if winner.request_fingerprint != request_fingerprint:
                raise AppError(ErrorCode.IDEMPOTENCY_MISMATCH) from None
            return winner
        return row

    async def _find_row(
        self, tenant_id: str, idempotency_key: str, endpoint: SubmissionEndpoint
    ) -> TaskSubmission | None:
        return (
            await self._session.execute(
                select(TaskSubmission).where(
                    TaskSubmission.tenant_id == tenant_id,
                    TaskSubmission.idempotency_key == idempotency_key,
                    TaskSubmission.endpoint == str(endpoint),
                    TaskSubmission.is_deleted.is_(False),
                )
            )
        ).scalar_one_or_none()
