"""Worker root key propagation; capture executor, no PG recovery claim."""
import json
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from muad_agent_core.skill import SkillExecutionResult, SkillExecutionStatus
from muad_agent_worker.infrastructure.models.task import TaskExecution
from muad_agent_worker.worker.executor import SkillTaskExecutor
from muad_contracts.shared_egress import TrustedInvocation


class Resolver:
    def __init__(self, ready: Path):
        self.ready = ready

    async def ensure(self, **kwargs):
        return self.ready


class CaptureExecutor:
    def __init__(self):
        self.calls = 0

    async def execute(self, request):
        self.calls += 1
        assert request.platform_context.budget_scope is None
        return SkillExecutionResult(status=SkillExecutionStatus.SUCCEEDED, result={})


async def test_worker_reexecution_uses_same_logical_call_key_independent_of_attempt(tmp_path, monkeypatch):
    import muad_agent_worker.worker.egress_execution as module
    keys = []
    class Host:
        async def host_invocation(self, *, operation_key, skill_key):
            keys.append(operation_key)
            return TrustedInvocation("execution", "invocation", "segment")
        def broker(self, *args, **kwargs):
            return None  # Capture-only executor starts no child and opens no transport.
        async def run(self, operation):
            return await operation()
    @asynccontextmanager
    async def configured(**kwargs):
        yield Host()
    monkeypatch.setattr(module, "configured_host_egress", configured)
    capture = CaptureExecutor()
    executor = SkillTaskExecutor(Resolver(tmp_path), executor=capture)
    task = TaskExecution(id=uuid.uuid4(), tenant_id="tenant", actor_user_id=uuid.uuid4(), attempt=1,
                         lease_epoch=1, deadline_at=datetime.now(UTC) + timedelta(minutes=5),
                         input_json={}, execution_snapshot_json={"budget": {
                             "execution_timeout_sec": 30,
                             "egress_budget": {"skill_defaults": {"total": {"max_requests": 1}}},
                         }})
    skill = {"artifact_id": str(uuid.uuid4()), "key": "skill", "storage_key": "skill.zip",
             "checksum": "sha256:test"}
    await executor._run_skill(task, skill, "ADMIN", tmp_path)
    task.attempt, task.lease_epoch = 2, 7
    await executor._run_skill(task, skill, "ADMIN", tmp_path)
    assert capture.calls == 2
    assert keys[0] == keys[1]
    assert json.loads(keys[0]) == ["TASK_SKILL", str(task.id)]
