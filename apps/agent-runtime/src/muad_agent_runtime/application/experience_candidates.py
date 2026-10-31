"""Cross-Run aggregation into PROPOSED experience candidates.

候选只进入 PROPOSED，不参与注入；写入复用 `MemoryService` 的命令路径（advisory 锁 + scope 锁 +
幂等回执 + history），已删除/禁用的 key 不再复活。
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import sqlalchemy as sa
from muad_contracts.memory import (
    FactContent,
    MemoryCandidate,
    MemoryCategory,
    MemorySourceEvidence,
)
from muad_contracts.platform_settings import MemoryPolicySettings
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.db import SessionFactory, get_session_factory
from ..infrastructure.models.runtime import CanonicalEvent, MemoryScope, RunRecord, UserMemory
from .memory_commands import MemoryCommand, MemoryMutation, MemoryWriteContext
from .memory_service import MemoryService
from .task_family import SKILL_TOOLS, SUCCESS_STATUS

logger = logging.getLogger(__name__)

EXPERIENCE_TTL = timedelta(days=365)
PROCEDURE = MemoryCategory.PROCEDURE


@dataclass(frozen=True, slots=True)
class CandidateLimits:
    min_success_runs: int = 3
    lookback_days: int = 30


@dataclass(frozen=True, slots=True)
class CandidateReport:
    created: int
    skipped: int
    families: tuple[str, ...]


def experience_key(task_family: str) -> str:
    digest = hashlib.sha256(task_family.encode("utf-8")).hexdigest()[:16]
    return f"exp.proc.{digest}"


def _procedure_text(task_family: str, batch: bool) -> str:
    if task_family.startswith("skill:"):
        skill = task_family.split(":", 1)[1]
        if batch:
            return (
                f"处理同类批量任务时：逐个目标调用 {skill}，记录每一项结果，"
                "核对目标数量与失败项后再汇总；不要只处理第一个目标。"
            )
        return f"处理同类任务时：先确认目标与输入，再调用 {skill}，核对结果后再回答。"
    if batch:
        return "处理同类批量请求时：先列出全部目标，再逐项处理并核对数量与失败项后汇总。"
    return "处理同类请求时：先确认目标与约束，再执行并核对结果后回答。"


def _trigger_text(task_family: str, batch: bool) -> str:
    return f"{task_family}；批量目标、逐项处理并核对数量" if batch else task_family


class ExperienceCandidates:
    def __init__(
        self,
        session_factory: SessionFactory | None = None,
        service: MemoryService | None = None,
        limits: CandidateLimits | None = None,
    ) -> None:
        self._factory = session_factory or get_session_factory()
        self._service = service or MemoryService(self._factory)
        self._limits = limits or CandidateLimits()

    async def generate(
        self,
        tenant_id: str,
        *,
        task_family: str | None = None,
        now: datetime | None = None,
    ) -> CandidateReport:
        moment = now or datetime.now(UTC)
        async with self._factory() as session:
            runs = await self._successful_runs(session, tenant_id, task_family, moment)
            calls = await self._skill_calls(session, tenant_id, [run.id for run in runs])
            excluded = await self._runs_with_signals(session, tenant_id, [run.id for run in runs])
        by_family: dict[str, list[RunRecord]] = {}
        for run in runs:
            if run.id in excluded or not run.task_family:
                continue
            by_family.setdefault(run.task_family, []).append(run)

        created = skipped = 0
        families: list[str] = []
        for family, family_runs in sorted(by_family.items()):
            if len(family_runs) < self._limits.min_success_runs:
                skipped += 1
                continue
            key = experience_key(family)
            if await self._blocked(tenant_id, family_runs[0].user_id, key):
                skipped += 1
                continue
            evidence = await self._evidence(
                tenant_id, family_runs[0].user_id, family_runs[-1].id
            )
            if evidence is None:
                skipped += 1
                continue
            item_total = sum(len(calls.get(run.id, [])) for run in family_runs)
            batch = any(len(calls.get(run.id, [])) > 1 for run in family_runs)
            candidate = MemoryCandidate(
                memory_key=key,
                category=PROCEDURE,
                content=FactContent(value=_procedure_text(family, batch)),
            )
            context = MemoryWriteContext(
                tenant_id=tenant_id,
                user_id=family_runs[0].user_id,
                actor_type="AGENT",
                actor_id="learning",
                settings=MemoryPolicySettings(),
                source_run_id=family_runs[-1].id,
                evidence=evidence,
            )
            command = MemoryCommand(
                command_id=f"experience:{tenant_id}:{key}:{family_runs[-1].id}",
                endpoint="learning.experience",
                mutations=[
                    MemoryMutation(
                        action="UPSERT",
                        candidate=candidate,
                        expires_at=moment + EXPERIENCE_TTL,
                    )
                ],
            )
            async with self._factory.begin() as session:
                outcome = await self._service.execute_in_session(session, context, command)
                if outcome.response.status == "STALE":
                    skipped += 1
                    continue
                await self._apply_metadata(
                    session, tenant_id, family_runs[0].user_id, key, family, family_runs,
                    item_total, batch,
                )
            created += 1
            families.append(family)
        return CandidateReport(created, skipped, tuple(families))

    async def _evidence(
        self, tenant_id: str, user_id: UUID, run_id: UUID
    ) -> MemorySourceEvidence | None:
        """AGENT 写入必须有真实来源：取来源 Run 的最后一条消息事件与当前 scope revision。"""
        async with self._factory() as session:
            event = await session.scalar(
                sa.select(CanonicalEvent)
                .where(
                    CanonicalEvent.tenant_id == tenant_id,
                    CanonicalEvent.run_id == run_id,
                    CanonicalEvent.event_type.in_(("USER_MESSAGE", "ASSISTANT_MESSAGE")),
                    CanonicalEvent.is_deleted.is_(False),
                )
                .order_by(CanonicalEvent.seq.desc())
                .limit(1)
            )
            if event is None:
                return None
            scope = await session.scalar(
                sa.select(MemoryScope).where(
                    MemoryScope.tenant_id == tenant_id, MemoryScope.user_id == user_id
                )
            )
        return MemorySourceEvidence(
            source_event_id=event.id,
            source_seq=event.seq if event.seq > 0 else 1,
            source_scope_revision=scope.revision if scope is not None else 0,
        )

    async def _successful_runs(
        self, session: AsyncSession, tenant_id: str, task_family: str | None, now: datetime
    ) -> list[RunRecord]:
        statement = sa.select(RunRecord).where(
            RunRecord.tenant_id == tenant_id,
            RunRecord.status == "COMPLETED",
            RunRecord.is_deleted.is_(False),
            RunRecord.task_family.is_not(None),
            RunRecord.end_time >= now - timedelta(days=self._limits.lookback_days),
        )
        if task_family is not None:
            statement = statement.where(RunRecord.task_family == task_family)
        return list((await session.scalars(statement.order_by(RunRecord.end_time))).all())

    async def _runs_with_signals(
        self, session: AsyncSession, tenant_id: str, run_ids: list[UUID]
    ) -> set[UUID]:
        if not run_ids:
            return set()
        wanted = set(run_ids)
        rows = await session.execute(
            sa.select(CanonicalEvent.run_id, CanonicalEvent.payload_json).where(
                CanonicalEvent.tenant_id == tenant_id,
                CanonicalEvent.event_type == "LEARNING_SIGNAL",
                CanonicalEvent.is_deleted.is_(False),
            )
        )
        excluded: set[UUID] = set()
        for run_id, payload in rows:
            if run_id in wanted:
                excluded.add(run_id)
                continue
            source = payload.get("source_run_id") if isinstance(payload, dict) else None
            if isinstance(source, str):
                try:
                    if UUID(source) in wanted:
                        excluded.add(UUID(source))
                except ValueError:
                    continue
        return excluded

    async def _skill_calls(
        self, session: AsyncSession, tenant_id: str, run_ids: list[UUID]
    ) -> dict[UUID, list[str]]:
        if not run_ids:
            return {}
        rows = await session.execute(
            sa.select(CanonicalEvent.run_id, CanonicalEvent.payload_json)
            .where(
                CanonicalEvent.tenant_id == tenant_id,
                CanonicalEvent.run_id.in_(run_ids),
                CanonicalEvent.event_type == "TOOL_CALL",
                CanonicalEvent.is_deleted.is_(False),
            )
            .order_by(CanonicalEvent.seq)
        )
        calls: dict[UUID, list[str]] = {}
        for run_id, payload in rows:
            if not isinstance(payload, dict):
                continue
            if (
                payload.get("tool_name") in SKILL_TOOLS
                and payload.get("status") == SUCCESS_STATUS
                and isinstance(payload.get("tool_call_id"), str)
            ):
                calls.setdefault(run_id, []).append(payload["tool_call_id"])
        return calls

    async def _blocked(self, tenant_id: str, user_id: UUID, key: str) -> bool:
        """已删除/禁用/已存在的 key 一律跳过：不复活、不覆盖既有经验。"""
        async with self._factory() as session:
            row = await session.scalar(
                sa.select(UserMemory).where(
                    UserMemory.tenant_id == tenant_id,
                    UserMemory.user_id == user_id,
                    UserMemory.memory_key == key,
                )
            )
        return row is not None

    async def _apply_metadata(
        self,
        session: AsyncSession,
        tenant_id: str,
        user_id: UUID,
        key: str,
        family: str,
        runs: list[RunRecord],
        item_total: int,
        batch: bool,
    ) -> None:
        """经验元数据与内容同一事务写入；行锁在 scope 锁之后取得，不形成锁序环。"""
        row = await session.scalar(
            sa.select(UserMemory)
            .where(
                UserMemory.tenant_id == tenant_id,
                UserMemory.user_id == user_id,
                UserMemory.memory_key == key,
            )
            .with_for_update()
        )
        if row is None:
            return
        row.task_signature = family
        row.trigger_text = _trigger_text(family, batch)
        row.activation_state = "PROPOSED"
        row.evidence_json = {
            "task_family": family,
            "source_run_ids": [str(run.id) for run in runs],
            "success_runs": len(runs),
            "item_total": item_total,
        }
