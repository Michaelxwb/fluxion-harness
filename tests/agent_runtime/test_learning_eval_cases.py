"""S-09 / S-14: task family derivation and Golden Set extraction (TASK-002)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from muad_agent_runtime.application.golden_set import (
    GoldenSetExtractor,
    GoldenSetLimits,
)
from muad_agent_runtime.application.task_family import derive_task_family
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import (
    CanonicalEvent,
    Conversation,
    EvalCase,
    RunRecord,
)
from muad_contracts import RunStatus
from muad_contracts.canonical import canonical_json


def _skill_events(count: int, skill: str = "demo") -> list[dict[str, Any]]:
    calls = [
        {
            "id": f"call-{index}",
            "name": "execute_skill",
            "arguments": {"skill_key": skill, "input": {"customer": index}},
        }
        for index in range(count)
    ]
    events: list[dict[str, Any]] = [{"event_type": "ASSISTANT_TURN", "payload": {"tool_calls": calls}}]
    events.extend(
        {
            "event_type": "TOOL_CALL",
            "payload": {"tool_call_id": call["id"], "tool_name": "execute_skill", "status": "OK"},
        }
        for call in calls
    )
    return events


def test_s09_derives_family_from_first_successful_skill() -> None:
    assert derive_task_family(_skill_events(2), agent_id="agent-1") == "skill:demo"

    failed = [
        {
            "event_type": "ASSISTANT_TURN",
            "payload": {
                "tool_calls": [
                    {"id": "c1", "name": "execute_skill", "arguments": {"skill_key": "demo"}}
                ]
            },
        },
        {
            "event_type": "TOOL_CALL",
            "payload": {"tool_call_id": "c1", "tool_name": "execute_skill", "status": "ERROR"},
        },
    ]
    assert derive_task_family(failed, agent_id="agent-1") == "agent:agent-1"
    assert derive_task_family([], agent_id="agent-1") == "agent:agent-1"


async def _seed_run(
    tenant: str,
    *,
    status: RunStatus,
    family: str | None,
    events: list[dict[str, Any]],
    minutes_ago: int = 0,
    signal: bool = False,
    input_text: str = "handle 10 customers",
) -> uuid.UUID:
    conversation_id, run_id = uuid.uuid4(), uuid.uuid4()
    end_time = datetime.now(UTC) - timedelta(minutes=minutes_ago)
    async with get_session_factory()() as session:
        session.add(
            Conversation(
                id=conversation_id,
                tenant_id=tenant,
                user_id=uuid.uuid4(),
                agent_id=uuid.uuid4(),
            )
        )
        await session.flush()
        session.add(
            RunRecord(
                id=run_id,
                tenant_id=tenant,
                conversation_id=conversation_id,
                user_id=uuid.uuid4(),
                agent_id=uuid.uuid4(),
                status=status,
                input_text=input_text,
                trace_id=f"trace-{run_id}",
                end_time=end_time,
                task_family=family,
            )
        )
        await session.flush()
        seq = 0
        for event in events:
            seq += 1
            session.add(
                CanonicalEvent(
                    tenant_id=tenant,
                    conversation_id=conversation_id,
                    run_id=run_id,
                    seq=seq,
                    event_type=event["event_type"],
                    payload_json=event["payload"],
                )
            )
        if signal:
            seq += 1
            session.add(
                CanonicalEvent(
                    tenant_id=tenant,
                    conversation_id=conversation_id,
                    run_id=run_id,
                    seq=seq,
                    event_type="LEARNING_SIGNAL",
                    payload_json={"source_run_id": str(run_id), "signal_type": "NEGATION"},
                )
            )
        await session.commit()
    return run_id


async def _cleanup(tenant: str) -> None:
    async with get_session_factory()() as session:
        await session.execute(sa.delete(EvalCase).where(EvalCase.tenant_id == tenant))
        await session.execute(sa.delete(CanonicalEvent).where(CanonicalEvent.tenant_id == tenant))
        await session.execute(sa.delete(RunRecord).where(RunRecord.tenant_id == tenant))
        await session.execute(sa.delete(Conversation).where(Conversation.tenant_id == tenant))
        await session.commit()


async def _cases(tenant: str) -> list[EvalCase]:
    async with get_session_factory()() as session:
        return list(
            (
                await session.scalars(
                    sa.select(EvalCase)
                    .where(EvalCase.tenant_id == tenant)
                    .order_by(EvalCase.item_count)
                )
            ).all()
        )


def _dump(case: EvalCase) -> str:
    return canonical_json(
        {
            "task_family": case.task_family,
            "input_ref": case.input_ref_json,
            "criteria": case.criteria_json,
            "content_hash": case.content_hash,
        }
    )


async def test_s09_extracts_only_successful_uncorrected_runs(database_guard: None) -> None:
    tenant = f"learn-{uuid.uuid4()}"
    good = await _seed_run(
        tenant,
        status=RunStatus.COMPLETED,
        family="skill:demo",
        events=_skill_events(2),
        minutes_ago=10,
        input_text="10 customers CANARY-XYZ",
    )
    await _seed_run(
        tenant, status=RunStatus.FAILED, family="skill:demo", events=_skill_events(1), minutes_ago=8
    )
    await _seed_run(
        tenant,
        status=RunStatus.CANCELLED,
        family="skill:demo",
        events=_skill_events(1),
        minutes_ago=6,
    )
    await _seed_run(
        tenant,
        status=RunStatus.COMPLETED,
        family="skill:demo",
        events=_skill_events(1),
        minutes_ago=4,
        signal=True,
    )
    try:
        report = await GoldenSetExtractor().extract(tenant)
        assert report.inserted == 1
        assert report.families == ("skill:demo",)

        cases = await _cases(tenant)
        assert len(cases) == 1
        case = cases[0]
        assert case.source_run_id == good
        assert case.task_family == "skill:demo"
        assert case.item_count == 2
        assert [item["tool_call_id"] for item in case.criteria_json["items"]] == ["call-0", "call-1"]
        assert case.criteria_json["incomplete"] is False
        assert case.criteria_json["split"] == "train"
        assert case.input_ref_json["run_id"] == str(good)
        assert "CANARY-XYZ" not in _dump(case)

        # 抽取是幂等的：再次触发不重复落库。
        assert (await GoldenSetExtractor().extract(tenant)).inserted == 0

        async with get_session_factory()() as session:
            statuses = (
                await session.scalars(
                    sa.select(RunRecord.status).where(RunRecord.tenant_id == tenant)
                )
            ).all()
        assert sorted(str(status) for status in statuses) == sorted(
            [
                "COMPLETED",
                "COMPLETED",
                "FAILED",
                "CANCELLED",
            ]
        )
    finally:
        await _cleanup(tenant)


async def test_s14_batch_items_are_recorded_with_item_count_and_cap(database_guard: None) -> None:
    tenant = f"learn-{uuid.uuid4()}"
    await _seed_run(
        tenant,
        status=RunStatus.COMPLETED,
        family="skill:demo",
        events=_skill_events(10),
        minutes_ago=20,
        input_text="10 customers batch",
    )
    await _seed_run(
        tenant,
        status=RunStatus.COMPLETED,
        family="skill:demo",
        events=_skill_events(5),
        minutes_ago=10,
        input_text="5 customers batch",
    )
    try:
        extractor = GoldenSetExtractor(
            limits=GoldenSetLimits(
                max_cases_per_family=5, max_items_per_case=5, holdout_ratio=0.5
            )
        )
        report = await extractor.extract(tenant)
        assert report.inserted == 2
        assert report.incomplete == 1

        cases = await _cases(tenant)
        assert [case.item_count for case in cases] == [5, 10]
        assert [case.criteria_json["split"] for case in cases] == ["holdout", "train"]
        assert cases[0].criteria_json["incomplete"] is False
        assert cases[1].criteria_json["incomplete"] is True
        assert len(cases[1].criteria_json["items"]) == 5
        assert "customers batch" not in _dump(cases[1])
    finally:
        await _cleanup(tenant)
