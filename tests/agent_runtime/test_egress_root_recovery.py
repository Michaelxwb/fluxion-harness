"""Runtime assembly with real PostgreSQL, Redis and HTTP; not full S-03."""

import json
import uuid
from dataclasses import replace

import httpx
import pytest
import sqlalchemy as sa
from muad_agent_runtime.application import egress_execution as runtime_egress
from muad_agent_runtime.application.async_tools.continuation_service import ExecutionIdentity
from muad_agent_runtime.application.executor import ExecutionDefaults, ExecutorRequest, ExecutorRunContext
from muad_agent_runtime.infrastructure.models.runtime import RunRecord
from muad_common import SharedSettings
from muad_contracts import ResolvedAgent, ResolvedModel, ResolvedSkill
from muad_contracts.egress_budget import resolve_egress_budget
from muad_platform_sdk.shared_redis import SharedLimitExceeded

from tests.sdk.test_egress_identity_registry import identity_environment as identity_environment_fixture
from tests.sdk.test_egress_source_authority import run_source as run_source_fixture
from tests.sdk.test_shared_http_transport import http_probe as http_probe_fixture

identity_environment = identity_environment_fixture
run_source = run_source_fixture
http_probe = http_probe_fixture
pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    "run_source",
    [{"execution": {"max_requests": 50}, "invocations": {"skill": {"max_requests": 50}}}],
    indirect=True,
)
async def test_runtime_root_recovery_keeps_budget(
    run_source, identity_environment, http_probe, tmp_path, monkeypatch
):
    sessions, _, scope, claim = run_source
    _, _, meter, _, _ = identity_environment
    probe, origin = http_probe
    config = {
        "domain": meter._prefix.split("{")[1].split("}")[0],
        "epoch": "epoch",
        "public_rules_enabled": True,
        "request_timeout_seconds": 3,
        "cleanup_seconds": 0.2,
        "renewal_interval_seconds": 0.05,
        "source_refresh_seconds": 0.1,
        "stop_margin_seconds": 12,
        "routes": [
            {
                "origin": origin,
                "method": "GET",
                "path_template": "/proxy",
                "target_instance_id": "platform",
                "route_id": "GET /assets",
            }
        ],
    }
    path = tmp_path / "egress.json"
    path.write_text(json.dumps(config))
    settings = SharedSettings(egress_control_file=str(path))
    monkeypatch.setattr(runtime_egress, "process_settings", lambda: settings)
    monkeypatch.setattr(runtime_egress, "get_session_factory", lambda: sessions)
    async with sessions() as session:
        row = await session.get(RunRecord, claim.source_id)
        context = ExecutorRunContext(claim.tenant_id, row.id, row.conversation_id, row.user_id)
    skill = ResolvedSkill(
        skill_id=uuid.uuid4(),
        artifact_id=uuid.uuid4(),
        key="skill",
        name="skill",
        description="test",
        version="1",
        checksum="sha256:" + "a" * 64,
        storage_key="test.zip",
        source="ADMIN",
    )

    async def cancelled():
        return False

    request = ExecutorRequest(
        agent=ResolvedAgent(id=uuid.uuid4(), key="probe", revision=1, instructions="probe"),
        model=ResolvedModel(id=uuid.uuid4(), revision=1, model_id="probe", base_url="http://model"),
        input_text="test",
        is_cancel_requested=cancelled,
        skills=(skill,),
        run_context=context,
        execution_identity=ExecutionIdentity(claim.tenant_id, claim.source_id, claim.owner, 1),
        execution=ExecutionDefaults(
            egress_budget=resolve_egress_budget(
                {
                    "execution": {"max_requests": 50},
                    "skill_defaults": {"total": {"max_requests": 50}},
                }
            )
        ),
    )
    identities = []

    async def execute(req, count, deny):
        async with runtime_egress.run_egress(req) as root:

            async def work():
                invocation = await root.host_invocation(operation_key="fixed-call", skill_key="skill")
                identities.append(invocation)
                async with httpx.AsyncClient(transport=root.transport(invocation)) as client:
                    for _ in range(count):
                        assert (await client.get(origin + "/proxy")).status_code == 429
                    if deny:
                        with pytest.raises(SharedLimitExceeded):
                            await client.get(origin + "/proxy")

            await root.run(work)

    await execute(request, 30, False)
    async with sessions.begin() as session:
        await session.execute(
            sa.update(RunRecord)
            .where(RunRecord.id == claim.source_id)
            .values(lease_owner="replacement", execution_epoch=2)
        )
    recovered = replace(
        request, execution_identity=ExecutionIdentity(claim.tenant_id, claim.source_id, "replacement", 2)
    )
    await execute(recovered, 20, True)
    assert identities[0].execution_id == identities[1].execution_id == str(scope.id)
    assert identities[0].invocation_id == identities[1].invocation_id
    assert identities[0].segment_id != identities[1].segment_id
    assert probe.calls == 50
