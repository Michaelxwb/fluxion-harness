from uuid import uuid4

import pytest

from framework.agent_core.resolver import resolve_agent
from framework.contracts.resource_scope import ValidatedResourceScope
from framework.domain.publish import build_service_release
from framework.execution.snapshot import build_execution_snapshot
from framework.web.errors import AppError

_BINDINGS = {
    "skill_bindings": ["skill-1"],
    "knowledge_bindings": ["kb-1"],
    "capability_bindings": ["cap-1", "cap-2"],
    "service_bindings": ["svc-1"],
}


def _scope() -> ValidatedResourceScope:
    return ValidatedResourceScope(scope_type="demo.scope", schema_hash="h" * 64, value={"wid": "w1"})


def test_build_service_release_freezes_agent_and_scope() -> None:
    service_id = uuid4()
    first = build_service_release(
        service_id=service_id,
        service_key="demo",
        draft={"name": "Demo", "goal": "g", "resource_scope_type": "demo.scope"},
        agent_snapshot={"revision": 3},
        resource_scope_schema_hash="h" * 64,
    )
    second = build_service_release(
        service_id=service_id,
        service_key="demo",
        draft={"name": "Demo", "goal": "g", "resource_scope_type": "demo.scope"},
        agent_snapshot={"revision": 3},
        resource_scope_schema_hash="h" * 64,
    )
    assert first.content_hash == second.content_hash
    assert first.release_id == second.release_id
    assert first.frozen_payload["agent_snapshot"] == {"revision": 3}
    # FEAT-04: the declared scope type travels in the payload, the hash is derived
    assert first.frozen_payload["resource_scope_type"] == "demo.scope"
    assert first.frozen_payload["resource_scope_schema_hash"] == "h" * 64


def test_build_service_release_rejects_incomplete_draft() -> None:
    with pytest.raises(AppError) as exc_info:
        build_service_release(service_id=uuid4(), service_key="demo", draft={"name": "Demo"})
    assert exc_info.value.code == "SERVICE_DRAFT_INVALID"


def test_resolve_agent_returns_full_aggregate() -> None:
    """P0-3: 解析结果必须是完整 Aggregate，bindings 逐项带上。"""
    agent_id = uuid4()
    agent = resolve_agent(
        agent_id,
        {"name": "n", "instructions": "i", "revision": 5, "enabled": True, **_BINDINGS},
    )
    assert agent.id == agent_id
    assert agent.revision == 5
    assert agent.enabled is True
    assert agent.skill_bindings == ["skill-1"]
    assert agent.knowledge_bindings == ["kb-1"]
    assert agent.capability_bindings == ["cap-1", "cap-2"]
    assert agent.service_bindings == ["svc-1"]


def test_resolve_agent_rejects_missing_or_broken_config() -> None:
    with pytest.raises(AppError) as exc_info:
        resolve_agent(uuid4(), None)
    assert exc_info.value.code == "AGENT_NOT_FOUND"
    with pytest.raises(AppError) as exc_info:
        resolve_agent(uuid4(), {"name": "n"})
    assert exc_info.value.code == "AGENT_CONFIGURATION_INVALID"


def test_resolve_agent_rejects_config_without_bindings() -> None:
    """P0-3: 缺 binding 集合的 config 必须拒绝，**不得静默解析成空集合**。

    静默成空会让一个丢了绑定的 Agent 看起来「没有能力」，而不是「配置不完整」。
    """
    with pytest.raises(AppError) as exc_info:
        resolve_agent(uuid4(), {"name": "n", "instructions": "i", "revision": 1})
    assert exc_info.value.code == "AGENT_CONFIGURATION_INVALID"
    assert "skill_bindings" in exc_info.value.message


def test_build_execution_snapshot_freezes_bindings_not_authorization() -> None:
    """P0-1: 快照冻结 Agent 的绑定与 scope，**绝不冻结 actor 的授权**（RULE-04）。

    授权（effective_capability_set）必须在执行/恢复时按当前状态重新解析，
    冻结它会让一个已撤销授权的 Execution 继续跑下去。签名里已无
    TrustedExecutionContext 入口——本用例守住这一点。
    """
    snapshot = build_execution_snapshot(
        service_release_ref="svc:r-abc",
        service_content_hash="c" * 64,
        execution_spec={"goal": "weekly summary"},
        validated_scope=_scope(),
        agent_revision=7,
        skill_artifacts=["skill-1@aaa"],
        knowledge_bindings=["kb-1"],
        capability_contracts=["cap-1"],
    )
    assert snapshot.resource_scope_type == "demo.scope"
    assert snapshot.resource_scope_schema_hash == "h" * 64
    assert snapshot.agent_revision == 7
    assert snapshot.skill_artifacts == ["skill-1@aaa"]
    assert snapshot.knowledge_bindings == ["kb-1"]
    assert snapshot.capability_contracts == ["cap-1"]
    dumped = str(snapshot.model_dump(mode="json"))
    assert "credential" not in dumped.lower()
    assert "effective_capability_set" not in dumped


def test_build_service_release_rejects_wrongly_typed_draft_field() -> None:
    """P1-3: 草稿字段类型错误必须在发布前拒绝，不得冻进 payload。"""
    with pytest.raises(AppError) as exc_info:
        build_service_release(
            service_id=uuid4(),
            service_key="demo",
            draft={"name": "Demo", "goal": "g", "resource_scope_type": 123},
        )
    assert exc_info.value.code == "SERVICE_DRAFT_INVALID"
    assert "resource_scope_type" in exc_info.value.message


def test_build_service_release_keeps_open_ended_draft_sections() -> None:
    """P1-3: extra="allow" —— execution_spec 等开放段不得被校验丢弃。"""
    release = build_service_release(
        service_id=uuid4(),
        service_key="demo",
        draft={"name": "Demo", "goal": "g", "execution_spec": {"steps": 3}},
    )
    frozen_draft = release.frozen_payload["draft"]
    assert isinstance(frozen_draft, dict)
    assert frozen_draft["execution_spec"] == {"steps": 3}
