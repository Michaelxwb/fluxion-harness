"""Actual Worker claim/reclaim and sandbox sends preserve a frozen budget."""

import asyncio
import hashlib
import json
import os
import runpy
import tempfile
import uuid
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import sqlalchemy as sa
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

SCRIPT = """import json, requests, sys
from pathlib import Path
payload = json.load(sys.stdin)
marker = Path('/workspace/first-done')
count = 21 if marker.exists() else 30
for _ in range(count):
    requests.get(payload['origin'] + '/ok')
marker.write_text('done')
print(json.dumps({'count': count}))
"""


async def probe(parent, worker, core):
    from muad_agent_core.sandbox import workspace_layout_from_settings
    from muad_agent_worker.infrastructure.models.task import TaskExecution
    from muad_agent_worker.worker.claimer import TaskClaimer
    from muad_agent_worker.worker.executor import TaskExecutionError
    from muad_agent_worker.worker.service import WorkerLoop
    from muad_common import SharedSettings
    from muad_contracts.shared_egress import SharedEgressPolicy
    from muad_platform_sdk.shared_redis import SharedRedisMeter

    settings = SharedSettings()
    engine = create_async_engine(settings.require_database_url())
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    task_id, artifact_id, user_id, agent_id = (uuid.uuid4() for _ in range(4))
    tenant, domain = "recover-" + uuid.uuid4().hex, "recover-" + uuid.uuid4().hex
    workspace = workspace_layout_from_settings(settings).workspace_dir(
        tenant_id=tenant, user_id=str(user_id), agent_id=str(agent_id)
    )
    workspace.mkdir(parents=True)
    network = core["Network"](workspace)
    artifacts = parent / "artifacts"
    artifacts.mkdir()
    archive = artifacts / "probe.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("SKILL.md", "---\nname: probe\ndescription: probe\n---\n")
        bundle.writestr("scripts/main.py", SCRIPT)
    checksum = "sha256:" + hashlib.sha256(archive.read_bytes()).hexdigest()
    frozen = worker["snapshot"](task_id, tenant, user_id, agent_id, artifact_id, checksum)
    frozen["budget"]["egress_budget"] = {
        "execution": {"max_requests": 50},
        "skill_defaults": {"total": {"max_requests": 50}},
    }
    (parent / "egress.json").write_text(json.dumps(worker["configuration"](domain, network.origin)))
    meter = SharedRedisMeter(redis, domain=domain, epoch="epoch")
    loop = WorkerLoop(sessions, settings=settings, instance_id="worker-a")
    claimer = TaskClaimer(sessions, settings)
    try:
        await redis.hset(meter._key("guard"), mapping={"epoch": "epoch"})
        await meter.publish(
            SharedEgressPolicy.model_validate(
                {
                    "revision": 1,
                    "rules": [
                        {
                            "id": "probe",
                            "target_instance_id": "platform",
                            "route_id": "GET /probe",
                            "max_requests": 100,
                            "max_inflight": 4,
                        }
                    ],
                }
            )
        )
        await worker["seed"](
            sessions, task_id, tenant, user_id, agent_id, artifact_id, frozen, network.origin, "unused"
        )
        first = await claimer.claim_one("worker-a")
        executor = loop._resolve_executor()
        assert executor._execution_timeout(first) == 12
        await executor.execute(first)
        assert len(network.calls) == 30, network.calls
        async with sessions.begin() as session:
            ids = (
                await session.execute(
                    sa.text(
                        "SELECT e.id, i.id, e.current_fence FROM runtime.egress_execution e "
                        "JOIN runtime.egress_invocation i ON i.execution_id=e.id WHERE e.tenant_id=:tenant"
                    ),
                    {"tenant": tenant},
                )
            ).one()
            await session.execute(
                sa.update(TaskExecution)
                .where(TaskExecution.id == task_id)
                .values(
                    status="WAITING",
                    lease_owner=None,
                    lease_until=None,
                    not_before=datetime.now(UTC) - timedelta(seconds=1),
                )
            )
        second = await claimer.claim_one("worker-b")
        assert second.lease_epoch > first.lease_epoch and second.attempt == first.attempt
        try:
            await executor.execute(second)
        except TaskExecutionError as exc:
            assert exc.code == "LOCAL_BUDGET_EXHAUSTED" and not exc.retryable, (exc.code, exc)
        else:
            raise AssertionError("reclaimed execution exceeded its budget")
        assert len(network.calls) == 50, len(network.calls)
        async with sessions() as session:
            current = (
                await session.execute(
                    sa.text(
                        "SELECT e.id, i.id, e.current_fence FROM runtime.egress_execution e "
                        "JOIN runtime.egress_invocation i ON i.execution_id=e.id WHERE e.tenant_id=:tenant"
                    ),
                    {"tenant": tenant},
                )
            ).one()
        assert current[:2] == ids[:2] and current[2] > ids[2]
        return {"status": "passed", "first_requests": 30, "after_reclaim": 20, "frozen_timeout": 12}
    finally:
        await loop.aclose()
        network.close()
        await worker["cleanup"](sessions, redis, domain, tenant)
        await redis.aclose()
        await engine.dispose()


if __name__ == "__main__":
    core = runpy.run_path("/opt/egress-core-probe.py", run_name="core_fixture")
    worker = runpy.run_path("/opt/egress-worker-probe.py", run_name="worker_fixture")
    with tempfile.TemporaryDirectory(prefix="worker-recovery-") as directory:
        parent = Path(directory)
        os.environ.update(
            {
                "ARTIFACT_ROOT": str(parent / "artifacts"),
                "SKILL_CACHE_ROOT": str(parent / "cache"),
                "WORKSPACE_ROOT": str(parent / "workspaces"),
                "EGRESS_CONTROL_FILE": str(parent / "egress.json"),
            }
        )
        print(json.dumps(asyncio.run(probe(parent, worker, core))))
