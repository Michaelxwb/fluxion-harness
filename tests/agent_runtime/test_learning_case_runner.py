"""S-04: production CaseRunner replays from frozen Snapshot (TASK-003)."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import sqlalchemy as sa
from muad_agent_runtime.application.learning_case_runner import (
    ProductionCaseRunner,
    RunnerLimits,
    judge_answer,
)
from muad_agent_runtime.application.run_service import build_snapshot
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import (
    CanonicalEvent,
    Conversation,
    EvalCase,
    RunRecord,
    RuntimeSnapshot,
    UserMemory,
)
from muad_contracts import ResolvedAgent, ResolveDefinitionResponse, ResolvedModel, RunStatus


def _response(text: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [
                {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": text}}
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        },
    )


async def _seed(tenant: str, user: uuid.UUID) -> tuple[RunRecord, EvalCase]:
    conversation_id, run_id = uuid.uuid4(), uuid.uuid4()
    async with get_session_factory()() as session:
        session.add(Conversation(id=conversation_id, tenant_id=tenant, user_id=user, agent_id=uuid.uuid4()))
        await session.flush()
        run = RunRecord(
            id=run_id,
            tenant_id=tenant,
            conversation_id=conversation_id,
            user_id=user,
            agent_id=uuid.uuid4(),
            status=RunStatus.COMPLETED,
            input_text="请输出客户 A 的 JSON",
            trace_id=f"trace-{run_id}",
            end_time=datetime.now(UTC),
            task_family="skill:demo",
        )
        session.add(run)
        await session.flush()
        resolved = ResolveDefinitionResponse(
            agent=ResolvedAgent(
                id=run.agent_id, key="demo", revision=1, instructions="你是助手", runtime_config={}
            ),
            model=ResolvedModel(
                id=uuid.uuid4(), revision=1, model_id="probe", base_url="http://replay.test"
            ),
        )
        snapshot = build_snapshot(run_id=run.id, tenant_id=tenant, resolved=resolved)
        session.add(snapshot)
        await session.flush()
        run.snapshot_id = snapshot.id
        session.add(
            CanonicalEvent(
                tenant_id=tenant,
                conversation_id=conversation_id,
                run_id=run.id,
                seq=1,
                event_type="ASSISTANT_MESSAGE",
                payload_json={"text": '{"name":"A","amount":10}'},
            )
        )
        case = EvalCase(
            tenant_id=tenant,
            task_family="skill:demo",
            source_run_id=run.id,
            input_ref_json={"run_id": str(run.id)},
            criteria_json={"items": [], "incomplete": False, "split": "train"},
            item_count=1,
            content_hash="hash-1",
            version=1,
        )
        session.add(case)
        session.add(
            UserMemory(
                tenant_id=tenant,
                user_id=user,
                memory_key="exp.json",
                category="PROCEDURE",
                content_json={"value": "输出 JSON，包含 name 与 amount 字段"},
                author="AGENT",
                confidence=2,
                explicit_request=False,
                expires_at=datetime.now(UTC) + timedelta(days=365),
                activation_state="ACTIVE",
                task_signature="skill:demo",
                trigger_text="客户 JSON",
            )
        )
        await session.commit()
        await session.refresh(case)
    return run, case


async def _cleanup(tenant: str) -> None:
    async with get_session_factory()() as session:
        await session.execute(sa.delete(EvalCase).where(EvalCase.tenant_id == tenant))
        await session.execute(sa.delete(UserMemory).where(UserMemory.tenant_id == tenant))
        await session.execute(sa.delete(CanonicalEvent).where(CanonicalEvent.tenant_id == tenant))
        run_ids = list(
            (await session.scalars(sa.select(RunRecord.id).where(RunRecord.tenant_id == tenant))).all()
        )
        if run_ids:
            await session.execute(sa.delete(RuntimeSnapshot).where(RuntimeSnapshot.run_id.in_(run_ids)))
        await session.execute(sa.delete(RunRecord).where(RunRecord.tenant_id == tenant))
        await session.execute(sa.delete(Conversation).where(Conversation.tenant_id == tenant))
        await session.commit()


def test_judge_answer_prefers_json_structure() -> None:
    assert judge_answer('{"name":"A","amount":10}', '{"name":"A","amount":10}') == (True, "")
    ok, reason = judge_answer('{"name":"A","amount":10}', '{"name":"A"}')
    assert not ok and "missing keys" in reason
    ok, reason = judge_answer("客户 A 欠款 10 元", "客户 A 欠款 10 元，币种 CNY")
    assert ok
    ok, reason = judge_answer("客户 A 欠款 10 元", "无关内容")
    assert not ok and "coverage" in reason


async def test_s04_two_arm_requests_rebuilt_from_snapshot(database_guard: None) -> None:
    tenant, user = f"case-{uuid.uuid4()}", uuid.uuid4()
    run, case = await _seed(tenant, user)
    captured: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        captured.append(body)
        system = " ".join(
            item.get("content", "") for item in body["messages"] if item.get("role") == "system"
        )
        if "参考经验" in system:
            return _response('{"name":"A","amount":10}')
        return _response("已整理")

    async def credentials(tenant_id: str, model: object) -> str:
        return "fixture-key"

    runner = ProductionCaseRunner(
        get_session_factory(),
        resolve_credentials=credentials,
        limits=RunnerLimits(max_tokens=64, timeout_sec=5),
        transport=httpx.MockTransport(handler),
    )
    try:
        candidate = await runner(case, "exp.json", user)
        baseline = await runner(case, None, None)
        assert candidate.passed is True and candidate.items_passed == 1
        assert baseline.passed is False and baseline.items_passed == 0

        assert len(captured) == 2
        candidate_system = captured[0]["messages"][0]["content"]
        baseline_system = captured[1]["messages"][0]["content"]
        assert "参考经验" in candidate_system and "exp.json" not in candidate_system
        assert "参考经验" not in baseline_system
        for body in captured:
            user_messages = [item["content"] for item in body["messages"] if item.get("role") == "user"]
            assert user_messages == [run.input_text]
            assert body["model"] == "probe"
    finally:
        await _cleanup(tenant)
