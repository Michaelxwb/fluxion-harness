"""Memory requirements must be enforceable before implementation starts."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".code-flow/scripts"))

from cf_spec_context import load_context  # noqa: E402
from cf_spec_gate import validate_plan_coverage  # noqa: E402
from cf_spec_metadata import SpecMetadataError, load_spec_metadata  # noqa: E402
from cf_spec_resolver import resolve_candidates  # noqa: E402

TASK_DIR = ROOT / ".code-flow/tasks/2026-10-12/memory-redesign"
ARCHIVED_TASK_DIR = ROOT / ".code-flow/tasks/archived/2026-10-12/memory-redesign"
# 需求归档后仍以归档件为事实源校验（与 tests/async_tool_runtime_inventory.py 同惯例）。
TASK_DIR = TASK_DIR if TASK_DIR.exists() else ARCHIVED_TASK_DIR
SPEC = ROOT / ".code-flow/specs/memory/harness-memory.md"
RULES = {f"RULE-memory-{number:03d}" for number in range(1, 7)}
pytestmark = pytest.mark.contract


def test_memory_required_rules_have_executable_review_verifiers():
    assert SPEC.is_file(), "six required memory rules have no Tier-1 carrier"
    spec = load_spec_metadata(str(SPEC))
    assert spec.id == "harness-memory"
    assert {rule.ref for rule in spec.rules if rule.enforcement == "required"} == RULES
    assert {verifier.rule for verifier in spec.verifiers} == RULES
    for verifier in spec.verifiers:
        assert verifier.stage == "review"
        assert verifier.type == "command"
        assert verifier.config["argv"][:4] == ["uv", "run", "pytest", "-q"]
        assert verifier.config["cwd"] == "."


@pytest.mark.parametrize("path", [
    "packages/contracts/src/muad_contracts/memory.py",
    "packages/agent-core/src/muad_agent_core/memory/evidence.py",
    "apps/agent-runtime/src/muad_agent_runtime/application/memory/queries.py",
    "apps/agent-runtime/src/muad_agent_runtime/application/memory_service.py",
    "tests/agent_runtime/test_memory_redesign_cache.py",
    "migrations/versions/0020_memory_redesign.py",
])
def test_memory_path_mapping_matches_only_memory_carriers(path):
    matched = {candidate.spec_id for candidate in resolve_candidates(str(ROOT), "code", [path])
               if candidate.scope == "path"}
    assert "harness-memory" in matched
    unrelated = resolve_candidates(
        str(ROOT), "code", ["apps/agent-runtime/src/muad_agent_runtime/api/health.py"]
    )
    assert all(candidate.scope == "unmatched" for candidate in unrelated
               if candidate.spec_id == "harness-memory")


def test_memory_duplicate_rule_metadata_is_rejected(tmp_path):
    assert SPEC.is_file(), "memory metadata has not been created"
    invalid = tmp_path / "duplicate.md"
    invalid.write_text(SPEC.read_text().replace("RULE-memory-002", "RULE-memory-001"))
    with pytest.raises(SpecMetadataError):
        load_spec_metadata(str(invalid))


def test_memory_rule_has_exactly_one_owner_and_duplicate_is_blocked(tmp_path):
    context = load_context(str(TASK_DIR / "spec-context.yml"))
    binding = next((item for item in context.bindings if item.spec_id == "harness-memory"), None)
    assert binding is not None, "memory rules have not been bound to this requirement"
    assert {rule.ref for rule in binding.rules} == RULES
    plan = TASK_DIR / "memory-redesign.md"
    assert validate_plan_coverage(context, str(plan)).decision == "pass"
    invalid = tmp_path / "duplicate-owner.md"
    invalid.write_text(plan.read_text() + "\n- harness-memory#RULE-memory-001\n")
    result = validate_plan_coverage(context, str(invalid))
    assert result.decision == "block"
    assert any(issue.rule_ref == "RULE-memory-001" for issue in result.errors)
