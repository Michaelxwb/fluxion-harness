"""B-01: quote provenance never grants user confirmation."""

from importlib import import_module
from uuid import uuid4

import pytest
from pydantic import ValidationError

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(("message", "quote", "valid"), [
    ("记住我喜欢蓝色", "记住我喜欢蓝色", True),
    ("不要记住我喜欢蓝色", "记住我喜欢蓝色", False),
    ("记住我喜欢蓝色吗？", "记住我喜欢蓝色", False),
    ("如果记住我喜欢蓝色就好了", "记住我喜欢蓝色", False),
    ("我说蓝色", "蓝色", False),
    ("甲\r\n乙", "甲\n乙", True),
    ("甲\r乙", "甲\n乙", True),
    (" 甲", "甲", False),
    ("", "", False),
    ("甲" * 513, "甲" * 513, False),
])
def test_b01_memory_redesign_quote_is_complete_provenance(message, quote, valid):
    evidence = import_module("muad_agent_core.memory.evidence")
    assert evidence.verify_quote(message, quote) is valid


@pytest.mark.parametrize("field", [
    "author", "explicit_request", "tenant_id", "user_id", "source_event_id",
    "source_scope_revision", "version", "confirmation_event_id",
])
def test_b01_model_cannot_grant_confirmation_or_trusted_identity(field):
    contracts = import_module("muad_contracts.memory")
    payload = {"memory_key": "preference.color", "value": "记住", "category": "PREFERENCE"}
    payload[field] = "USER"
    with pytest.raises(ValidationError):
        contracts.RememberMemoryInput.model_validate(payload)


def test_b01_exact_quote_does_not_add_author_or_explicit_request():
    contracts = import_module("muad_contracts.memory")
    candidate = contracts.RememberMemoryInput(
        memory_key="color", value="记住", category="PREFERENCE", source_quote="记住"
    )
    assert candidate.model_dump() == {
        "memory_key": "color", "value": "记住", "category": "PREFERENCE", "source_quote": "记住"
    }


@pytest.mark.parametrize("payload", [
    {"memory_key": "task.fake", "value": "value", "category": "PREFERENCE"},
    {"memory_key": "color", "value": 123, "category": "PREFERENCE"},
    {"memory_key": "color", "value": "x" * 513, "category": "PREFERENCE"},
    {"memory_key": "color", "value": "x", "category": "TASK_STATE"},
    {"memory_key": "color", "value": "x", "category": "UNKNOWN"},
])
def test_remember_contract_rejects_invalid_shape(payload):
    contracts = import_module("muad_contracts.memory")
    with pytest.raises(ValidationError):
        contracts.RememberMemoryInput.model_validate(payload)


def _task_payload():
    root = uuid4()
    return {
        "title": "季度报告", "goal": "完成报告", "constraints": [], "open_items": ["补数据"],
        "next_action": "确认数据", "root_conversation_id": root, "source_conversation_id": root,
    }


@pytest.mark.parametrize(("field", "value"), [
    ("title", "x" * 81), ("goal", "x" * 257), ("next_action", "x" * 257),
    ("constraints", ["x"] * 6), ("open_items", ["x" * 129]), ("authority", "granted"),
])
def test_task_state_validates_every_field(field, value):
    contracts = import_module("muad_contracts.memory")
    payload = _task_payload()
    payload[field] = value
    with pytest.raises(ValidationError):
        contracts.TaskStateContent.model_validate(payload)


def test_task_state_enforces_canonical_bytes_and_root_namespace():
    contracts = import_module("muad_contracts.memory")
    payload = _task_payload()
    state = contracts.TaskStateContent.model_validate(payload)
    assert contracts.task_memory_key(state.root_conversation_id) == f"task.{state.root_conversation_id.hex}"
    payload.update(goal="甲" * 256, next_action="乙" * 256, constraints=["丙" * 128] * 5)
    with pytest.raises(ValidationError, match="2048"):
        contracts.TaskStateContent.model_validate(payload)


def test_confirmation_hash_reuses_canonical_json_and_rejects_non_json():
    contracts = import_module("muad_contracts.memory")
    from muad_contracts.canonical import NonCanonicalJsonError

    assert contracts.memory_hash({"a": "中文", "b": 1}) == contracts.memory_hash({"b": 1, "a": "中文"})
    assert contracts.memory_hash({"a": "中文"}) != contracts.memory_hash({"a": "中"})
    with pytest.raises(NonCanonicalJsonError):
        contracts.memory_hash({"a": float("nan")})


@pytest.mark.parametrize("key", ["Upper", "a_b", "a:b", "a\n", "x" * 65])
def test_memory_key_preserves_existing_strict_namespace(key):
    contracts = import_module("muad_contracts.memory")
    with pytest.raises(ValidationError):
        contracts.RememberMemoryInput(memory_key=key, value="x", category="EXPLICIT")


@pytest.mark.parametrize(("field", "value"), [
    ("candidate_hash", "sha256:" + "0" * 64),
    ("forget_epoch", True), ("expected_version", True), ("expected_version", 0),
    ("expires_at", "2026-10-12T10:00:00"),
])
def test_confirmation_proposal_binds_content_and_strict_versions(field, value):
    contracts = import_module("muad_contracts.memory")
    candidate = {"memory_key": "color", "value": "蓝色", "category": "PREFERENCE", "source_quote": None}
    payload = {
        "tenant_id": "t", "user_id": uuid4(), "run_id": uuid4(), "submission_id": uuid4(),
        "candidate": candidate, "candidate_hash": contracts.memory_hash(candidate), "expected_version": None,
        "forget_epoch": 0, "evidence": {
            "source_event_id": uuid4(), "source_seq": 1, "source_scope_revision": 0,
        }, "expires_at": "2026-10-12T10:00:00Z",
    }
    assert contracts.MemoryConfirmationProposal.model_validate(payload).candidate.value == "蓝色"
    payload[field] = value
    with pytest.raises(ValidationError):
        contracts.MemoryConfirmationProposal.model_validate(payload)


def test_task_candidate_requires_root_key_and_matching_category():
    contracts = import_module("muad_contracts.memory")
    state = contracts.TaskStateContent.model_validate(_task_payload())
    key = contracts.task_memory_key(state.root_conversation_id)
    assert contracts.MemoryCandidate(memory_key=key, category="TASK_STATE", content=state).content == state
    with pytest.raises(ValidationError):
        contracts.MemoryCandidate(memory_key="task." + uuid4().hex, category="TASK_STATE", content=state)
    with pytest.raises(ValidationError):
        contracts.MemoryCandidate(memory_key="ordinary", category="EXPLICIT", content=state)
    with pytest.raises(ValidationError):
        contracts.MemoryCandidate(memory_key=key, category="TASK_STATE", content={"value": "x"})
