"""B-01：平台设置文档 schema 与跨字段联动校验（真实 schema 函数，无服务、无 mock）。

边界口径：每个叶子取「下界-1 / 下界 / 上界 / 上界+1」；联动组合逐一验证；未知键与敏感键
一律拒绝；错误消息必须定位到字段路径。
"""

from __future__ import annotations

import uuid
from dataclasses import asdict
from typing import Any

import pytest
from muad_contracts.platform_settings import (
    RECALL_MAX_LIMIT,
    CompactionConfigError,
    PlatformSettingsError,
    default_platform_settings,
    parse_platform_settings,
    validate_platform_settings,
)

BATCH_PLATFORM_LIMIT = 16


def _with(path: str, value: Any) -> dict[str, Any]:
    """把 `a.b.c` 形式的键路径落成嵌套 payload。"""
    payload: dict[str, Any] = {}
    node = payload
    parts = path.split(".")
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value
    return payload


def _count_leaves(node: Any) -> int:
    if isinstance(node, dict):
        return sum(_count_leaves(value) for value in node.values())
    return 1


def test_default_document_has_41_leaves_with_designed_defaults() -> None:
    settings = default_platform_settings()
    document = asdict(settings)

    counts = {group: _count_leaves(document[group]) for group in document}
    assert counts == {
        "compaction": 15,
        "agent": 4,
        "task": 6,
        "memory": 5,
        "artifact": 3,
        "auth": 4,
        "locale": 2,
        "im": 1,
        "mcp": 1,
    }
    assert sum(counts.values()) == 41

    compaction = settings.compaction
    assert (compaction.snip.enabled, compaction.snip.max_groups) == (True, 50)
    assert (compaction.snip.keep_head_groups, compaction.snip.keep_tail_groups) == (3, 20)
    assert (
        compaction.tool_result.persist_threshold_bytes,
        compaction.tool_result.round_budget_bytes,
        compaction.tool_result.preview_head_bytes,
        compaction.tool_result.preview_tail_bytes,
    ) == (8192, 200000, 2000, 2000)
    assert (compaction.micro.enabled, compaction.micro.keep_recent_tool_groups) == (False, 3)
    assert (compaction.summary.enabled, compaction.summary.threshold_bytes) == (False, 50000)
    assert compaction.summary.model_ref is None
    assert compaction.memory.budget_ratio == 0.2
    assert compaction.history_budget_messages == 40

    assert (settings.agent.max_turns, settings.agent.max_tool_calls) == (20, 30)
    assert (settings.agent.deadline_ms, settings.agent.max_model_retries) == (120000, 3)
    assert (
        settings.task.default_deadline_hours,
        settings.task.max_attempts,
        settings.task.batch_max_concurrency,
    ) == (24, 3, 8)
    assert (
        settings.task.misfire_grace_sec,
        settings.task.delivery_max_attempts,
        settings.task.delivery_backoff_base_sec,
    ) == (60, 5, 5)
    assert (settings.memory.write_enabled, settings.memory.max_injected_memories) == (True, 10)
    assert (settings.memory.max_injected_bytes, settings.memory.max_recall_bytes) == (2048, 4096)
    assert settings.memory.recall_default_limit == 10
    assert (
        settings.artifact.retention_days,
        settings.artifact.max_archive_files,
        settings.artifact.cleanup_batch_size,
    ) == (30, 2000, 500)
    assert (
        settings.auth.min_password_length,
        settings.auth.max_failed_attempts,
        settings.auth.lock_duration_minutes,
        settings.auth.session_ttl_hours,
    ) == (12, 5, 15, 12)
    assert (settings.locale.default_locale, settings.locale.default_timezone) == (
        "zh-CN",
        "Asia/Shanghai",
    )
    assert settings.im.progress_interval_sec == 5.0
    assert settings.mcp.max_tools_per_server == 200


def test_default_document_round_trips_through_parse() -> None:
    original = default_platform_settings()
    parsed = parse_platform_settings(asdict(original), batch_platform_limit=BATCH_PLATFORM_LIMIT)
    assert parsed == original


def test_empty_and_none_payload_fall_back_to_defaults() -> None:
    defaults = default_platform_settings()
    assert parse_platform_settings(None, batch_platform_limit=BATCH_PLATFORM_LIMIT) == defaults
    assert parse_platform_settings({}, batch_platform_limit=BATCH_PLATFORM_LIMIT) == defaults
    validate_platform_settings(defaults, batch_platform_limit=BATCH_PLATFORM_LIMIT)


# ---- 各叶子范围边界：下界-1 / 下界 / 上界 / 上界+1 ----

