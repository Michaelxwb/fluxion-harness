"""Worker error → tool message → audit → finalization → export, same Run/call identity."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from muad_agent_core.agent.termination import (
    FailureEvidence,
    FailureState,
    TerminationSnapshot,
)
from muad_agent_core.tools import ToolError, ToolExecutionPipeline, ToolRegistry
from muad_agent_runtime.application.executor import _control_facts
from muad_agent_runtime.application.finalization import (
    build_finalization_facts,
    render_finalization,
)
from muad_agent_runtime.application.finalization.render import (
    render_technical_details,
)
from muad_agent_runtime.application.run_service import RunService
from muad_agent_runtime.application.task_client import TaskSubmissionContext, WorkerTaskClient
from muad_agent_runtime.application.task_tools import (
    DRAFT_SCHEDULE_TOOL,
    BackgroundTaskToolSet,
)
from muad_agent_runtime.infrastructure.audit_writer import RuntimeAuditWriter
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import ToolCallAudit
from muad_console_platform.application.execution_diagnosis import build_diagnosis
from muad_console_platform.application.execution_export_view import build_report
from muad_console_platform.infrastructure.repositories.execution_export_repository import (
    ExecutionExportBundle,
    ExportSection,
)
from muad_contracts import ResolvedAgent, ResolvedModel
from sqlalchemy import delete, select

pytestmark = pytest.mark.integration

TENANT = f"tool-error-flow-{uuid.uuid4()}"
RUN_ID = uuid.uuid4()
CONVERSATION_ID = uuid.uuid4()
USER_ID = uuid.uuid4()
CONTRACT = "SCHEDULE_INVOCATION_CONTRACT_MISSING"
ZH_CONTRACT = "技能的定时执行配置未通过预检"


@pytest.fixture(autouse=True)
async def _cleanup() -> AsyncIterator[None]:
    yield
    async with get_session_factory()() as session:
        await session.execute(delete(ToolCallAudit).where(ToolCallAudit.tenant_id == TENANT))
        await session.commit()


def _context(locale: str = "zh-CN") -> TaskSubmissionContext:
    return TaskSubmissionContext(
        tenant_id=TENANT,
        actor_user_id=USER_ID,
        agent=ResolvedAgent(
            id=uuid.uuid4(), key="agent", revision=1, instructions="hi", runtime_config={}
        ),
        model=ResolvedModel(
            id=uuid.uuid4(),
            revision=1,
            model_id="gpt-4o-mini",
            base_url="http://model",
            params={},
        ),
        skills=(),
        source_run_id=RUN_ID,
        conversation_id=CONVERSATION_ID,
        locale=locale,
    )


def _draft_args() -> dict[str, Any]:
    return {
        "instruction": "每天 9 点提醒我开周会",
        "timezone": "Asia/Shanghai",
        "recurrence": {"kind": "CRON", "cron": "0 9 * * *"},
        "payload": {"kind": "REMINDER", "text": "开周会"},
        "name": "周会提醒",
    }


async def _tool_result(
    payload: object | None,
    *,
    status: int = 422,
    call_id: str = "call-1",
) -> tuple[Any, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET" and request.url.path.endswith("/delivery-targets"):
            return httpx.Response(200, json={"code": "0", "msg": "ok", "data": {"items": []}})
        if payload is None:
            raise httpx.ReadTimeout("worker read timed out")
        return httpx.Response(status, json=payload)

    client = WorkerTaskClient("http://worker", transport=httpx.MockTransport(handler))
    registry = ToolRegistry()
    BackgroundTaskToolSet(client=client, context=_context(), skills=[]).register(registry)
    pipeline = ToolExecutionPipeline(registry=registry)
    try:
        execution = await pipeline.execute(
            pipeline.prepare(
                call_id=call_id,
                tool_name=DRAFT_SCHEDULE_TOOL,
                arguments=_draft_args(),
            )
        )
    finally:
        await client.aclose()
    return execution, requests


async def _record_audit(
    *,
    tool_call_id: str,
    outcome: Any,
    tool_name: str = "draft_schedule",
) -> None:
    retryable, retry_disposition, agent_disposition, diagnostic = _control_facts(outcome)
    writer = RuntimeAuditWriter(
        tenant_id=TENANT,
        run_id=RUN_ID,
        task_id=None,
        conversation_id=CONVERSATION_ID,
        user_id=USER_ID,
        session_factory=get_session_factory,
    )
    await writer.record_tool_call(
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        tool_kind="SKILL",
        prepared_args_hash="sha256:" + "0" * 64,
        args_preview_json={},
        status="OK" if outcome.business_status == "SUCCEEDED" else "ERROR",
        start_time=outcome_time(),
        end_time=outcome_time(),
        latency_ms=1,
        error_code=outcome.error_code,
        business_status=str(outcome.business_status),
        failure_layer=str(outcome.failure_layer),
        exit_code=outcome.exit_code,
        output_truncated=outcome.evidence.output_truncated,
        evidence_preview=outcome.evidence.preview,
        retryable=retryable,
        retry_disposition=retry_disposition,
        agent_disposition=agent_disposition,
        diagnostic_json=diagnostic,
    )


def outcome_time() -> datetime:
    return datetime.now(UTC)


def _snapshot(
    *, trigger: str, source: str, code: str = CONTRACT, unknown_write: bool = False
) -> TerminationSnapshot:
    return TerminationSnapshot(
        status="NO_PROGRESS",
        failure_state=FailureState(
            last_failure=FailureEvidence(
                tool="draft_schedule",
                tool_call_id=trigger,
                code=code,
                message="failed",
                failure_layer="TOOL",
                status="FAILED",
            ),
            no_progress=True,
            no_progress_trigger_call_id=trigger,
            no_progress_source_call_id=source,
            unknown_write_targets=frozenset({"platform:a"}) if unknown_write else frozenset(),
        ),
    )


async def _facts(snapshot: TerminationSnapshot):
    async with get_session_factory()() as session:
        return await RunService._finalization_facts(
            object.__new__(RunService),
            session,
            tenant_id=TENANT,
            run_id=RUN_ID,
            error_code="RUN_NO_PROGRESS",
            snapshot=snapshot,
        )


async def _rows() -> list[ToolCallAudit]:
    async with get_session_factory()() as session:
        return list(
            (
                await session.scalars(
                    select(ToolCallAudit)
                    .where(ToolCallAudit.tenant_id == TENANT)
                    .order_by(ToolCallAudit.tool_call_id)
                )
            ).all()
        )


def _report(rows: list[ToolCallAudit]) -> dict[str, Any]:
    record = {
        "id": str(RUN_ID),
        "agent_id": str(uuid.uuid4()),
        "user_id": str(USER_ID),
        "status": "FAILED",
        "error_code": "RUN_NO_PROGRESS",
        "trace_id": "trace-flow",
        "start_time": "2026-11-28T00:00:00+00:00",
        "end_time": "2026-11-28T00:00:10+00:00",
    }
    payloads = []
    for row in rows:
        payloads.append(
            {
                "id": str(row.id),
                "create_time": row.start_time.isoformat(),
                "tool_call_id": row.tool_call_id,
                "tool_name": row.tool_name,
                "tool_kind": row.tool_kind,
                "status": row.status,
                "error_code": row.error_code,
                "business_status": row.business_status,
                "failure_layer": row.failure_layer,
                "exit_code": row.exit_code,
                "output_truncated": row.output_truncated,
                "evidence_preview": row.evidence_preview,
                "retryable": row.retryable,
                "retry_disposition": row.retry_disposition,
                "agent_disposition": row.agent_disposition,
                "diagnostic_json": row.diagnostic_json,
                "latency_ms": row.latency_ms,
            }
        )
    bundle = ExecutionExportBundle(
        kind="RUN",
        record=record,
        sections={
            "runtime.canonical_event": ExportSection(status="ok", rows=()),
            "runtime.tool_call_audit": ExportSection(status="ok", rows=tuple(payloads)),
            "runtime.model_invocation_audit": ExportSection(status="ok", rows=()),
            "runtime.egress_audit": ExportSection(status="ok", rows=()),
            "runtime.tool_operation": ExportSection(status="ok", rows=()),
            "task.task_execution": ExportSection(status="ok", rows=()),
        },
        truncation={},
        database_snapshot={"snapshot_time": "2026-11-28T00:00:10+00:00"},
    )
    return build_report(bundle, build_diagnosis(bundle))


@pytest.mark.parametrize("reason", ["CONTRACT_MISSING", "CONTRACT_INVALID"])
async def test_contract_reasons_flow_to_tool_audit_finalization_and_export(reason: str) -> None:
    execution, requests = await _tool_result(
        {
            "code": CONTRACT,
            "msg": ZH_CONTRACT,
            "data": {
                "field": "payload.skill_id",
                "reason": reason,
                "repair": "REIMPORT_SKILL",
            },
        }
    )
    content = json.loads(execution.result.content)["error"]
    assert content["detail"]["reason"] == reason
    assert content["detail"]["repair"] == "REIMPORT_SKILL"
    assert content["action_owner"] == "ADMIN"
    assert requests and requests[-1].method == "POST"

    await _record_audit(tool_call_id="call-1", outcome=execution.result.outcome)
    snapshot = _snapshot(trigger="call-1", source="call-1")
    facts = await _facts(snapshot)
    body = render_finalization(facts, run_id=str(RUN_ID))

    assert facts.business_failure_evidence.decisive_failure is not None
    assert facts.business_failure_evidence.decisive_failure.error_code == CONTRACT
    assert "管理员重新导入" in body
    assert "CONTRACT_MISSING" not in body.partition("技术信息")[0]
    if reason == "CONTRACT_MISSING":
        assert "技能缺少定时执行配置" in body
    else:
        assert "技能的定时执行配置无效" in body

    rows = await _rows()
    assert len(rows) == 1
    assert rows[0].retryable is False
    assert rows[0].diagnostic_json["detail"]["reason"] == reason
    report = _report(rows)
    exported = report["tool_calls"][0]
    assert exported["retryable"] is False
    assert exported["diagnostic"]["detail"]["repair"] == "REIMPORT_SKILL"


async def test_guard_overlay_does_not_replace_the_original_business_source() -> None:
    business, _ = await _tool_result(
        {
            "code": CONTRACT,
            "msg": ZH_CONTRACT,
            "data": {
                "field": "payload.skill_id",
                "reason": "CONTRACT_MISSING",
                "repair": "REIMPORT_SKILL",
            },
        },
        call_id="call-original",
    )
    guard_error = ToolError(
        "TOOL_RETRY_UNSAFE",
        "The previous operation cannot safely be run again.",
        detail={"gate_reason": "BLOCKED_ATTEMPT", "source_tool_call_id": "call-original"},
        retryable=False,
        diagnostic={
            "gate_reason": "BLOCKED_ATTEMPT",
            "trigger_tool_call_id": "call-guard",
            "source_tool_call_id": "call-original",
        },
    )
    guard = guard_error.to_result().outcome

    await _record_audit(tool_call_id="call-original", outcome=business.result.outcome)
    await _record_audit(tool_call_id="call-guard", outcome=guard)
    snapshot = _snapshot(trigger="call-guard", source="call-original")
    facts = await _facts(snapshot)
    body = render_finalization(facts)

    evidence = facts.business_failure_evidence
    assert evidence.last_failure is not None
    assert evidence.last_failure.tool_call_id == "call-guard"
    assert evidence.decisive_failure is not None
    assert evidence.decisive_failure.tool_call_id == "call-original"
    assert "技能缺少定时执行配置" in body
    assert "当前操作不能原样重做" not in body
    details = "\n".join(
        render_technical_details(facts, locale="zh-CN", run_id=str(RUN_ID))
    )
    assert "来源调用：call-original" in details


async def test_only_code_failure_is_closed_out_deterministically() -> None:
    execution, _ = await _tool_result({"code": CONTRACT, "msg": ZH_CONTRACT})
    content = json.loads(execution.result.content)["error"]
    assert "detail" not in content

    await _record_audit(tool_call_id="call-1", outcome=execution.result.outcome)
    facts = await _facts(_snapshot(trigger="call-1", source="call-1"))

    body = render_finalization(facts)
    assert "具体配置原因尚未确定" in body
    assert "技能缺少定时执行配置" not in body
    assert CONTRACT not in body.partition("技术信息")[0]


async def test_unknown_write_outcome_is_not_reported_as_safe() -> None:
    execution, _ = await _tool_result(None)
    outcome = execution.result.outcome
    assert outcome.control.outcome_known is False
    await _record_audit(tool_call_id="call-1", outcome=outcome)

    snapshot = _snapshot(trigger="call-1", source="call-1", unknown_write=True)
    facts = await _facts(snapshot)
    body = render_finalization(facts)

    assert facts.has_unverified_write is True
    assert "尚未确认" in body
    assert "已有任务不受影响" not in body
    rows = await _rows()
    assert rows[0].retryable is None
    assert rows[0].retry_disposition == "BLOCK"
    report = _report(rows)
    assert report["tool_calls"][0]["retryable"] is None


async def test_successful_worker_call_has_no_failure_diagnostic() -> None:
    client = WorkerTaskClient(
        "http://worker",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json={"data": {"status": "ok"}})
        ),
    )
    try:
        assert await client._request(
            "POST", "/internal/schedule-drafts", tenant_id=TENANT
        ) == {"status": "ok"}
    finally:
        await client.aclose()

    facts = build_finalization_facts(error_code=None, snapshot=None, tool_calls=[])
    assert facts.business_failure_evidence.last_failure is None
    assert facts.has_unverified_write is None
