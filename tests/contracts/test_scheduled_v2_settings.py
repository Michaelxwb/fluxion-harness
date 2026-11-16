"""调度限制的平台设置契约（scheduled-task-v2 TASK-005 / B-04）。

真实边界：真实 schema 模块 + 真实 Console catalog 元数据；不连数据库、不 mock 校验。
配额数值的**唯一权威**是 `ScheduleSettings`（字段元信息声明范围），Console 元数据从它派生，
这里钉住三者不各自复制默认值。
"""

from __future__ import annotations

from dataclasses import asdict, fields

import pytest
from muad_console_platform.application.platform_settings_catalog import build_groups
from muad_contracts.platform_settings import (
    PlatformSettings,
    PlatformSettingsError,
    ScheduleSettings,
    default_platform_settings,
    parse_platform_settings,
)

pytestmark = pytest.mark.contract

#: 用户在 Console 可见/可改的调度限制叶子（新增叶子时先复核本回归）。
EXPECTED_LEAVES = {
    "max_schedules_per_user",
    "min_schedule_interval_sec",
    "max_fires_per_day_per_user",
    "max_agentic_runs_per_day_per_user",
    "max_concurrent_agentic_executions_per_user",
    "max_fire_history_days",
    "max_execution_history_days",
    "max_snapshot_bytes",
    "confirmation_ttl_sec",
}


def test_schedule_settings_are_the_declared_leaves() -> None:
    assert {item.name for item in fields(ScheduleSettings)} == EXPECTED_LEAVES


def test_schedule_group_defaults_are_declared_in_one_place() -> None:
    settings = parse_platform_settings({})
    assert asdict(settings.schedule) == {
        "max_schedules_per_user": 50,
        "min_schedule_interval_sec": 60,
        "max_fires_per_day_per_user": 500,
        "max_agentic_runs_per_day_per_user": 100,
        "max_concurrent_agentic_executions_per_user": 2,
        "max_fire_history_days": 90,
        "max_execution_history_days": 90,
        "max_snapshot_bytes": 262144,
        "confirmation_ttl_sec": 900,
    }


def test_schedule_group_merges_partial_payload_with_defaults() -> None:
    settings = parse_platform_settings({"schedule": {"max_schedules_per_user": 3}})
    assert settings.schedule.max_schedules_per_user == 3
    assert settings.schedule.max_fires_per_day_per_user == 500


@pytest.mark.parametrize(
    "payload",
    [
        {"schedule": {"unknown_leaf": 1}},
        {"schedule": {"max_schedules_per_user": "5"}},
        {"schedule": {"max_schedules_per_user": True}},
        {"schedule": {"max_fire_history_days": None}},
        {"schedule": []},
        {"schedule": {"min_schedule_interval_sec": 1.5}},
    ],
)
def test_schedule_group_rejects_unknown_or_mistyped_values(payload: dict[str, object]) -> None:
    with pytest.raises(PlatformSettingsError):
        parse_platform_settings(payload)


def _bounded_fields() -> list[tuple[str, int, int]]:
    return [
        (item.name, item.metadata["minimum"], item.metadata["maximum"])
        for item in fields(ScheduleSettings)
        if "minimum" in item.metadata and "maximum" in item.metadata
    ]


@pytest.mark.parametrize(("name", "minimum", "maximum"), _bounded_fields())
def test_schedule_bounds_are_enforced_from_schema_metadata(
    name: str, minimum: int, maximum: int
) -> None:
    def payload(value: int) -> dict[str, object]:
        values: dict[str, object] = {name: value}
        if name == "max_agentic_runs_per_day_per_user":
            # 跨字段联动：并发上限不得超过日上限，测试边界时保持组合合法。
            values["max_concurrent_agentic_executions_per_user"] = 1
        return {"schedule": values}

    assert getattr(parse_platform_settings(payload(minimum)).schedule, name) == minimum
    assert getattr(parse_platform_settings(payload(maximum)).schedule, name) == maximum
    for invalid in (minimum - 1, maximum + 1):
        with pytest.raises(PlatformSettingsError):
            parse_platform_settings(payload(invalid))


def test_agentic_concurrency_cannot_exceed_daily_agentic_cap() -> None:
    with pytest.raises(PlatformSettingsError):
        parse_platform_settings(
            {
                "schedule": {
                    "max_agentic_runs_per_day_per_user": 1,
                    "max_concurrent_agentic_executions_per_user": 2,
                }
            }
        )


def test_catalog_derives_schedule_fields_from_schema() -> None:
    group = next(item for item in build_groups(PlatformSettings(), overrides={}) if item["key"] == "schedule")
    assert group["applies_to"] == "next_operation"
    assert group["readonly"] is False
    catalog_fields = {item["path"]: item for item in group["fields"]}
    assert set(catalog_fields) == {f"schedule.{name}" for name in EXPECTED_LEAVES}
    defaults = default_platform_settings().schedule
    for leaf in EXPECTED_LEAVES:
        item = catalog_fields[f"schedule.{leaf}"]
        assert item["default"] == getattr(defaults, leaf)
        assert item["value"] == getattr(defaults, leaf)
        assert item["type"] == "int"
        assert item["effect_key"].startswith("settings.effect.")
    for leaf in ("min_schedule_interval_sec", "confirmation_ttl_sec"):
        assert catalog_fields[f"schedule.{leaf}"]["unit_key"] == "settings.unit.seconds"
    for leaf in ("max_fire_history_days", "max_execution_history_days"):
        assert catalog_fields[f"schedule.{leaf}"]["unit_key"] == "settings.unit.days"
    assert catalog_fields["schedule.max_snapshot_bytes"]["unit_key"] == "settings.unit.bytes"
    assert catalog_fields["schedule.max_schedules_per_user"]["min"] == 1
