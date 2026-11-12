"""PG + Redis + HTTP hosted assembly; not the app executor acceptance S-03."""
import importlib
from dataclasses import replace

import httpx
import pytest
import sqlalchemy as sa
from muad_agent_runtime.application.egress_source_lease import read_run_source_lease
from muad_agent_runtime.infrastructure.models.runtime import RunRecord
from muad_contracts.shared_egress import FrozenIdentityBudgets, SharedEgressPolicy
from muad_platform_sdk.egress_ipc import EgressTarget
from muad_platform_sdk.shared_http_transport import RequestLifetime
from muad_platform_sdk.shared_redis import SharedLimitExceeded

from tests.sdk.test_egress_identity_registry import identity_environment as identity_environment_fixture
from tests.sdk.test_egress_source_authority import run_source as run_source_fixture
from tests.sdk.test_shared_http_transport import http_probe as http_probe_fixture

identity_environment = identity_environment_fixture
run_source = run_source_fixture
http_probe = http_probe_fixture
pytestmark = pytest.mark.integration


def settings(base):
    module = importlib.import_module("muad_platform_sdk.host_egress_execution")
    return module.HostEgressSettings(
        domain=base._prefix.split("{")[1].split("}")[0], epoch=base.epoch,
        public_rules_enabled=False, lifetime=RequestLifetime(.5, .1, .05),
        source_refresh_seconds=.1, stop_margin_seconds=.5,
    )


@pytest.mark.parametrize("run_source", [{"execution": {"max_requests": 5},
                                       "invocations": {"skill": {"max_requests": 3}}}], indirect=True)
async def test_hosted_recovery_keeps_scope_ids_and_remaining_budget(
    run_source, identity_environment, http_probe,
):
    module = importlib.import_module("muad_platform_sdk.host_egress_execution")
    sessions, _, scope, claim = run_source
    _, _, base, redis, _ = identity_environment
    await base.publish(SharedEgressPolicy(revision=2))
    probe, origin = http_probe
    budget = FrozenIdentityBudgets(execution={"max_requests": 5}, invocations={"skill": {"max_requests": 3}})
    async def open_root(source):
        return await module.HostEgressExecution.create(
            settings=settings(base), budget=budget, sessions=sessions, redis=redis, claim=source,
            reader=read_run_source_lease, resolve=lambda request: EgressTarget("platform", "GET /assets"),
        )
    first = await open_root(claim)
    first_ids = []
    async def initial():
        invocation = await first.host_invocation(operation_key="fixed-call", skill_key="skill")
        first_ids.append(invocation)
        async with httpx.AsyncClient(transport=first.transport(invocation)) as client:
            for _ in range(2):
                assert (await client.get(origin + "/proxy")).status_code == 429
    await first.run(initial)
    async with sessions.begin() as session:
        await session.execute(sa.update(RunRecord).where(RunRecord.id == claim.source_id).values(
            lease_owner="replacement", execution_epoch=2,
        ))
    second = await open_root(replace(claim, owner="replacement", epoch=2))
    async def recovered():
        invocation = await second.host_invocation(operation_key="fixed-call", skill_key="skill")
        assert invocation.execution_id == first_ids[0].execution_id == str(scope.id)
        assert invocation.invocation_id == first_ids[0].invocation_id
        assert invocation.segment_id != first_ids[0].segment_id
        async with httpx.AsyncClient(transport=second.transport(invocation)) as client:
            assert (await client.get(origin + "/proxy")).status_code == 429
            with pytest.raises(SharedLimitExceeded) as error:
                await client.get(origin + "/proxy")
            assert error.value.scope == "invocation"
    await second.run(recovered)
    assert probe.calls == 3


async def test_empty_hosted_assembly_performs_no_storage_calls():
    module = importlib.import_module("muad_platform_sdk.host_egress_execution")
    configuration = module.HostEgressSettings(
        domain="unused", epoch="epoch", public_rules_enabled=False,
        lifetime=RequestLifetime(1, .1, .05), source_refresh_seconds=.1, stop_margin_seconds=.5,
    )
    assert await module.HostEgressExecution.create(
        settings=configuration, budget=FrozenIdentityBudgets(), sessions=None, redis=None,
        claim=None, reader=None, resolve=None,
    ) is None
