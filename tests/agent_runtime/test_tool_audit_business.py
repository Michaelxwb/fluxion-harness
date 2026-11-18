"""`tool_call_audit` 业务执行字段（TASK-008 / design §4.7）。"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from muad_agent_runtime.infrastructure.audit_writer import RuntimeAuditWriter
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import ToolCallAudit
from sqlalchemy import select

TENANT = f"audit-business-{uuid.uuid4()}"
RUN_ID = uuid.uuid4()
CONV_ID = uuid.uuid4()
USER_ID = uuid.uuid4()


def _writer() -> RuntimeAuditWriter:
    return RuntimeAuditWriter(
        tenant_id=TENANT,
        run_id=RUN_ID,
        task_id=None,
        conversation_id=CONV_ID,
        user_id=USER_ID,
        session_factory=get_session_factory,
    )


@pytest.fixture(autouse=True)
async def _cleanup() -> AsyncIterator[None]:
    yield
    async with get_session_factory()() as session:
        await session.execute(
            ToolCallAudit.__table__.delete().where(ToolCallAudit.tenant_id == TENANT)
        )
        await session.commit()


async def _record(**fields) -> None:
    now = datetime.now(UTC)
    await _writer().record_tool_call(
        tool_call_id=f"call-{uuid.uuid4().hex[:8]}",
        tool_name="execute",
        tool_kind="SKILL",
        prepared_args_hash="sha256:" + "0" * 64,
        args_preview_json={},
        status="OK",
        start_time=now,
        end_time=now,
        latency_ms=12,
        **fields,
    )


async def _row() -> ToolCallAudit:
    async with get_session_factory()() as session:
        row = await session.scalar(
            select(ToolCallAudit).where(ToolCallAudit.tenant_id == TENANT)
        )
    assert row is not None
    return row


async def test_business_fields_round_trip() -> None:
    await _record(
        business_status="FAILED",
        failure_layer="CONTENT",
        exit_code=3,
        output_truncated=True,
        evidence_preview="mssw session rejected: HTTP 401",
        error_code="PLATFORM_AUTH_REJECTED",
    )

    row = await _row()
    assert row.status == "OK", "transport 状态保持 OK"
    assert row.business_status == "FAILED"
    assert row.failure_layer == "CONTENT"
    assert row.exit_code == 3
    assert row.output_truncated is True
    assert row.evidence_preview == "mssw session rejected: HTTP 401"
    assert row.error_code == "PLATFORM_AUTH_REJECTED"


async def test_missing_business_fields_are_unknown_not_success() -> None:
    await _record()

    row = await _row()
    assert row.business_status == "UNKNOWN"
    assert row.failure_layer is None
    assert row.exit_code is None
    assert row.output_truncated is None
    assert row.evidence_preview is None


async def test_evidence_preview_is_redacted_before_write() -> None:
    await _record(
        business_status="FAILED",
        failure_layer="CONTENT",
        evidence_preview="request failed token=super-secret-value",
    )

    row = await _row()
    assert row.evidence_preview is not None
    assert "super-secret-value" not in row.evidence_preview
    assert "***" in row.evidence_preview
