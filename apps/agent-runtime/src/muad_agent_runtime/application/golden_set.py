"""Golden Set extraction from real successful Runs.

抽取是**幂等**的：同一来源 Run 只落一条 version=1 的用例；重复触发只补缺失行。
输入正文不落库（只存 Run 引用与输入指纹），因此 canary 文本不会进入 eval_case。
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import sqlalchemy as sa
from muad_contracts import RunStatus
from muad_contracts.canonical import canonical_json
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.db import SessionFactory, get_session_factory
from ..infrastructure.models.runtime import CanonicalEvent, EvalCase, RunRecord
from .task_family import SKILL_TOOLS, SUCCESS_STATUS

NEGATIVE_SIGNAL_EVENT = "LEARNING_SIGNAL"


@dataclass(frozen=True, slots=True)
class GoldenSetLimits:
    max_cases_per_family: int = 50
    max_items_per_case: int = 10
    lookback_days: int = 30
    holdout_ratio: float = 0.2


@dataclass(frozen=True, slots=True)
class ExtractionReport:
    inserted: int
    skipped: int
    incomplete: int
    families: tuple[str, ...]


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _split(n: int, index: int, ratio: float) -> str:
    if n < 2:
        return "train"
    holdout = max(1, math.ceil(n * ratio))
    return "holdout" if index >= n - holdout else "train"


class GoldenSetExtractor:
    def __init__(
        self,
        session_factory: SessionFactory | None = None,
        limits: GoldenSetLimits | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._limits = limits or GoldenSetLimits()

    def _sessions(self) -> SessionFactory:
        return self._session_factory if self._session_factory is not None else get_session_factory()

    async def extract(
        self,
        tenant_id: str,
        *,
        task_family: str | None = None,
        now: datetime | None = None,
    ) -> ExtractionReport:
        moment = now or datetime.now(UTC)
        limits = self._limits
        async with self._sessions()() as session:
            runs = await self._candidate_runs(session, tenant_id, task_family, moment)
            excluded = await self._runs_with_signals(session, tenant_id, [run.id for run in runs])
            by_family: dict[str, list[RunRecord]] = {}
            for run in runs:
                if run.id in excluded or not run.task_family:
                    continue
                by_family.setdefault(run.task_family, []).append(run)

            inserted = skipped = incomplete = 0
            for family, family_runs in sorted(by_family.items()):
                existing = await session.scalar(
                    sa.select(sa.func.count())
                    .select_from(EvalCase)
                    .where(
                        EvalCase.tenant_id == tenant_id,
                        EvalCase.task_family == family,
                        EvalCase.is_deleted.is_(False),
                    )
                )
                available = max(0, limits.max_cases_per_family - int(existing or 0))
                family_runs.sort(key=lambda row: (row.end_time, row.id))
                selected = family_runs[-available:] if available else []
                skipped += len(family_runs) - len(selected)
                for index, run in enumerate(selected):
                    calls = await self._skill_calls(session, tenant_id, run.id)
                    row_incomplete = len(calls) > limits.max_items_per_case
                    criteria = {
                        "items": [
                            {"tool_call_id": call_id, "status": status}
                            for call_id, status in calls[: limits.max_items_per_case]
                        ],
                        "incomplete": row_incomplete,
                        "split": _split(len(selected), index, limits.holdout_ratio),
                    }
                    statement = (
                        pg_insert(EvalCase)
                        .values(
                            tenant_id=tenant_id,
                            task_family=family,
                            source_run_id=run.id,
                            input_ref_json={
                                "run_id": str(run.id),
                                "conversation_id": str(run.conversation_id),
                                "snapshot_id": str(run.snapshot_id) if run.snapshot_id else None,
                                "input_sha256": _sha256(run.input_text),
                            },
                            criteria_json=criteria,
                            item_count=max(1, len(calls)),
                            content_hash=_sha256(
                                canonical_json(
                                    {
                                        "tenant": tenant_id,
                                        "family": family,
                                        "run": str(run.id),
                                        "input": run.input_text,
                                    }
                                )
                            ),
                            version=1,
                        )
                        .on_conflict_do_nothing(
                            index_elements=["tenant_id", "task_family", "source_run_id", "version"]
                        )
                        .returning(EvalCase.id)
                    )
                    created = (await session.execute(statement)).scalar_one_or_none()
                    if created is None:
                        skipped += 1
                    else:
                        inserted += 1
                        incomplete += int(row_incomplete)
            await session.commit()
        return ExtractionReport(inserted, skipped, incomplete, tuple(sorted(by_family)))

    async def _candidate_runs(
        self,
        session: AsyncSession,
        tenant_id: str,
        task_family: str | None,
        now: datetime,
    ) -> Sequence[RunRecord]:
        statement = sa.select(RunRecord).where(
            RunRecord.tenant_id == tenant_id,
            RunRecord.status == RunStatus.COMPLETED,
            RunRecord.is_deleted.is_(False),
            RunRecord.task_family.is_not(None),
            RunRecord.end_time >= now - timedelta(days=self._limits.lookback_days),
        )
        if task_family is not None:
            statement = statement.where(RunRecord.task_family == task_family)
        return (await session.scalars(statement.order_by(RunRecord.end_time))).all()

    async def _runs_with_signals(
        self, session: AsyncSession, tenant_id: str, run_ids: Sequence[UUID]
    ) -> set[UUID]:
        if not run_ids:
            return set()
        rows = await session.execute(
            sa.select(CanonicalEvent.run_id, CanonicalEvent.payload_json).where(
                CanonicalEvent.tenant_id == tenant_id,
                CanonicalEvent.event_type == NEGATIVE_SIGNAL_EVENT,
                CanonicalEvent.is_deleted.is_(False),
            )
        )
        excluded: set[UUID] = set()
        wanted = set(run_ids)
        for run_id, payload in rows:
            source = payload.get("source_run_id") if isinstance(payload, dict) else None
            if run_id in wanted:
                excluded.add(run_id)
            elif isinstance(source, str):
                try:
                    if UUID(source) in wanted:
                        excluded.add(UUID(source))
                except ValueError:
                    continue
        return excluded

    async def _skill_calls(
        self, session: AsyncSession, tenant_id: str, run_id: UUID
    ) -> list[tuple[str, str]]:
        rows = await session.execute(
            sa.select(CanonicalEvent.payload_json)
            .where(
                CanonicalEvent.tenant_id == tenant_id,
                CanonicalEvent.run_id == run_id,
                CanonicalEvent.event_type == "TOOL_CALL",
                CanonicalEvent.is_deleted.is_(False),
            )
            .order_by(CanonicalEvent.seq)
        )
        calls: list[tuple[str, str]] = []
        for (payload,) in rows.all():
            if not isinstance(payload, dict):
                continue
            name = payload.get("tool_name")
            status = payload.get("status")
            call_id = payload.get("tool_call_id")
            if name in SKILL_TOOLS and status == SUCCESS_STATUS and isinstance(call_id, str):
                calls.append((call_id, status))
        return calls
