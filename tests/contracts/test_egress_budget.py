"""Executable B-04 config contract; no controller or network mock."""
import pytest
from pydantic import ValidationError


def test_defaults_overrides_and_parent_budget():
    from muad_contracts.egress_budget import resolve_egress_budget

    source = {
        "execution": {"max_requests": 200},
        "skill_defaults": {"total": {"max_requests": 100, "max_inflight": 2}},
        "skills": {"assets": {"total": {"max_requests": 50, "max_inflight": None}}},
    }
    effective = resolve_egress_budget(source)
    assert effective.execution.max_requests == 200
    assert effective.for_skill("assets").total.max_requests == 50
    assert effective.for_skill("assets").total.max_inflight is None
    assert effective.for_skill("other").total.max_requests == 100
    source["execution"]["max_requests"] = 1
    assert effective.execution.max_requests == 200


@pytest.mark.parametrize("value", [True, 0, -1, 1.5, "3"])
def test_invalid_counts_are_not_unlimited(value):
    from muad_contracts.egress_budget import resolve_egress_budget
    with pytest.raises(ValidationError):
        resolve_egress_budget({"execution": {"max_requests": value}})


@pytest.mark.parametrize("limits", [
    {"window_requests": 3}, {"window_seconds": 2},
    {"window_requests": 3, "window_seconds": float("nan")},
    {"window_requests": 3, "window_seconds": float("inf")},
    {"window_requests": 3, "window_seconds": True},
    {"unknown": 1},
])
def test_invalid_windows_and_unknown_fields(limits):
    from muad_contracts.egress_budget import resolve_egress_budget
    with pytest.raises(ValidationError):
        resolve_egress_budget({"execution": limits})


def test_empty_policy_is_bypass():
    from muad_contracts.egress_budget import resolve_egress_budget
    assert resolve_egress_budget({}).is_unlimited
    assert resolve_egress_budget({"execution": {"max_requests": None}}).is_unlimited


def test_target_override_replaces_by_id():
    from muad_contracts.egress_budget import resolve_egress_budget
    match = {"platform_key": "mssp", "method": "GET", "path_template": "/assets/{id}"}
    result = resolve_egress_budget({
        "skill_defaults": {"targets": [{"id": "assets", "match": match, "limits": {"max_requests": 10}}]},
        "skills": {"assets": {"targets": [{"id": "assets", "match": match, "limits": {"max_requests": 5}}]}},
    })
    assert len(result.for_skill("assets").targets) == 1
    assert result.for_skill("assets").targets[0].limits.max_requests == 5
    assert result.for_skill("other").targets[0].limits.max_requests == 10


def test_partial_window_override_inherits_other_half():
    from muad_contracts.egress_budget import resolve_egress_budget
    effective = resolve_egress_budget({
        "skill_defaults": {"total": {"window_requests": 10, "window_seconds": 60}},
        "skills": {"assets": {"total": {"window_requests": 5}}},
    })
    assert effective.for_skill("assets").total.window_requests == 5
    assert effective.for_skill("assets").total.window_seconds == 60


def test_snapshot_builders_freeze_normalized_policy():
    import uuid

    from muad_agent_runtime.application.run_service import build_snapshot, execution_defaults_of
    from muad_contracts import ResolvedAgent, ResolveDefinitionResponse, ResolvedModel, ResolvedSkill
    from muad_contracts.resolve import WorkspaceRef
    from muad_contracts.snapshot import build_task_snapshot

    agent = ResolvedAgent(id=uuid.uuid4(), key="agent", revision=1, instructions="test", runtime_config={
        "egress_budget": {"execution": {"max_requests": 200},
                          "skill_defaults": {"total": {"max_requests": 50}}},
    })
    model = ResolvedModel(id=uuid.uuid4(), revision=1, model_id="test", base_url="http://localhost")
    skill = ResolvedSkill(skill_id=uuid.uuid4(), artifact_id=uuid.uuid4(), key="assets", name="assets",
                          description="test", version="1", checksum="sha256:" + "a" * 64,
                          storage_key="skills/a.zip", source="ADMIN")
    resolved = ResolveDefinitionResponse(agent=agent, model=model, skills=[skill])
    workspace = WorkspaceRef(tenant_id="t", user_id=uuid.uuid4(), agent_id=agent.id)
    run = build_snapshot(run_id=uuid.uuid4(), tenant_id="t", resolved=resolved)
    task = build_task_snapshot(agent=agent, model=model, skill=skill, workspace=workspace)
    before = run.policy_json["egress_budget"]
    assert before == task["budget"]["egress_budget"]
    assert "egress_budget" not in {k: v for k, v in task.items() if k != "budget"}
    agent.runtime_config["egress_budget"]["execution"]["max_requests"] = 1
    assert execution_defaults_of(run.policy_json).egress_budget.execution.max_requests == 200
    assert task["budget"]["egress_budget"]["execution"]["max_requests"] == 200
    new_run = build_snapshot(run_id=uuid.uuid4(), tenant_id="t", resolved=resolved)
    assert new_run.policy_json["egress_budget"]["execution"]["max_requests"] == 1
    assert new_run.content_hash != run.content_hash


@pytest.mark.parametrize("match", [
    {}, {"platform_key": "p", "mcp_server_key": "m"},
    {"platform_key": "p", "tool_name": "t"},
    {"mcp_server_key": "m", "method": "GET"},
    {"platform_key": "p", "path_template": "//other/host"},
    {"platform_key": "p", "path_template": "/a/../b"},
    {"platform_key": "p", "path_template": "/a/pre{id}"},
    {"platform_key": "p", "path_template": "/a/%2f"},
])
def test_invalid_target_match_is_rejected(match):
    from muad_contracts.egress_budget import resolve_egress_budget
    with pytest.raises(ValidationError):
        resolve_egress_budget({"targets": [{"id": "rule", "match": match,
                                             "limits": {"max_requests": 1}}]})


def test_duplicate_skill_target_ids_do_not_silently_overwrite():
    from muad_contracts.egress_budget import resolve_egress_budget
    target = {"id": "same", "match": {"platform_key": "p"}, "limits": {"max_requests": 1}}
    with pytest.raises(ValidationError):
        resolve_egress_budget({"skills": {"a": {"targets": [target, target]}}})


def test_null_clears_window_as_a_pair():
    from muad_contracts.egress_budget import resolve_egress_budget
    source = {
        "skill_defaults": {"total": {"window_requests": 2, "window_seconds": 1.5}},
        "skills": {"a": {"total": {"window_requests": None, "window_seconds": None}}},
    }
    result = resolve_egress_budget(source)
    assert result.for_skill("a").total.is_unlimited
    assert result.for_skill("b").total.window_seconds == 1.5
    source["skills"]["a"]["total"] = {"window_requests": None}
    with pytest.raises(ValidationError):
        resolve_egress_budget(source)
