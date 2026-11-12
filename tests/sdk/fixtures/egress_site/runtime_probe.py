"""Runtime executor with real PG, Redis, HTTP and Linux skill process tree."""

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


class Provider:
    def __init__(self, origin, marker):
        self.origin, self.marker, self.calls = origin, marker, 0
        self.messages = []

    async def complete(self, request):
        from muad_agent_core.model import ModelResponse, ModelToolCall

        self.calls += 1
        self.messages.append(request.messages)
        if self.calls <= 2:
            return ModelResponse(
                content="",
                finish_reason="tool_calls",
                tool_calls=(
                    ModelToolCall(
                        id=f"call-{self.calls}",
                        name="execute_skill",
                        arguments={
                            "skill_key": "probe",
                            "input": {
                                "origin": self.origin,
                                "marker": self.marker,
                                "phase": self.calls,
                            },
                        },
                    ),
                ),
            )
        return ModelResponse(content="partial results retained", finish_reason="stop")


async def run(parent, core, worker):
    from muad_agent_core.agent import AgentPolicy, AgentRunner
    from muad_agent_runtime.application.async_tools.continuation_service import ExecutionIdentity
    from muad_agent_runtime.application.egress_execution import run_egress
    from muad_agent_runtime.application.executor import (
        AgentRunnerExecutor,
        ExecutionDefaults,
        ExecutorRequest,
        ExecutorRunContext,
    )
    from muad_agent_runtime.application.skill_tools import build_skill_registry
    from muad_agent_runtime.infrastructure.models.runtime import Conversation, RunRecord
    from muad_artifact_store import NfsArtifactStore, SkillArtifactCache
    from muad_common import SharedSettings
    from muad_contracts import ResolvedAgent, ResolvedModel, ResolvedSkill
    from muad_contracts.egress_budget import resolve_egress_budget
    from muad_contracts.shared_egress import SharedEgressPolicy
    from muad_platform_sdk.shared_redis import SharedRedisMeter

    settings = SharedSettings()
    engine = create_async_engine(settings.require_database_url())
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    tenant, domain = "runtime-probe-" + uuid.uuid4().hex, "runtime-probe-" + uuid.uuid4().hex
    run_id, conv_id, user_id, agent_id = (uuid.uuid4() for _ in range(4))
    workspace = parent / "workspace"
    workspace.mkdir()
    network = core["Network"](workspace)
    marker = "runtime-grandchild-" + uuid.uuid4().hex
    meter = SharedRedisMeter(redis, domain=domain, epoch="epoch")
    settings = settings.model_copy(update=worker["settings_overrides"](domain))
    import muad_agent_runtime.application.egress_execution as egress_module

    egress_module.process_settings = lambda: settings
    artifacts = parent / "artifacts"
    artifacts.mkdir()
    archive = artifacts / "probe.zip"
    script = (
        "import json,sys,requests\npayload=json.load(sys.stdin)\n"
        'if payload["phase"]==1:\n requests.get(payload["origin"]+"/ok"); '
        'requests.get(payload["origin"]+"/ok"); print(json.dumps({"completed":2})); sys.exit(0)\n'
    )
    # The second branch consumes the remaining execution budget and deliberately ignores SDK errors.
    script += core["STOP_BUSINESS"].replace("payload = json.load(sys.stdin)", "")
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("SKILL.md", "---\nname: probe\ndescription: probe\n---\n")
        bundle.writestr("scripts/main.py", script)
    skill = ResolvedSkill(
        skill_id=uuid.uuid4(),
        artifact_id=uuid.uuid4(),
        key="probe",
        name="probe",
        description="probe",
        version="1",
        checksum="sha256:" + hashlib.sha256(archive.read_bytes()).hexdigest(),
        storage_key="probe.zip",
        source="ADMIN",
    )

    async def cancelled():
        return False

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
                            "http_match": {
                                "origin": network.origin,
                                "method": "GET",
                                "path_template": "/{request}",
                            },
                        }
                    ],
                }
            )
        )
        async with sessions.begin() as session:
            session.add(
                Conversation(
                    id=conv_id,
                    tenant_id=tenant,
                    user_id=user_id,
                    agent_id=agent_id,
                    status="ACTIVE",
                    last_seq=0,
                )
            )
            await session.flush()
            session.add(
                RunRecord(
                    id=run_id,
                    tenant_id=tenant,
                    conversation_id=conv_id,
                    user_id=user_id,
                    agent_id=agent_id,
                    status="RUNNING",
                    input_text="probe",
                    trace_id=uuid.uuid4().hex,
                    execution_epoch=1,
                    lease_owner="runtime-probe",
                    lease_until=datetime.now(UTC) + timedelta(minutes=5),
                    deadline_at=datetime.now(UTC) + timedelta(minutes=10),
                )
            )
        request = ExecutorRequest(
            agent=ResolvedAgent(id=agent_id, key="probe", revision=1, instructions="probe"),
            model=ResolvedModel(id=uuid.uuid4(), revision=1, model_id="probe", base_url="http://model"),
            input_text="probe",
            is_cancel_requested=cancelled,
            skills=(skill,),
            run_context=ExecutorRunContext(tenant, run_id, conv_id, user_id),
            execution_identity=ExecutionIdentity(tenant, run_id, "runtime-probe", 1),
            execution=ExecutionDefaults(
                egress_budget=resolve_egress_budget({"execution": {"max_requests": 3}})
            ),
        )
        provider = Provider(network.origin, marker)
        async with run_egress(request) as root:
            registry = build_skill_registry(
                cache=SkillArtifactCache(NfsArtifactStore(artifacts), parent / "cache"),
                skills=(skill,),
                policy=AgentPolicy(deadline_ms=30000),
                workspace_dir=workspace,
                execution_id=str(run_id),
                egress=root,
            )
            executor = AgentRunnerExecutor(
                runner=AgentRunner(provider=provider, registry=registry), request=request, egress=root
            )
            events = [event async for event in executor.run()]
        tool_results = [
            str(message.content)
            for batch in provider.messages
            for message in batch
            if message.role.value == "tool"
        ]
        assert any("completed" in result for result in tool_results), tool_results
        assert any("LOCAL_BUDGET_EXHAUSTED" in result for result in tool_results), tool_results
        assert any(
            event.type == "runner.outcome" and event.data["final_text"] == "partial results retained"
            for event in events
        )
        assert len(network.calls) == 3, network.calls
        assert not core["active_processes"](marker)
        assert await asyncio.to_thread(network.closed.wait, 3)
        return {"status": "passed", "platform_requests": 3, "partial_results": True, "tree_reclaimed": True}
    finally:
        network.close()
        async with sessions.begin() as session:
            for table in (
                "runtime.egress_invocation",
                "runtime.egress_execution",
                "runtime.run_record",
                "runtime.conversation",
            ):
                await session.execute(
                    sa.text(f"DELETE FROM {table} WHERE tenant_id=:tenant"), {"tenant": tenant}
                )
        keys = [key async for key in redis.scan_iter(f"fx:egress:{{{domain}}}:*")]
        if keys:
            await redis.delete(*keys)
        await redis.aclose()
        await engine.dispose()


if __name__ == "__main__":
    core = runpy.run_path("/opt/egress-core-probe.py", run_name="core_fixture")
    worker = runpy.run_path("/opt/egress-worker-probe.py", run_name="worker_fixture")
    with tempfile.TemporaryDirectory(prefix="runtime-egress-") as directory:
        parent = Path(directory)
        os.environ.update(
            {
                "ARTIFACT_ROOT": str(parent / "artifacts"),
                "SKILL_CACHE_ROOT": str(parent / "cache"),
                "WORKSPACE_ROOT": str(parent / "workspaces"),
            }
        )
        print(json.dumps(asyncio.run(run(parent, core, worker))))
