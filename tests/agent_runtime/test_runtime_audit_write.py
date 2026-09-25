"""[S-02 / RULE-08] 运行审计三表写入：同一 Run 的 tool/egress/model 审计同源、无 Secret、状态列可归一。

不得 Mock 的真实边界：真实 tool/egress/model 审计写入 port → 真实 PostgreSQL
（`runtime.tool_call_audit` / `runtime.egress_audit` / `runtime.model_invocation_audit` 逐行回读）。
验收类不制造 RED；真实缺口如实记录。
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
from muad_agent_runtime.infrastructure.audit_writer import RuntimeAuditWriter
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import (
    EgressAudit,
    ModelInvocationAudit,
    ToolCallAudit,
)
from sqlalchemy import text

TENANT = f"audit-write-{uuid.uuid4()}"
RUN_ID = uuid.uuid4()
CONV_ID = uuid.uuid4()
USER_ID = uuid.uuid4()
SECRET = "sk-live-verysecret-1234567890"

# RULE-08：各表用于归一出 result_status 的列与词表（投影层按此映射）
TOOL_STATUSES = {"OK", "ERROR", "SUCCESS", "FAILED", "PREPARED", "RUNNING", "DENIED"}
EGRESS_RESULT_STATUSES = {"OK", "FAILED", "DENIED", "TIMEOUT", "ERROR"}
MODEL_STATUSES = {"SUCCEEDED", "FAILED", "RETRY"}


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
        for model in (ToolCallAudit, EgressAudit, ModelInvocationAudit):
            await session.execute(
                model.__table__.delete().where(model.tenant_id == TENANT)
            )
        await session.commit()


async def _record_bundle(*, with_secret: bool) -> None:
    """同一 Run 上写出 tool/egress/model 三类审计（真实写入 port，各自独立短事务）。"""
    writer = _writer()
    now = datetime.now(UTC)
    await writer.record_tool_call(
        tool_call_id=f"call-{uuid.uuid4().hex[:8]}",
        tool_name="http.request",
        tool_kind="SKILL",
        prepared_args_hash="hash-1",
        args_preview_json={
            "url": "https://api.example.com/v1/items",
            "api_key": SECRET if with_secret else "<redacted>",
        },
        status="OK",
        start_time=now,
        end_time=now,
        latency_ms=12,
    )
    await writer.record_egress(
        target_type="PLATFORM_SERVICE",
        target="https://api.example.com/v1/items",
        policy_decision="ALLOW",
        operation="POST /v1/items",
        method="POST",
        status_code=200,
        result_status="OK",
        latency_ms=20,
    )
    await writer.record_model_invocation(
        provider="openai",
        model="gpt-4o-mini",
        attempt=1,
        retry_reason=None,
        input_tokens=128,
        output_tokens=64,
        latency_ms=350,
        status="SUCCEEDED",
    )


async def _rows(model: Any) -> list[Any]:
    async with get_session_factory()() as session:
        return list(
            (await session.execute(model.__table__.select().where(model.tenant_id == TENANT))).all()
        )


async def test_s02_tool_egress_model_audits_share_one_run() -> None:
    """S-02：一次执行链路的 tool/egress/model 审计同源（同一 Run）且状态列可归一出 result_status。"""
    await _record_bundle(with_secret=False)

    tool_rows, egress_rows, model_rows = (
        await _rows(ToolCallAudit),
        await _rows(EgressAudit),
        await _rows(ModelInvocationAudit),
    )
    assert len(tool_rows) == 1 and len(egress_rows) == 1 and len(model_rows) == 1, (
        len(tool_rows),
        len(egress_rows),
        len(model_rows),
    )
    # 同源锚点：三表都挂在同一个 Run（trace 由 Run 承载）
    for row in (*tool_rows, *egress_rows, *model_rows):
        assert str(row.run_id) == str(RUN_ID)
        assert row.tenant_id == TENANT
    # RULE-08：投影读取的状态列存在且取值在各自词表内
    tool_row = tool_rows[0]._mapping
    egress_row = egress_rows[0]._mapping
    model_row = model_rows[0]._mapping
    assert tool_row["status"] in TOOL_STATUSES, tool_row["status"]
    assert egress_row["result_status"] in EGRESS_RESULT_STATUSES, egress_row["result_status"]
    assert model_row["status"] in MODEL_STATUSES, model_row["status"]


async def test_s02_audit_payloads_carry_no_secret() -> None:
    """S-02：审计写入不得落 Secret 明文（`args_preview_json` 为脱敏参数预览）。"""
    await _record_bundle(with_secret=True)

    tool_rows = await _rows(ToolCallAudit)
    assert tool_rows, "未写入 tool_call_audit"
    preview = tool_rows[0]._mapping["args_preview_json"]
    serialized = json.dumps(preview, ensure_ascii=False)
    assert SECRET not in serialized, (
        f"args_preview_json 落入了 Secret 明文：{serialized}"
    )

    async with get_session_factory()() as session:
        leaked = await session.scalar(
            text(
                "SELECT count(*) FROM runtime.tool_call_audit "
                "WHERE tenant_id = :t AND args_preview_json::text LIKE :pattern"
            ),
            {"t": TENANT, "pattern": f"%{SECRET}%"},
        )
    assert int(leaked or 0) == 0, "tool_call_audit 中可查询到 Secret 明文"
