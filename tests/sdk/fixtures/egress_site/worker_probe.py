"""Real WorkerLoop -> SkillTaskExecutor -> bwrap -> IPC/Redis stop probe."""
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


def snapshot(task_id, tenant, user_id, agent_id, artifact_id, checksum):
    return {"schema_version": 1, "skills": [{
        "skill_id": str(task_id), "artifact_id": str(artifact_id), "key": "probe",
        "storage_key": "probe.zip", "checksum": checksum, "source": "ADMIN",
        "execution_mode": "ASYNC", "platforms": [],
        "workspace": {"tenant_id": tenant, "user_id": str(user_id), "agent_id": str(agent_id)},
    }], "budget": {"execution_timeout_sec": 12, "retry_safety": "IDEMPOTENT", "egress_budget": {
        "execution": {"max_requests": 100}, "skill_defaults": {"total": {"max_requests": 3}},
    }}}


def configuration(domain, origin):
    return {"domain": domain, "epoch": "epoch", "public_rules_enabled": True,
            "request_timeout_seconds": 30, "cleanup_seconds": 3, "renewal_interval_seconds": .1,
            "source_refresh_seconds": .5, "stop_margin_seconds": 12,
            "routes": [{"origin": origin, "method": "GET", "path_template": path,
                        "target_instance_id": "platform", "route_id": "GET /probe"}
                       for path in ("/slow", "/ok")]}


async def seed(sessions, task_id, tenant, user_id, agent_id, artifact_id, frozen, origin, marker):
    from muad_agent_worker.infrastructure.models.task import TaskExecution
    from muad_contracts import ToolRetrySafety, task_retry_safety
    from muad_contracts.snapshot import snapshot_hash
    assert task_retry_safety(frozen) == ToolRetrySafety.IDEMPOTENT
    async with sessions.begin() as session:
        session.add(TaskExecution(
            id=task_id, tenant_id=tenant, agent_id=agent_id, actor_user_id=user_id,
            intent_key="probe", skill_id=task_id, skill_artifact_id=artifact_id,
            trigger_type="IMMEDIATE", execution_mode="ASYNC", task_type="SKILL", status="QUEUED",
            input_json={"origin": origin, "marker": marker}, execution_snapshot_json=frozen,
            snapshot_hash=snapshot_hash(frozen), idempotency_key=str(task_id),
            deadline_at=datetime.now(UTC) + timedelta(minutes=5),
            delivery_mode="NONE", delivery_status="NONE",
            delivery_key=f"task:{task_id}:final",
        ))


async def cleanup(sessions, redis, domain, tenant):
    async with sessions.begin() as session:
        await session.execute(sa.text("DELETE FROM task.workspace_lease WHERE workspace_id IN "
                                      "(SELECT id FROM task.workspace WHERE tenant_id=:tenant)"),
                              {"tenant": tenant})
        for table in ("runtime.egress_invocation", "runtime.egress_execution", "task.task_event",
                      "task.workspace", "task.task_execution"):
            await session.execute(sa.text(f"DELETE FROM {table} WHERE tenant_id=:tenant"), {"tenant": tenant})
    keys = [key async for key in redis.scan_iter(f"fx:egress:{{{domain}}}:*")]
    if keys:
        await redis.delete(*keys)


async def run_probe(parent, core):
    from muad_agent_core.sandbox import workspace_layout_from_settings
    from muad_agent_worker.worker.service import WorkerLoop
    from muad_common import SharedSettings
    from muad_contracts.shared_egress import SharedEgressPolicy
    from muad_platform_sdk.shared_redis import SharedRedisMeter
    settings = SharedSettings()
    engine = create_async_engine(settings.require_database_url())
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    task_id, artifact_id, user_id, agent_id = (uuid.uuid4() for _ in range(4))
    tenant, domain = "worker-probe-" + uuid.uuid4().hex, "worker-probe-" + uuid.uuid4().hex
    workspace = workspace_layout_from_settings(settings).workspace_dir(
        tenant_id=tenant, user_id=str(user_id), agent_id=str(agent_id),
    )
    workspace.mkdir(parents=True)
    network, marker = core["Network"](workspace), "worker-grandchild-" + uuid.uuid4().hex
    frozen = prepare(parent, core, task_id, artifact_id, tenant, user_id, agent_id, domain, network.origin)
    meter = SharedRedisMeter(redis, domain=domain, epoch="epoch")
    loop = WorkerLoop(sessions, settings=settings, instance_id="probe-worker")
    try:
        await redis.hset(meter._key("guard"), mapping={"epoch": "epoch"})
        await meter.publish(SharedEgressPolicy.model_validate({"revision": 1, "rules": [{
            "id": "probe", "target_instance_id": "platform", "route_id": "GET /probe",
            "max_requests": 100, "max_inflight": 4,
        }]}))
        await seed(sessions, task_id, tenant, user_id, agent_id, artifact_id, frozen, network.origin, marker)
        assert await asyncio.wait_for(loop.run_once(), 45) == task_id
        await verify(sessions, loop, task_id, network, marker, core)
        assert await redis.zcard(meter._bucket("probe", "leases")) == 1
        return {"status": "passed", "platform_requests": 3, "task_retry": False,
                "grandchild_reclaimed": True, "slow_http_closed": True}
    finally:
        await loop.aclose()
        network.close()
        await cleanup(sessions, redis, domain, tenant)
        await redis.aclose()
        await engine.dispose()


def prepare(parent, core, task_id, artifact_id, tenant, user_id, agent_id, domain, origin):
    artifacts = parent / "artifacts"
    artifacts.mkdir(exist_ok=True)
    archive = artifacts / "probe.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("SKILL.md", "---\nname: probe\ndescription: probe\n---\n")
        bundle.writestr("scripts/main.py", core["STOP_BUSINESS"])
    checksum = "sha256:" + hashlib.sha256(archive.read_bytes()).hexdigest()
    (parent / "egress.json").write_text(json.dumps(configuration(domain, origin)))
    return snapshot(task_id, tenant, user_id, agent_id, artifact_id, checksum)


async def verify(sessions, loop, task_id, network, marker, core):
    from muad_agent_worker.infrastructure.models.task import TaskExecution
    async with sessions() as session:
        task = await session.get(TaskExecution, task_id)
        assert len(network.calls) == 3, (network.calls, task.status, task.error_code, task.error_message)
        assert task.status == "FAILED" and task.error_code == "LOCAL_BUDGET_EXHAUSTED", (
            task.status, task.error_code,
        )
        assert task.attempt == 1
    assert await loop.run_once() is None, "deterministic budget exhaustion was retried"
    assert not core["active_processes"](marker), "sandbox grandchild survived"
    assert await asyncio.to_thread(network.closed.wait, 3), "slow platform connection survived"


if __name__ == "__main__":
    core = runpy.run_path("/opt/egress-core-probe.py", run_name="egress_core_fixture")
    with tempfile.TemporaryDirectory(prefix="worker-egress-") as directory:
        parent = Path(directory)
        os.environ.update({"ARTIFACT_ROOT": str(parent / "artifacts"),
                           "SKILL_CACHE_ROOT": str(parent / "cache"),
                           "WORKSPACE_ROOT": str(parent / "workspaces"),
                           "EGRESS_CONTROL_FILE": str(parent / "egress.json")})
        print(json.dumps(asyncio.run(run_probe(parent, core))))
