"""Real PostgreSQL rows and HTTP servers shared by async-tool acceptance tests."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from muad_agent_runtime.infrastructure.models.async_tools import ToolOperation
from muad_agent_runtime.infrastructure.models.runtime import Conversation, RunRecord
from muad_agent_worker.infrastructure.models.task import TaskExecution
from muad_contracts import CompletionMode, OperationStatus, RunStatus

HASH = "sha256:" + "a" * 64


async def seed_operation(
    factory, *, mode=CompletionMode.JOIN, task_status="QUEUED", tenant: str | None = None
):
    """种一条 operation+task；`tenant` 缺省随机（隔离库里的独立种子），
    验收套件应传自己的租户，否则这些行不在任何收尾清理范围内、会卡 0019 全局 drain 前置。"""
    tenant, actor, run_id, operation_id, task_id = tenant or str(uuid4()), uuid4(), uuid4(), uuid4(), uuid4()
    now = datetime.now(UTC)
    async with factory() as session, session.begin():
        conversation = Conversation(tenant_id=tenant, user_id=actor, agent_id=uuid4())
        session.add(conversation)
        await session.flush()
        run = RunRecord(
            id=run_id,
            tenant_id=tenant,
            conversation_id=conversation.id,
            user_id=actor,
            agent_id=conversation.agent_id,
            status=RunStatus.RUNNING,
            input_text="async",
            trace_id="async-test",
            deadline_at=now + timedelta(hours=1),
        )
        session.add(run)
        await session.flush()
        operation = ToolOperation(
            id=operation_id,
            tenant_id=tenant,
            run_id=run_id,
            actor_user_id=actor,
            source_tool_call_id="call-1",
            completion_mode=mode,
            status=OperationStatus.SUBMIT_PENDING,
            task_snapshot_hash=HASH,
            input_hash=HASH,
            submission_json={},
        )
        task = TaskExecution(
            id=task_id,
            tenant_id=tenant,
            source_run_id=run_id,
            source_operation_id=operation_id,
            source_tool_call_id="call-1",
            completion_mode=mode,
            agent_id=run.agent_id,
            actor_user_id=actor,
            intent_key="async",
            skill_id=uuid4(),
            skill_artifact_id=uuid4(),
            trigger_type="IMMEDIATE",
            execution_mode="ASYNC",
            task_type="SKILL",
            status=task_status,
            input_json={},
            execution_snapshot_schema_version=1,
            execution_snapshot_json={},
            snapshot_hash=HASH,
            idempotency_key=str(task_id),
            priority=100,
            attempt=1,
            max_attempts=1,
            not_before=now,
            deadline_at=now + timedelta(hours=1),
            delivery_mode="NONE",
            delivery_status="NONE",
            delivery_key=f"task:{task_id}:final",
            delivery_attempts=0,
            lease_owner="worker-a",
            lease_until=now + timedelta(seconds=60),
        )
        session.add_all([operation, task])
    return task, operation, run