_ACCEPT: list[tuple[str, Any]] = [
    ("agent.max_turns", 1),
    ("agent.max_tool_calls", 0),
    ("agent.deadline_ms", 1),
    ("agent.max_model_retries", 0),
    ("task.default_deadline_hours", 1),
    ("task.max_attempts", 1),
    ("task.batch_max_concurrency", 1),
    ("task.misfire_grace_sec", 0),
    ("task.delivery_max_attempts", 1),
    ("task.delivery_backoff_base_sec", 1),
    ("memory.max_injected_memories", 0),
    ("memory.max_injected_bytes", 0),
    ("memory.max_recall_bytes", 1),
    ("memory.recall_default_limit", 0),
    ("memory.recall_default_limit", RECALL_MAX_LIMIT),
    ("artifact.retention_days", 1),
    ("artifact.max_archive_files", 1),
    ("artifact.cleanup_batch_size", 1),
    ("auth.min_password_length", 8),
    ("auth.max_failed_attempts", 1),
    ("auth.lock_duration_minutes", 1),
    ("auth.session_ttl_hours", 1),
    ("im.progress_interval_sec", 1.0),
    ("mcp.max_tools_per_server", 1),
    ("locale.default_locale", "en-US"),
    ("locale.default_timezone", "UTC"),
    ("locale.default_timezone", "America/New_York"),
    ("compaction.snip.max_groups", 24),
    ("compaction.snip.keep_head_groups", 1),
    ("compaction.snip.keep_tail_groups", 1),
    ("compaction.tool_result.persist_threshold_bytes", 0),
    ("compaction.tool_result.preview_head_bytes", 0),
    ("compaction.tool_result.preview_tail_bytes", 0),
    ("compaction.micro.keep_recent_tool_groups", 1),
    ("compaction.summary.threshold_bytes", 0),
    ("compaction.history_budget_messages", 1),
    ("compaction.memory.budget_ratio", 0.0),
    ("compaction.memory.budget_ratio", 1.0),
]

_REJECT: list[tuple[str, Any]] = [
    ("agent.max_turns", 0),
    ("agent.max_tool_calls", -1),
    ("agent.deadline_ms", 0),
    ("agent.max_model_retries", -1),
    ("task.default_deadline_hours", 0),
    ("task.max_attempts", 0),
    ("task.batch_max_concurrency", 0),
    ("task.misfire_grace_sec", -1),
    ("task.delivery_max_attempts", 0),
    ("task.delivery_backoff_base_sec", 0),
    ("memory.max_injected_memories", -1),
    ("memory.max_injected_bytes", -1),
    ("memory.max_recall_bytes", 0),
    ("memory.recall_default_limit", -1),
    ("memory.recall_default_limit", RECALL_MAX_LIMIT + 1),
    ("artifact.retention_days", 0),
    ("artifact.max_archive_files", 0),
    ("artifact.cleanup_batch_size", 0),
    ("auth.min_password_length", 7),
    ("auth.max_failed_attempts", 0),
    ("auth.lock_duration_minutes", 0),
    ("auth.session_ttl_hours", 0),
    ("im.progress_interval_sec", 0.5),
    ("mcp.max_tools_per_server", 0),
    ("locale.default_locale", "fr-FR"),
    ("locale.default_locale", "zh_CN"),
    ("locale.default_timezone", "Mars/Olympus"),
    ("locale.default_timezone", "Not/AZone"),
    ("compaction.snip.max_groups", 23),
    ("compaction.snip.keep_head_groups", 0),
    ("compaction.snip.keep_tail_groups", 0),
    ("compaction.tool_result.persist_threshold_bytes", -1),
    ("compaction.tool_result.preview_head_bytes", -1),
    ("compaction.tool_result.preview_tail_bytes", -1),
    ("compaction.micro.keep_recent_tool_groups", 0),
    ("compaction.summary.threshold_bytes", -1),
    ("compaction.history_budget_messages", 0),
    ("compaction.memory.budget_ratio", -0.1),
    ("compaction.memory.budget_ratio", 1.1),
]


@pytest.mark.parametrize("path,value", _ACCEPT, ids=lambda item: str(item))
def test_leaf_boundaries_at_or_inside_are_accepted(path: str, value: Any) -> None:
    settings = parse_platform_settings(
        _with(path, value), batch_platform_limit=BATCH_PLATFORM_LIMIT
    )
    node: Any = settings
    for part in path.split("."):
        node = getattr(node, part)
    assert node == value


@pytest.mark.parametrize("path,value", _REJECT, ids=lambda item: str(item))
def test_leaf_boundaries_outside_are_rejected(path: str, value: Any) -> None:
    with pytest.raises(PlatformSettingsError) as excinfo:
        parse_platform_settings(_with(path, value), batch_platform_limit=BATCH_PLATFORM_LIMIT)
    assert str(excinfo.value), "错误必须带可读原因"


# ---- 跨字段联动 ----

