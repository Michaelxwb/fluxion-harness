"""经验生命周期写入口：状态机 + 异步回放 job（Runtime 侧唯一权威）。"""

from __future__ import annotations

import hashlib
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts.platform_settings import LearningSettings, parse_platform_settings

from ..infrastructure.db import SessionFactory
from ..infrastructure.models.runtime import LearningJob, UserMemory
from .learning_activation import (
    BELOW_THRESHOLD,
    INVALID_TRANSITION,
    NOT_FOUND,
    ActivationOutcome,
    LearningActivation,
)
from .ports import PlatformSettingsClient


class LearningAdminService:
    def __init__(
        self,
        factory: SessionFactory,
        *,
        activation: LearningActivation | None = None,
        settings_client: PlatformSettingsClient | None = None,
    ) -> None:
        self._factory = factory
        self._activation = activation or LearningActivation(factory)
        self._settings = settings_client

    async def learning_settings(self, tenant_id: str) -> LearningSettings:
        if self._settings is None:
            return parse_platform_settings({}).learning
        snapshot = await self._settings.fetch_snapshot(tenant_id=tenant_id)
        return parse_platform_settings(snapshot.settings).learning

    @staticmethod
    def _base_hash(tenant_id: str, family: str) -> str:
        return "sha256:" + hashlib.sha256(f"{tenant_id}:{family}".encode()).hexdigest()[:16]

    async def _family(self, tenant_id: str, user_id: UUID, key: str) -> str:
        async with self._factory() as session:
            family = await session.scalar(
                sa.select(UserMemory.task_signature).where(
                    UserMemory.tenant_id == tenant_id,
                    UserMemory.user_id == user_id,
                    UserMemory.memory_key == key,
                    UserMemory.is_deleted.is_(False),
                )
            )
        if family is None:
            raise AppError(ErrorCode.LEARNING_EXPERIENCE_NOT_FOUND)
        return str(family)

    async def _require_enabled(self, tenant_id: str) -> None:
        if not (await self.learning_settings(tenant_id)).learning_enabled:
            raise AppError(ErrorCode.LEARNING_DISABLED)

    async def enqueue_replay(
        self, tenant_id: str, user_id: UUID, key: str, *, command_id: str
    ) -> LearningJob:
        await self._require_enabled(tenant_id)
        async with self._factory.begin() as session:
            family = await session.scalar(
                sa.select(UserMemory.task_signature).where(
                    UserMemory.tenant_id == tenant_id,
                    UserMemory.user_id == user_id,
                    UserMemory.memory_key == key,
                    UserMemory.is_deleted.is_(False),
                )
            )
            if family is None:
                raise AppError(ErrorCode.LEARNING_EXPERIENCE_NOT_FOUND)
            base_hash = "sha256:" + hashlib.sha256(
                f"{tenant_id}:{family}".encode()
            ).hexdigest()[:16]
            request = {"task_family": family, "base_config_hash": base_hash}
            await session.execute(
                sa.text(
                    "INSERT INTO runtime.learning_job"
                    " (tenant_id, user_id, experience_key, job_type, status, command_id, request_json)"
                    " VALUES (:tenant, :user, :key, 'REPLAY', 'PENDING', :command,"
                    " CAST(:request AS jsonb))"
                    " ON CONFLICT (tenant_id, command_id) WHERE is_deleted=false DO NOTHING"
                ),
                {
                    "tenant": tenant_id,
                    "user": user_id,
                    "key": key,
                    "command": command_id,
                    "request": __import__("json").dumps(request),
                },
            )
            job = await session.scalar(
                sa.select(LearningJob).where(
                    LearningJob.tenant_id == tenant_id,
                    LearningJob.command_id == command_id,
                    LearningJob.is_deleted.is_(False),
                )
            )
        if job is None:
            raise AppError(ErrorCode.COMMON_INTERNAL_ERROR)
        return job

    async def activate(
        self,
        tenant_id: str,
        user_id: UUID,
        key: str,
        *,
        operator: str,
        reason: str | None,
        base_config_hash: str,
    ) -> ActivationOutcome:
        await self._require_enabled(tenant_id)
        if not base_config_hash:
            base_config_hash = self._base_hash(tenant_id, await self._family(tenant_id, user_id, key))
        outcome = await self._activation.activate_manually(
            tenant_id,
            user_id,
            key,
            base_config_hash=base_config_hash,
            operator=operator,
            reason=reason,
        )
        return self._raise_for(outcome)

    async def retire(
        self,
        tenant_id: str,
        user_id: UUID,
        key: str,
        *,
        operator: str,
        reason: str,
        base_config_hash: str = "",
    ) -> ActivationOutcome:
        await self._require_enabled(tenant_id)
        if not base_config_hash:
            base_config_hash = self._base_hash(tenant_id, await self._family(tenant_id, user_id, key))
        outcome = await self._activation.retire(
            tenant_id,
            user_id,
            key,
            reason=reason,
            operator=operator,
            base_config_hash=base_config_hash,
        )
        return self._raise_for(outcome)

    async def regenerate(
        self,
        tenant_id: str,
        user_id: UUID,
        key: str,
        *,
        operator: str,
        reason: str,
        base_config_hash: str = "",
    ) -> ActivationOutcome:
        await self._require_enabled(tenant_id)
        if not base_config_hash:
            base_config_hash = self._base_hash(tenant_id, await self._family(tenant_id, user_id, key))
        outcome = await self._activation._transition(
            tenant_id,
            user_id,
            key,
            target="PROPOSED",
            action="REGENERATE",
            operator=operator,
            base_config_hash=base_config_hash,
            reason=reason,
        )
        return self._raise_for(outcome)

    @staticmethod
    def _raise_for(outcome: ActivationOutcome) -> ActivationOutcome:
        if outcome.status == NOT_FOUND:
            raise AppError(ErrorCode.LEARNING_EXPERIENCE_NOT_FOUND)
        if outcome.status == INVALID_TRANSITION:
            raise AppError(ErrorCode.LEARNING_INVALID_TRANSITION)
        if outcome.status == BELOW_THRESHOLD:
            raise AppError(ErrorCode.LEARNING_REPLAY_GATE_FAILED)
        return outcome

    async def job(self, tenant_id: str, job_id: UUID) -> LearningJob:
        async with self._factory() as session:
            job = await session.scalar(
                sa.select(LearningJob).where(
                    LearningJob.tenant_id == tenant_id,
                    LearningJob.id == job_id,
                    LearningJob.is_deleted.is_(False),
                )
            )
        if job is None:
            raise AppError(ErrorCode.LEARNING_EXPERIENCE_NOT_FOUND)
        return job

    @staticmethod
    def job_json(job: LearningJob) -> dict[str, Any]:
        return {
            "id": str(job.id),
            "status": job.status,
            "job_type": job.job_type,
            "user_id": str(job.user_id),
            "experience_key": job.experience_key,
            "result": job.result_json,
            "error_code": job.error_code,
            "error_message": job.error_message,
            "create_time": job.create_time.isoformat(),
            "update_time": job.update_time.isoformat(),
        }
