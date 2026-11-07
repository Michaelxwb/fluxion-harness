""" [S-41] Skill 生命周期审计与执行/清理/配额/审批指标可查询。

真实边界：`AuditService` → 真实 PostgreSQL `control.config_audit_log`；各服务真实
`/metrics` HTTP 端点 → api-kit 进程内注册表。不 mock ORM、HTTP 或指标注册表。

审计/日志/指标都只允许记录关联字段与封闭枚举；Cookie/Token/密钥原文不得落库或出现在
指标文本里。
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from muad_agent_runtime.metrics import (
    SANDBOX_TIMEOUT_METRIC,
    SKILL_EXECUTION_METRIC,
    install_runtime_metrics,
)
from muad_agent_worker.metrics import (
    WORKSPACE_CLEANUP_METRIC,
    WORKSPACE_QUOTA_REJECTED_METRIC,
    install_worker_metrics,
    record_workspace_cleanup,
    record_workspace_quota_rejected,
)
from muad_api import inc_counter
from muad_api.context import context_scope
from muad_console_platform.application.audit_service import AuditService
from muad_console_platform.application.skill_audit import (
    SkillAuditAction,
    SkillAuditEvent,
    WorkspaceCleanupAuditEvent,
)
from muad_console_platform.infrastructure.models.control import ConfigAuditLog
from muad_console_platform.metrics import install_console_metrics, record_skill_review
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

SECRET_CANARY = "s41-cookie-token-canary-do-not-persist"

SAMPLE_PATTERN = re.compile(
    r"^(?P<name>[A-Za-z_:][A-Za-z0-9_:]*)(?P<labels>\{.*\})?\s+(?P<value>[0-9eE+\-.]+)$"
)


def _metric_value(text: str, name: str, labels: dict[str, str]) -> float | None:
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        matched = SAMPLE_PATTERN.match(line.strip())
        if matched is None or matched.group("name") != name:
            continue
        raw_labels = matched.group("labels")
        parsed: dict[str, str] = {}
        if raw_labels:
            for pair in raw_labels[1:-1].split(","):
                key, _, value = pair.partition("=")
                parsed[key.strip()] = value.strip().strip('"')
        if all(parsed.get(key) == value for key, value in labels.items()):
            return float(matched.group("value"))
    return None


async def _metrics_text(app: FastAPI) -> str:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/metrics")
    assert response.status_code == 200
    return response.text


def _app(installer: Callable[[FastAPI], None]) -> FastAPI:
    app = FastAPI()
    installer(app)
    return app


async def test_s41_skill_lifecycle_audit_rows_are_complete_and_sanitized(
    session: AsyncSession, draft_tenant
) -> None:
    """审批/提交/下载/解绑/执行/清理事件可由真实审计表回查，且不含凭据原文。"""
    tenant = draft_tenant("acc-s41")
    actor = uuid.uuid4()
    skill_id = uuid.uuid4()
    artifact_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    run_id = f"run-{uuid.uuid4()}"
    audit = AuditService(session)

    events = [
        SkillAuditEvent(
            tenant_id=tenant,
            actor_id=actor,
            action=SkillAuditAction.CREATE,
            skill_id=skill_id,
            detail={"source": "USER"},
        ),
        SkillAuditEvent(
            tenant_id=tenant,
            actor_id=actor,
            action=SkillAuditAction.SUBMIT,
            skill_id=skill_id,
            artifact_id=artifact_id,
            result="PENDING",
        ),
        SkillAuditEvent(
            tenant_id=tenant,
            actor_id=actor,
            action=SkillAuditAction.REVIEW,
            skill_id=skill_id,
            artifact_id=artifact_id,
            agent_id=agent_id,
            result="APPROVE",
        ),
        SkillAuditEvent(
            tenant_id=tenant,
            actor_id=actor,
            action=SkillAuditAction.ARTIFACT_DOWNLOAD,
            skill_id=skill_id,
            artifact_id=artifact_id,
            result="SUCCESS",
        ),
        SkillAuditEvent(
            tenant_id=tenant,
            actor_id=actor,
            action=SkillAuditAction.UNBIND,
            skill_id=skill_id,
            agent_id=agent_id,
            result="SUCCESS",
        ),
        SkillAuditEvent(
            tenant_id=tenant,
            actor_id=actor,
            action=SkillAuditAction.DELETE,
            skill_id=skill_id,
            result="DELETED",
        ),
        SkillAuditEvent(
            tenant_id=tenant,
            actor_id=actor,
            action=SkillAuditAction.EXECUTION,
            skill_id=skill_id,
            artifact_id=artifact_id,
            agent_id=agent_id,
            run_id=run_id,
            result="SUCCEEDED",
        ),
    ]

    with context_scope(tenant_id=tenant, trace_id="trace-s41", request_id="req-s41"):
        for event in events:
            await audit.record_skill_event(event)
        await audit.record_workspace_cleanup_event(
            WorkspaceCleanupAuditEvent(
                tenant_id=tenant,
                actor_id=actor,
                workspace_id=workspace_id,
                result="DELETED",
                detail={
                    "tenant_id": tenant,
                    "user_id": str(uuid.uuid4()),
                    "agent_id": str(agent_id),
                    "outputs_removed": 3,
                    # 敏感键必须在落库前被剔除（不是只靠调用方自觉）。
                    "cookie": SECRET_CANARY,
                    "authorization": f"Bearer {SECRET_CANARY}",
                },
            )
        )
    await session.commit()

    rows = list(
        (
            await session.scalars(
                sa.select(ConfigAuditLog)
                .where(ConfigAuditLog.tenant_id == tenant)
                .order_by(ConfigAuditLog.create_time)
            )
        ).all()
    )
    assert len(rows) == len(events) + 1
    assert {row.action for row in rows} == {
        "SKILL_CREATE",
        "SKILL_SUBMIT",
        "SKILL_REVIEW",
        "SKILL_ARTIFACT_DOWNLOAD",
        "SKILL_UNBIND",
        "SKILL_DELETE",
        "SKILL_EXECUTION",
        "WORKSPACE_CLEANUP",
    }
    assert {row.actor_user_id for row in rows} == {actor}
    assert all(row.trace_id == "trace-s41" for row in rows)

    payload = json.dumps(
        [
            {
                "before": row.before_json,
                "after": row.after_json,
                "source_ip": row.source_ip,
            }
            for row in rows
        ],
        ensure_ascii=False,
        default=str,
    )
    assert SECRET_CANARY not in payload, "审计不得保存 Cookie/Token/密钥原文"

    execution_row = next(row for row in rows if row.action == "SKILL_EXECUTION")
    assert execution_row.after_json == {
        "skill_id": str(skill_id),
        "artifact_id": str(artifact_id),
        "agent_id": str(agent_id),
        "run_id": run_id,
        "result": "SUCCEEDED",
    }
    cleanup_row = next(row for row in rows if row.action == "WORKSPACE_CLEANUP")
    assert cleanup_row.after_json["workspace_id"] == str(workspace_id)
    assert cleanup_row.after_json["result"] == "DELETED"
    assert cleanup_row.after_json["outputs_removed"] == 3
    assert "cookie" not in cleanup_row.after_json
    assert "authorization" not in cleanup_row.after_json

    listed, total = await audit.list_audits(tenant, page=1, page_size=100)
    assert total == len(rows)
    assert {item["action"] for item in listed} == {row.action for row in rows}


async def test_s41_metrics_endpoints_expose_execution_failure_cleanup_and_review() -> None:
    """执行/失败/超时/清理/配额/审批计数都能从各服务真实 `/metrics` 端点查询。"""
    runtime_app = _app(install_runtime_metrics)
    worker_app = _app(install_worker_metrics)
    console_app = _app(install_console_metrics)

    runtime_before = await _metrics_text(runtime_app)
    succeeded_before = _metric_value(
        runtime_before, SKILL_EXECUTION_METRIC, {"source": "USER", "status": "SUCCEEDED"}
    ) or 0.0
    failed_before = _metric_value(
        runtime_before, SKILL_EXECUTION_METRIC, {"source": "USER", "status": "FAILED"}
    ) or 0.0
    timeout_before = _metric_value(
        runtime_before, SANDBOX_TIMEOUT_METRIC, {"source": "USER", "status": "TIMED_OUT"}
    ) or 0.0

    inc_counter(SKILL_EXECUTION_METRIC, 1, {"source": "USER", "status": "SUCCEEDED"})
    inc_counter(SKILL_EXECUTION_METRIC, 1, {"source": "USER", "status": "FAILED"})
    inc_counter(SANDBOX_TIMEOUT_METRIC, 1, {"source": "USER", "status": "TIMED_OUT"})
    runtime_after = await _metrics_text(runtime_app)
    assert _metric_value(
        runtime_after, SKILL_EXECUTION_METRIC, {"source": "USER", "status": "SUCCEEDED"}
    ) == succeeded_before + 1
    assert _metric_value(
        runtime_after, SKILL_EXECUTION_METRIC, {"source": "USER", "status": "FAILED"}
    ) == failed_before + 1
    assert _metric_value(
        runtime_after, SANDBOX_TIMEOUT_METRIC, {"source": "USER", "status": "TIMED_OUT"}
    ) == timeout_before + 1

    worker_before = await _metrics_text(worker_app)
    cleanup_before = _metric_value(
        worker_before, WORKSPACE_CLEANUP_METRIC, {"status": "DELETED"}
    ) or 0.0
    failed_before = _metric_value(
        worker_before, WORKSPACE_CLEANUP_METRIC, {"status": "FAILED"}
    ) or 0.0
    quota_before = _metric_value(worker_before, WORKSPACE_QUOTA_REJECTED_METRIC, {}) or 0.0
    record_workspace_cleanup("DELETED")
    record_workspace_cleanup("FAILED")
    record_workspace_quota_rejected()
    worker_after = await _metrics_text(worker_app)
    assert _metric_value(
        worker_after, WORKSPACE_CLEANUP_METRIC, {"status": "DELETED"}
    ) == cleanup_before + 1
    assert _metric_value(
        worker_after, WORKSPACE_CLEANUP_METRIC, {"status": "FAILED"}
    ) == failed_before + 1
    assert _metric_value(
        worker_after, WORKSPACE_QUOTA_REJECTED_METRIC, {}
    ) == quota_before + 1

    console_before = await _metrics_text(console_app)
    approve_before = _metric_value(
        console_before, "skill_review_total", {"action": "APPROVE"}
    ) or 0.0
    reject_before = _metric_value(
        console_before, "skill_review_total", {"action": "REJECT"}
    ) or 0.0
    record_skill_review("APPROVE")
    record_skill_review("REJECT")
    console_after = await _metrics_text(console_app)
    assert _metric_value(
        console_after, "skill_review_total", {"action": "APPROVE"}
    ) == approve_before + 1
    assert _metric_value(
        console_after, "skill_review_total", {"action": "REJECT"}
    ) == reject_before + 1

    for metrics_text in (runtime_after, worker_after, console_after):
        assert SECRET_CANARY not in metrics_text
        assert str(uuid.UUID(int=0)) not in metrics_text