def test_snip_max_groups_must_exceed_kept_head_and_tail() -> None:
    boundary = _with("compaction.snip.keep_head_groups", 2)
    boundary["compaction"]["snip"]["keep_tail_groups"] = 3
    with pytest.raises(PlatformSettingsError):
        parse_platform_settings(
            {**boundary, "compaction": {"snip": {**boundary["compaction"]["snip"], "max_groups": 5}}}
        )
    accepted = parse_platform_settings(
        {"compaction": {"snip": {"keep_head_groups": 2, "keep_tail_groups": 3, "max_groups": 6}}}
    )
    assert accepted.compaction.snip.max_groups == 6


def test_preview_budget_must_fit_round_budget() -> None:
    with pytest.raises(PlatformSettingsError):
        parse_platform_settings(
            {
                "compaction": {
                    "tool_result": {
                        "persist_threshold_bytes": 1000,
                        "round_budget_bytes": 4000,
                        "preview_head_bytes": 2000,
                        "preview_tail_bytes": 2001,
                    }
                }
            }
        )
    accepted = parse_platform_settings(
        {
            "compaction": {
                "tool_result": {
                    "persist_threshold_bytes": 1000,
                    "round_budget_bytes": 4000,
                    "preview_head_bytes": 2000,
                    "preview_tail_bytes": 2000,
                }
            }
        }
    )
    assert accepted.compaction.tool_result.preview_tail_bytes == 2000


def test_summary_enabled_requires_uuid_model_reference() -> None:
    with pytest.raises(CompactionConfigError):
        parse_platform_settings({"compaction": {"summary": {"enabled": True}}})
    with pytest.raises(CompactionConfigError):
        parse_platform_settings(
            {"compaction": {"summary": {"enabled": True, "model_ref": "not-a-uuid"}}}
        )
    reference = str(uuid.uuid4())
    accepted = parse_platform_settings(
        {"compaction": {"summary": {"enabled": True, "model_ref": reference}}}
    )
    assert accepted.compaction.summary.model_ref == reference


def test_disabled_summary_accepts_absent_model_reference() -> None:
    settings = default_platform_settings()
    assert settings.compaction.summary.enabled is False
    assert settings.compaction.summary.model_ref is None


def test_batch_concurrency_must_not_exceed_platform_limit() -> None:
    with pytest.raises(PlatformSettingsError):
        parse_platform_settings(
            {"task": {"batch_max_concurrency": BATCH_PLATFORM_LIMIT + 1}},
            batch_platform_limit=BATCH_PLATFORM_LIMIT,
        )
    accepted = parse_platform_settings(
        {"task": {"batch_max_concurrency": BATCH_PLATFORM_LIMIT}},
        batch_platform_limit=BATCH_PLATFORM_LIMIT,
    )
    assert accepted.task.batch_max_concurrency == BATCH_PLATFORM_LIMIT


# ---- 未知键与敏感键一律拒绝（fail-closed）----

@pytest.mark.parametrize(
    "payload",
    [
        {"unknown_group": {}},
        {"account_password": "x"},
        {"agent": {"unknown_key": 1}},
        {"agent": {"api_key": "x"}},
        {"auth": {"password": "x"}},
        {"auth": {"client_secret": "x"}},
        {"auth": {"access_token": "x"}},
        {"auth": {"database_dsn": "x"}},
        {"auth": {"credential": "x"}},
        {"memory": {"api_key": "x"}},
        {"compaction": {"unknown_section": {}}},
        {"compaction": {"snip": {"unknown_key": 1}}},
        {"compaction": {"summary": {"api_token": "x"}}},
    ],
)
def test_unknown_and_sensitive_keys_are_rejected(payload: dict[str, Any]) -> None:
    with pytest.raises(PlatformSettingsError):
        parse_platform_settings(payload)


def test_sensitive_named_leaves_outside_whitelist_do_not_leak_through() -> None:
    # `auth.min_password_length` 虽含 "password" 但在白名单内，必须被接受。
    accepted = parse_platform_settings({"auth": {"min_password_length": 16}})
    assert accepted.auth.min_password_length == 16


# ---- 类型错误与错误定位 ----

@pytest.mark.parametrize(
    "payload",
    [
        {"agent": {"max_turns": "20"}},
        {"agent": {"max_turns": True}},
        {"im": {"progress_interval_sec": True}},
        {"locale": {"default_locale": 123}},
        {"compaction": {"summary": {"model_ref": 123}}},
    ],
)
def test_type_mismatches_are_rejected(payload: dict[str, Any]) -> None:
    with pytest.raises(PlatformSettingsError):
        parse_platform_settings(payload)


def test_error_message_locates_the_field_path() -> None:
    with pytest.raises(PlatformSettingsError) as excinfo:
        parse_platform_settings({"task": {"max_attempts": 0}})
    assert "task.max_attempts" in str(excinfo.value)
