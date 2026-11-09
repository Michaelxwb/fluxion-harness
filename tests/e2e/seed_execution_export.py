"""S-05 browser seed: one failed Run + TOOL audit + failed Task in real PostgreSQL."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from muad_agent_runtime.infrastructure.models.runtime import RunRecord
from muad_agent_worker.infrastructure.models.task import TaskEvent
from muad_console_platform.application.auth_service import hash_password
from muad_console_platform.infrastructure.db import get_session_factory
from muad_console_platform.infrastructure.models.auth import ROLE_ADMIN, ConsoleAccount
from muad_console_platform.infrastructure.models.control import (
    AgentDefinition,
    ModelDefinition,
    PlatformUser,
)
from muad_contracts import RunStatus
from sqlalchemy import text

from tests.e2e.seed_run_observability import _conversation, _event, _task, _tool_audit

TENANT = os.environ.get("E2E_EXECUTION_EXPORT_TENANT", "exec-export-browser")
USERNAME = os.environ.get("E2E_EXECUTION_EXPORT_USERNAME", "exec-export-admin")
PASSWORD = os.environ.get("E2E_EXECUTION_EXPORT_PASSWORD", "exec-export-password-111")
STATE_FILE = os.environ.get("E2E_EXECUTION_EXPORT_STATE_FILE", "")
DIAGNOSTIC_MARKER = "EXPORT-DIAGNOSTIC-MARKER"
SECRET_MARKER = "EXPORT-SECRET-MARKER"

_CLEANUP = (
    "DELETE FROM task.task_event WHERE tenant_id = :t",
    "DELETE FROM task.task_execution WHERE tenant_id = :t",
    "DELETE FROM runtime.tool_call_audit WHERE tenant_id = :t",
    "DELETE FROM runtime.canonical_event WHERE tenant_id = :t",
    "DELETE FROM runtime.runtime_snapshot WHERE tenant_id = :t",
    "DELETE FROM runtime.run_record WHERE tenant_id = :t",
    "DELETE FROM runtime.conversation WHERE tenant_id = :t",
    "DELETE FROM control.console_session WHERE account_id IN "
    "(SELECT id FROM control.console_account WHERE tenant_id = :t)",
    "DELETE FROM control.console_account WHERE tenant_id = :t",
    "DELETE FROM control.agent_definition WHERE tenant_id = :t",
    "DELETE FROM control.model_definition WHERE tenant_id = :t",
    "DELETE FROM control.platform_user WHERE tenant_id = :t",
)


async def _cleanup() -> None:
    async with get_session_factory()() as session:
        for statement in _CLEANUP:
            await session.execute(text(statement), {"t": TENANT})
        await session.commit()


async def _create(out: str) -> None:
    await _cleanup()
    now = datetime.now(UTC)
    async with get_session_factory()() as session:
        async with session.begin():
            model = ModelDefinition(
                tenant_id=TENANT,
                key=f"export-model-{uuid.uuid4().hex[:8]}",
                name="Export Model",
                model_id="gpt-4o-mini",
                provider_key="openai",
                params_json={"temperature": 0.0},
                enabled=True,
            )
            session.add(model)
            await session.flush()
            agent = AgentDefinition(
                tenant_id=TENANT,
                key=f"export-agent-{uuid.uuid4().hex[:8]}",
                name="Export Agent",
                description="",
                instructions="",
                model_id=model.id,
                runtime_config={},
                revision=1,
                enabled=True,
            )
            user = PlatformUser(
                tenant_id=TENANT,
                user_code=f"export-user-{uuid.uuid4().hex[:8]}",
                display_name="Export User",
                status="ACTIVE",
            )
            session.add_all([agent, user])
            await session.flush()
            account = ConsoleAccount(
                tenant_id=TENANT,
                username=USERNAME,
                display_name="Export Admin",
                password_hash=hash_password(PASSWORD),
                role=ROLE_ADMIN,
            )
            session.add(account)
            conversation = _conversation(
                tenant=TENANT, user_id=user.id, agent_id=agent.id, title="export run"
            )
            session.add(conversation)
            await session.flush()

            run_id = uuid.uuid4()
            run = RunRecord(
                id=run_id,
                tenant_id=TENANT,
                conversation_id=conversation.id,
                user_id=user.id,
                agent_id=agent.id,
                status=RunStatus.FAILED,
                input_text=f"export e2e input {DIAGNOSTIC_MARKER}",
                trace_id=f"trace-export-{uuid.uuid4().hex[:12]}",
                start_time=now - timedelta(minutes=2),
                end_time=now,
                error_code="RUN_NO_PROGRESS",
                error_message=f"export e2e failure {DIAGNOSTIC_MARKER}",
            )
            session.add(run)
            await session.flush()

            task = _task(
                tenant=TENANT,
                agent_id=agent.id,
                actor_id=user.id,
                intent="export_e2e_task",
                status="FAILED",
                source_run_id=run_id,
                error_code="SKILL_EXECUTION_FAILED",
                finished=True,
            )
            session.add(task)
            await session.flush()
            session.add(
                TaskEvent(
                    tenant_id=TENANT,
                    task_id=task.id,
                    seq=1,
                    event_type="FAILED",
                    payload_json={
                        "error_code": "SKILL_EXECUTION_FAILED",
                        "error_message": f"task failure {DIAGNOSTIC_MARKER}",
                    },
                    trace_id=run.trace_id,
                )
            )

            session.add(
                _event(
                    tenant=TENANT,
                    conversation_id=conversation.id,
                    run_id=run_id,
                    seq=1,
                    event_type="RUN_CREATED",
                    stream_type="run.created",
                    create_time=now - timedelta(minutes=2),
                    payload={"status": "CREATED"},
                )
            )
            session.add(
                _event(
                    tenant=TENANT,
                    conversation_id=conversation.id,
                    run_id=run_id,
                    seq=2,
                    event_type="TOOL_CALL",
                    stream_type="tool.completed",
                    create_time=now - timedelta(minutes=1),
                    payload={
                        "status": "ERROR",
                        "error_code": "SKILL_EXECUTION_FAILED",
                        "tool_call_id": "call-export-1",
                        "task_id": str(task.id),
                        "text": f"tool failure {DIAGNOSTIC_MARKER}",
                        "api_key": SECRET_MARKER,
                    },
                )
            )
            audit = _tool_audit(
                tenant=TENANT,
                run_id=run_id,
                conversation_id=conversation.id,
                call_id="call-export-1",
                tool_name="export_tool",
                start_time=now - timedelta(minutes=1),
            )
            audit.task_id = task.id
            audit.status = "ERROR"
            audit.error_code = "SKILL_EXECUTION_FAILED"
            audit.error_message = f"tool audit failure {DIAGNOSTIC_MARKER}"
            audit.args_preview_json = {"api_key": SECRET_MARKER, "query": DIAGNOSTIC_MARKER}
            session.add(audit)
            await session.flush()
            state = {
                "tenant": TENANT,
                "account": {"username": USERNAME, "password": PASSWORD},
                "runId": str(run_id),
                "auditId": str(audit.id),
                "taskId": str(task.id),
                "diagnosticMarker": DIAGNOSTIC_MARKER,
                "secretMarker": SECRET_MARKER,
            }
            Path(out).parent.mkdir(parents=True, exist_ok=True)
            Path(out).write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


async def _run(action: str, out: str) -> int:
    if action == "create":
        if not out:
            raise SystemExit("create requires --out or E2E_EXECUTION_EXPORT_STATE_FILE")
        await _create(out)
    elif action == "cleanup":
        await _cleanup()
    else:
        raise SystemExit(f"unknown action: {action}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="execution export e2e seed")
    parser.add_argument("action", choices=("create", "cleanup"))
    parser.add_argument("--out", default=STATE_FILE)
    args = parser.parse_args()
    return asyncio.run(_run(args.action, args.out))


if __name__ == "__main__":
    raise SystemExit(main())
