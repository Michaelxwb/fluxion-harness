"""Runtime assembly with real PostgreSQL, Redis and HTTP; not full S-03."""

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
    from muad_contracts.shared_egress import SharedHTTPMatch

    current = await meter.current_policy()
    await meter.publish(
        current.model_copy(
            update={
                "revision": 2,
                "rules": tuple(
                    rule.model_copy(
                        update={
                            "http_match": SharedHTTPMatch(origin=origin, method="GET", path_template="/proxy")
                        }
                    )
                    for rule in current.rules
                ),
            }
        )
    )
    settings = SharedSettings(
        egress_meter_domain=meter._prefix.split("{")[1].split("}")[0],
        egress_meter_epoch="epoch",
        egress_http_read_timeout_sec=3,
        egress_cleanup_sec=0.2,
        egress_renewal_interval_sec=0.05,
        egress_source_refresh_sec=0.1,
        egress_stop_margin_sec=12,
    )
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


async def test_console_platform_policy_activates_real_runtime_host_without_execution_cap(
    identity_environment,
    http_probe,
    tmp_path,
    monkeypatch,
):
    """Real Run claim, PG registry, Redis and HTTP; Console load is a typed policy fixture."""
    from datetime import timedelta

    import muad_platform_sdk.configured_egress as assembly
    from muad_agent_runtime.infrastructure.models.runtime import Conversation
    from muad_contracts.shared_egress import SharedEgressPolicy
    from muad_platform_sdk.console_egress import tenant_domain

    sessions, tenant, _, redis, _ = identity_environment
    probe, origin = http_probe
    conversation_id, run_id = uuid.uuid4(), uuid.uuid4()
    async with sessions.begin() as session:
        now = await session.scalar(sa.select(sa.func.statement_timestamp()))
        user, agent = uuid.uuid4(), uuid.uuid4()
        session.add(
            Conversation(
                id=conversation_id,
                tenant_id=tenant,
                user_id=user,
                agent_id=agent,
                status="ACTIVE",
                last_seq=0,
            )
        )
        await session.flush()
        session.add(
            RunRecord(
                id=run_id,
                tenant_id=tenant,
                conversation_id=conversation_id,
                user_id=user,
                agent_id=agent,
                status="RUNNING",
                input_text="test",
                trace_id=uuid.uuid4().hex,
                execution_epoch=1,
                lease_owner="runtime-owner",
                lease_until=now + timedelta(minutes=5),
                deadline_at=now + timedelta(minutes=10),
            )
        )
    domain = "runtime-console-" + uuid.uuid4().hex
    settings = SharedSettings(egress_meter_domain=domain, egress_meter_epoch="epoch")
    monkeypatch.setattr(runtime_egress, "process_settings", lambda: settings)
    monkeypatch.setattr(runtime_egress, "get_session_factory", lambda: sessions)

    async def console_policy(*args):
        return SharedEgressPolicy.model_validate(
            {
                "revision": 1,
                "rules": [
                    {
                        "id": "configured-platform",
                        "target_instance_id": "assets",
                        "route_id": "GET /proxy",
                        "max_requests": 1,
                        "http_match": {"origin": origin, "method": "GET", "path_template": "/proxy"},
                    }
                ],
            }
        )

    monkeypatch.setattr(assembly, "load_console_egress_policy", console_policy)

    async def cancelled():
        return False

    request = ExecutorRequest(
        agent=ResolvedAgent(id=agent, key="agent", revision=1, instructions="test"),
        model=ResolvedModel(id=uuid.uuid4(), revision=1, model_id="test", base_url="http://model"),
        input_text="test",
        is_cancel_requested=cancelled,
        run_context=ExecutorRunContext(tenant, run_id, conversation_id, user),
        execution_identity=ExecutionIdentity(tenant, run_id, "runtime-owner", 1),
        execution=ExecutionDefaults(egress_enabled=True),
    )
    try:
        async with runtime_egress.run_egress(request) as root:
            assert root is not None

            async def work():
                invocation = await root.host_invocation(operation_key="skill-call", skill_key="skill")
                async with httpx.AsyncClient(transport=root.transport(invocation)) as client:
                    assert (await client.get(origin + "/proxy")).status_code == 429
                    with pytest.raises(SharedLimitExceeded):
                        await client.get(origin + "/proxy")

            await root.run(work)
        assert probe.calls == 1
        await _assert_runtime_egress_audits(sessions, tenant, run_id)
    finally:
        async with sessions.begin() as session:
            await session.execute(sa.delete(RunRecord).where(RunRecord.id == run_id))
            await session.execute(sa.delete(Conversation).where(Conversation.id == conversation_id))
        prefix = "fx:egress:{" + tenant_domain(domain, tenant) + "}:"
        keys = [key async for key in redis.scan_iter(prefix + "*")]
        if keys:
            await redis.delete(*keys)


async def _assert_runtime_egress_audits(sessions, tenant, run_id):
    from muad_agent_runtime.infrastructure.models.runtime import EgressAudit

    async with sessions() as session:
        audits = (await session.scalars(sa.select(EgressAudit).where(
            EgressAudit.tenant_id == tenant, EgressAudit.run_id == run_id,
        ).order_by(EgressAudit.create_time))).all()
    assert len(audits) == 2
    assert audits[0].status_code == 429 and audits[0].policy_decision == "ALLOW"
    assert audits[0].result_status == "FAILED"
    assert audits[1].result_status == "BLOCKED" and audits[1].error_code == "LIMIT_TOTAL"
