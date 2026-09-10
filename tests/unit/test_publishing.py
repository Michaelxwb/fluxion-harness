from uuid import uuid4

import pytest

from framework.agent_core.resolver import resolve_agent
from framework.contracts.context import TrustedExecutionContext
from framework.contracts.resource_scope import ValidatedResourceScope
from framework.domain.publish import build_service_release
from framework.execution.snapshot import build_execution_snapshot
from framework.web.errors import AppError


def _context() -> TrustedExecutionContext:
    return TrustedExecutionContext(
        actor_user_id=uuid4(),
        tenant_id="t1",
        effective_capability_set={"b", "a"},
    )


def _scope() -> ValidatedResourceScope:
    return ValidatedResourceScope(scope_type="demo.scope", schema_hash="h" * 64, value={"wid": "w1"})


def test_build_service_release_freezes_agent_and_scope() -> None:
    service_id = uuid4()
    first = build_service_release(
        service_id=service_id,
        service_key="demo",
        draft={"name": "Demo", "goal": "g"},
        agent_snapshot={"revision": 3},
        resource_scope_type="demo.scope",
        resource_scope_schema_hash="h" * 64,
    )
    second = build_service_release(
        service_id=service_id,
        service_key="demo",
        draft={"name": "Demo", "goal": "g"},
        agent_snapshot={"revision": 3},
        resource_scope_type="demo.scope",
        resource_scope_schema_hash="h" * 64,
    )
    assert first.content_hash == second.content_hash
    assert first.release_id == second.release_id
    assert first.frozen_payload["agent_snapshot"] == {"revision": 3}
    assert first.frozen_payload["resource_scope_schema_hash"] == "h" * 64


def test_build_service_release_rejects_incomplete_draft() -> None:
    with pytest.raises(AppError) as exc_info:
        build_service_release(service_id=uuid4(), service_key="demo", draft={"name": "Demo"})
    assert exc_info.value.code == "SERVICE_DRAFT_INVALID"


def test_resolve_agent_returns_domain_object() -> None:
    agent_id = uuid4()
    agent = resolve_agent(
        agent_id,
        {"name": "n", "instructions": "i", "revision": 5, "enabled": True},
    )
    assert agent.id == agent_id
    assert agent.revision == 5
    assert agent.enabled is True


def test_resolve_agent_rejects_missing_or_broken_config() -> None:
    with pytest.raises(AppError) as exc_info:
        resolve_agent(uuid4(), None)
    assert exc_info.value.code == "AGENT_NOT_FOUND"
    with pytest.raises(AppError) as exc_info:
        resolve_agent(uuid4(), {"name": "n"})
    assert exc_info.value.code == "AGENT_CONFIGURATION_INVALID"


def test_build_execution_snapshot_freezes_scope_not_security() -> None:
    snapshot = build_execution_snapshot(
        service_release_ref="svc:r-abc",
        service_content_hash="c" * 64,
        execution_spec={"resource_scope_type": "demo.scope"},
        validated_scope=_scope(),
        context=_context(),
        agent_revision=7,
    )
    assert snapshot.resource_scope_type == "demo.scope"
    assert snapshot.resource_scope_schema_hash == "h" * 64
    assert snapshot.capability_contracts == ["a", "b"]
    assert snapshot.agent_revision == 7
    dumped = snapshot.model_dump(mode="json")
    assert "credential" not in dumped and "Credential" not in str(dumped.values())
