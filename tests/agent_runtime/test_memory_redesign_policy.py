"""TASK-008 auxiliary tests; B-04/B-05/B-18 retain their real-boundary owners."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from importlib import import_module
from uuid import uuid4

import pytest
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts.memory import (
    FactContent,
    MemoryAuthor,
    MemoryCandidate,
    MemoryCategory,
    MemoryRetirementReason,
    MemorySourceEvidence,
    TaskStateContent,
    memory_content_bytes,
    task_memory_key,
)
from muad_contracts.platform_settings import MemoryPolicySettings

pytestmark = pytest.mark.unit
NOW = datetime(2026, 10, 13, tzinfo=UTC)


def policy():
    return import_module("muad_agent_runtime.application.memory_write")


def fact(key="color", **overrides):
    values = dict(
        memory_key=key,
        content=FactContent(value="blue"),
        author=MemoryAuthor.AGENT,
        category=MemoryCategory.PREFERENCE,
        version=1,
        updated_at=NOW,
        expires_at=NOW + timedelta(days=30),
    )
    return policy().MemoryWriteState(**{**values, **overrides})


@pytest.mark.parametrize("field", ["value", "key", "quote", "task"])
@pytest.mark.parametrize("secret", ["sk-" + "x" * 30, "password=fixture", "ghp_" + "z" * 30, "known-canary"])
def test_aux_content_gate_checks_all_candidate_strings_without_echoing(field, secret):
    root = uuid4()
    if field == "task":
        content = TaskStateContent(
            title="task",
            goal="goal",
            constraints=[secret],
            open_items=["x"],
            next_action="next",
            root_conversation_id=root,
            source_conversation_id=root,
        )
        candidate = MemoryCandidate(
            memory_key=task_memory_key(root), category=MemoryCategory.TASK_STATE, content=content
        )
    else:
        candidate = MemoryCandidate(
            memory_key="api-key" if field == "key" else "color",
            category=MemoryCategory.PREFERENCE,
            content=FactContent(value=secret if field == "value" else "blue"),
        )
    with pytest.raises(AppError) as exc:
        policy().validate_memory_content(
            candidate,
            source_quote=secret if field == "quote" else None,
            credential_canaries=("known-canary",),
        )
    assert exc.value.code == ErrorCode.MEMORY_FORBIDDEN_CONTENT
    assert secret not in str(exc.value)


def test_aux_content_gate_keeps_normal_task_content_and_rejects_invalid_raw_shape():
    candidate = MemoryCandidate(
        memory_key="work.style",
        category=MemoryCategory.WORK_STYLE,
        content=FactContent(value="请用中文，并注明来源"),
    )
    policy().validate_memory_content(candidate, source_quote="请用中文", credential_canaries=())
    invalid = MemoryCandidate.model_construct(
        memory_key="task.not-a-root", category=MemoryCategory.PREFERENCE, content=FactContent(value="blue")
    )
    with pytest.raises(AppError) as exc:
        policy().validate_memory_content(invalid, source_quote=None, credential_canaries=())
    assert exc.value.code == ErrorCode.MEMORY_VALUE_INVALID


@pytest.mark.parametrize("secret", ["token=fixture", "ＡＰＩ＿ＫＥＹ＝fixture", "Bearer known-canary"])
def test_aux_secret_detection_normalizes_labels_and_also_checks_quote(secret):
    candidate = MemoryCandidate(
        memory_key="color", category=MemoryCategory.PREFERENCE, content=FactContent(value="blue")
    )
    with pytest.raises(AppError) as exc:
        policy().validate_memory_content(
            candidate, source_quote=secret, credential_canaries=("known-canary",)
        )
    assert exc.value.code == ErrorCode.MEMORY_FORBIDDEN_CONTENT
    assert secret not in str(exc.value)


def test_aux_secret_gate_does_not_treat_general_security_advice_as_a_credential():
    candidate = MemoryCandidate(
        memory_key="security.advice",
        category=MemoryCategory.EXPLICIT,
        content=FactContent(value="Do not share your password with anybody."),
    )
    policy().validate_memory_content(candidate, source_quote=None, credential_canaries=())


@pytest.mark.parametrize("field", ["value", "quote", "task"])
@pytest.mark.parametrize("secret", ['{"api_key":"opaque-fixture"}', "{'password': 'fixture'}"])
def test_aux_quoted_sensitive_keys_inside_text_cannot_bypass_content_gate(field, secret):
    root = uuid4()
    content = (
        TaskStateContent(
            title="task",
            goal=secret,
            constraints=[],
            open_items=["next"],
            next_action="next",
            root_conversation_id=root,
            source_conversation_id=root,
        )
        if field == "task"
        else (FactContent(value=secret if field == "value" else "blue"))
    )
    candidate = MemoryCandidate(
        memory_key=task_memory_key(root) if field == "task" else "color",
        category=MemoryCategory.TASK_STATE if field == "task" else MemoryCategory.PREFERENCE,
        content=content,
    )
    with pytest.raises(AppError) as exc:
        policy().validate_memory_content(
            candidate, source_quote=secret if field == "quote" else None, credential_canaries=()
        )
    assert exc.value.code == ErrorCode.MEMORY_FORBIDDEN_CONTENT
    assert secret not in str(exc.value)


@pytest.mark.parametrize("author", [MemoryAuthor.USER, MemoryAuthor.ADMIN])
def test_aux_full_confirmed_quota_rejects_instead_of_evicting(author):
    with pytest.raises(AppError) as exc:
        policy().plan_memory_capacity(
            [fact(author=author)],
            [fact("new")],
            now=NOW,
            settings=MemoryPolicySettings(max_entries_per_user=1),
        )
    assert exc.value.code == ErrorCode.MEMORY_QUOTA_EXCEEDED


def test_aux_quota_cleans_expired_and_deterministically_evicts_agent_including_disabled():
    old = fact("a", confidence=1, confirmed_count=0, updated_at=NOW - timedelta(days=3), enabled=False)
    newer = fact("b", confidence=1, confirmed_count=0, updated_at=NOW - timedelta(days=2))
    protected = fact("user", author=MemoryAuthor.USER, expires_at=None)
    expired = fact("expired", expires_at=NOW)
    plan = policy().plan_memory_capacity(
        [newer, protected, old, expired],
        [fact("new")],
        now=NOW,
        settings=MemoryPolicySettings(max_entries_per_user=3),
    )
    assert plan.expired_keys == ("expired",) and plan.evicted_keys == ("a",)
    reverse = policy().plan_memory_capacity(
        [expired, old, protected, newer],
        [fact("new")],
        now=NOW,
        settings=MemoryPolicySettings(max_entries_per_user=3),
    )
    assert reverse == plan


def test_aux_eviction_order_prefers_low_confidence_then_fewer_confirmations_then_older_basis():
    current = [
        fact("a", confidence=2, confirmed_count=0),
        fact("b", confidence=1, confirmed_count=3),
        fact("c", confidence=1, confirmed_count=1),
        fact("d", confidence=1, confirmed_count=1, updated_at=NOW - timedelta(days=1)),
    ]
    plan = policy().plan_memory_capacity(
        current, [fact("new")], now=NOW, settings=MemoryPolicySettings(max_entries_per_user=4)
    )
    assert plan.evicted_keys == ("d",)


def test_aux_canonical_quote_bytes_count_and_growth_rejects_old_frozen_quota_without_evicting():
    current = [fact("a", source_quote="中文"), fact("b")]
    settings = MemoryPolicySettings(max_entries_per_user=1)
    assert policy().fact_bytes(current[0]) == memory_content_bytes({"value": "blue"}, "中文")
    plan = policy().plan_memory_capacity(
        current, [replace(current[0], content=FactContent(value="b"))], now=NOW, settings=settings
    )
    assert plan.evicted_keys == ()
    with pytest.raises(AppError) as exc:
        policy().plan_memory_capacity(current, [fact("new")], now=NOW, settings=settings)
    assert exc.value.code == ErrorCode.MEMORY_QUOTA_EXCEEDED
    plan = policy().plan_memory_capacity(
        current, [replace(current[0], is_deleted=True)], now=NOW, settings=settings
    )
    assert plan.evicted_keys == ()


def test_aux_byte_quota_cannot_be_bypassed_by_overwriting_one_key_with_larger_quote():
    current = fact(author=MemoryAuthor.USER, content=FactContent(value="汉" * 330))
    with pytest.raises(AppError) as exc:
        policy().plan_memory_capacity(
            [current],
            [replace(current, source_quote="汉" * 30)],
            now=NOW,
            settings=MemoryPolicySettings(max_memory_bytes_per_user=1024),
        )
    assert exc.value.code == ErrorCode.MEMORY_QUOTA_EXCEEDED


@pytest.mark.parametrize("reason", [MemoryRetirementReason.USER_DELETE, MemoryRetirementReason.ADMIN_DELETE])
def test_aux_automatic_write_never_revives_user_tombstone_even_with_new_evidence(reason):
    stored = fact(is_deleted=True, retirement_reason=reason, retirement_revision=5)
    evidence = MemorySourceEvidence(source_event_id=uuid4(), source_seq=9, source_scope_revision=8)
    result = policy().decide_automatic_update(
        stored, expected_version=1, evidence=evidence, source_run_id=uuid4(), same_conversation=False
    )
    assert result.allowed is False and result.reason == "USER_TOMBSTONE"


@pytest.mark.parametrize("reason,revision", [(None, 5), (MemoryRetirementReason.EXPIRE, None)])
def test_aux_deleted_state_with_missing_retirement_metadata_is_not_automatically_revived(reason, revision):
    stored = fact(is_deleted=True, retirement_reason=reason, retirement_revision=revision)
    evidence = MemorySourceEvidence(source_event_id=uuid4(), source_seq=9, source_scope_revision=5)
    decision = policy().decide_automatic_update(
        stored, expected_version=1, evidence=evidence, source_run_id=uuid4(), same_conversation=False
    )
    assert decision.allowed is False and decision.reason == "RETIREMENT_BARRIER"


@pytest.mark.parametrize(
    "reason",
    [MemoryRetirementReason.EXPIRE, MemoryRetirementReason.QUOTA_EVICT, MemoryRetirementReason.TASK_COMPLETE],
)
@pytest.mark.parametrize("source_revision,allowed", [(4, False), (5, True), (6, True), (None, False)])
def test_aux_retirement_revival_requires_actual_new_submission_revision(reason, source_revision, allowed):
    stored = fact(is_deleted=True, retirement_reason=reason, retirement_revision=5)
    evidence = (
        None
        if source_revision is None
        else MemorySourceEvidence(
            source_event_id=uuid4(), source_seq=9, source_scope_revision=source_revision
        )
    )
    result = policy().decide_automatic_update(
        stored, expected_version=1, evidence=evidence, source_run_id=uuid4(), same_conversation=False
    )
    assert result.allowed is allowed


@pytest.mark.parametrize(
    "changes,expected",
    [
        ({"author": MemoryAuthor.USER}, "CONFIRMED_PROTECTED"),
        ({"enabled": False}, "DISABLED"),
        ({"version": 2}, "VERSION_CHANGED"),
        ({"last_source_seq": 9}, "OLD_SOURCE"),
    ],
)
def test_aux_automatic_conflict_matrix_preserves_confirmation_disabled_and_causality(changes, expected):
    run = uuid4()
    stored = fact(last_source_run_id=run, **changes)
    evidence = MemorySourceEvidence(source_event_id=uuid4(), source_seq=9, source_scope_revision=0)
    result = policy().decide_automatic_update(
        stored, expected_version=1, evidence=evidence, source_run_id=run, same_conversation=True
    )
    assert result.allowed is False and result.reason == expected


def test_aux_cross_conversation_update_requires_selected_expected_version():
    stored = fact(last_source_seq=99, last_source_run_id=uuid4())
    evidence = MemorySourceEvidence(source_event_id=uuid4(), source_seq=1, source_scope_revision=0)
    assert (
        not policy()
        .decide_automatic_update(
            stored, expected_version=None, evidence=evidence, source_run_id=uuid4(), same_conversation=False
        )
        .allowed
    )
    assert (
        policy()
        .decide_automatic_update(
            stored, expected_version=1, evidence=evidence, source_run_id=uuid4(), same_conversation=False
        )
        .allowed
    )


def test_aux_author_category_ttl_and_manual_restore_are_explicit():
    module = policy()
    settings = MemoryPolicySettings()
    assert module.memory_expiry(MemoryAuthor.USER, MemoryCategory.PREFERENCE, NOW, settings) is None
    assert module.memory_expiry(MemoryAuthor.ADMIN, MemoryCategory.EXPLICIT, NOW, settings) is None
    assert module.memory_expiry(
        MemoryAuthor.AGENT, MemoryCategory.PREFERENCE, NOW, settings
    ) == NOW + timedelta(days=30)
    assert module.memory_expiry(
        MemoryAuthor.AGENT, MemoryCategory.TASK_STATE, NOW, settings
    ) == NOW + timedelta(days=7)
    deleted = fact(is_deleted=True, retirement_reason=MemoryRetirementReason.USER_DELETE)
    with pytest.raises(AppError):
        module.check_manual_update(deleted, expected_version=1, restore=False, enable=False)
    module.check_manual_update(deleted, expected_version=1, restore=True, enable=False)
    with pytest.raises(AppError) as exc:
        module.check_manual_update(deleted, expected_version=2, restore=True, enable=False)
    assert exc.value.code == ErrorCode.MEMORY_VERSION_CONFLICT
