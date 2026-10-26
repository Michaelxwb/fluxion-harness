"""Settings contracts and B-22 support through a real isolated PostgreSQL service."""

from dataclasses import asdict
from importlib import import_module
from uuid import uuid4

import pytest
from muad_common import SharedSettings
from muad_console_platform.application.audit_service import AuditActor
from muad_console_platform.application.platform_settings_catalog import build_groups
from muad_console_platform.application.platform_settings_service import PlatformSettingsService
from muad_console_platform.infrastructure.models.control import ConfigAuditLog, PlatformSetting
from muad_contracts.platform_settings import (
    PlatformSettingsError,
    default_platform_settings,
    parse_platform_settings,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.async_tool_datastores import async_tool_database as async_tool_database
from tests.async_tool_datastores import async_tool_datastore as async_tool_datastore

BOUNDS = {
    "max_injected_memories": (0, 20), "max_injected_bytes": (0, 16384),
    "max_recall_bytes": (1, 16384), "recall_default_limit": (0, 20),
    "max_entries_per_user": (1, 1000), "max_memory_bytes_per_user": (1024, 1048576),
    "ttl_days_user": (0, 3650), "ttl_days_agent": (1, 365), "ttl_days_task_state": (1, 90),
    "consolidation_min_user_messages": (1, 100), "consolidation_max_input_bytes": (1024, 131072),
    "consolidation_max_output_tokens": (128, 4096), "consolidation_timeout_sec": (1, 120),
    "consolidation_max_candidates": (1, 20), "consolidation_max_attempts": (1, 3),
    "consolidation_retry_delay_sec": (1, 60), "consolidation_lease_sec": (16, 300),
}
BOOLS = {"write_enabled", "inject_inferred", "task_state_injection_enabled", "consolidation_enabled"}


@pytest.mark.unit
def test_memory_schema_and_catalog_have_exact_designed_leaf_set_and_bounds():
    settings = default_platform_settings()
    assert set(asdict(settings.memory)) == set(BOUNDS) | BOOLS
    groups = build_groups(settings, overrides={})
    fields = {field["path"].removeprefix("memory."): field
              for group in groups if group["key"] == "memory" for field in group["fields"]}
    assert set(fields) == set(BOUNDS) | BOOLS
    for name, (minimum, maximum) in BOUNDS.items():
        assert (fields[name]["min"], fields[name]["max"]) == (minimum, maximum)
        assert fields[name]["default"] == asdict(settings.memory)[name]
    assert settings.memory.max_entries_per_user == 200
    assert settings.memory.max_memory_bytes_per_user == 65536
    assert settings.memory.consolidation_enabled is True
    assert settings.memory.inject_inferred is False


@pytest.mark.unit
@pytest.mark.parametrize("name", BOUNDS)
@pytest.mark.parametrize("edge", ["min", "max", "below", "above"])
def test_each_numeric_memory_leaf_accepts_bounds_and_rejects_outside(name, edge):
    minimum, maximum = BOUNDS[name]
    value = {"min": minimum, "max": maximum, "below": minimum - 1, "above": maximum + 1}[edge]
    memory = {"consolidation_timeout_sec": 1, "consolidation_lease_sec": 150, name: value}
    if edge in ("below", "above"):
        with pytest.raises(PlatformSettingsError):
            parse_platform_settings({"memory": memory})
    else:
        settings = parse_platform_settings({"memory": memory})
        assert getattr(settings.memory, name) == value


@pytest.mark.unit
@pytest.mark.parametrize("name", BOUNDS)
@pytest.mark.parametrize("value", [True, "1", 1.5])
def test_memory_int_fields_do_not_coerce_bool_string_or_float(name, value):
    with pytest.raises(PlatformSettingsError):
        parse_platform_settings({"memory": {name: value}})


@pytest.mark.unit
@pytest.mark.parametrize("name", sorted(BOOLS))
@pytest.mark.parametrize("value", [1, "true", None])
def test_memory_bool_fields_do_not_coerce_other_types(name, value):
    with pytest.raises(PlatformSettingsError):
        parse_platform_settings({"memory": {name: value}})


@pytest.mark.unit
def test_memory_lease_margin_and_removed_ratio_and_history_rounds():
    with pytest.raises(PlatformSettingsError):
        parse_platform_settings({"memory": {"consolidation_timeout_sec": 30, "consolidation_lease_sec": 44}})
    boundary = parse_platform_settings({"memory": {"consolidation_lease_sec": 45}})
    assert boundary.memory.consolidation_lease_sec == 45
    assert default_platform_settings().compaction.max_history_tool_rounds == 6
    for value in (0, 50):
        assert parse_platform_settings({"compaction": {"max_history_tool_rounds": value}})
    for value in (-1, 51, True):
        with pytest.raises(PlatformSettingsError):
            parse_platform_settings({"compaction": {"max_history_tool_rounds": value}})
    with pytest.raises(PlatformSettingsError):
        parse_platform_settings({"compaction": {"memory": {"budget_ratio": 0.2}}})
    with pytest.raises(PlatformSettingsError):
        parse_platform_settings({"memory": {"unknown_memory_key": 1}})


@pytest.mark.unit
@pytest.mark.parametrize("source", ["base", "override"])
def test_removed_memory_ratio_is_rejected_by_merge(source):
    from muad_contracts.platform_settings import merge_compaction_payload

    with pytest.raises(PlatformSettingsError):
        merge_compaction_payload(**{source: {"memory": {"budget_ratio": 0.2}}})


@pytest.mark.integration
@pytest.mark.parametrize(("quota", "higher"), [
    ("max_entries_per_user", 300), ("max_memory_bytes_per_user", 131072),
])
async def test_b22_save_and_restore_cannot_lower_quota_or_leave_version_and_audit(
    async_tool_database, quota, higher
):
    module = import_module("muad_console_platform.application.platform_settings_service")
    tenant = "memory-settings-" + uuid4().hex
    actor = AuditActor(account_id=uuid4())
    async with AsyncSession(async_tool_database) as session:
        service = PlatformSettingsService(session)
        await service.save(tenant, actor, 0, default_platform_settings())
        await session.commit()
        raised = parse_platform_settings({"memory": {quota: higher}})
        await service.save(tenant, actor, 1, raised)
        await session.commit()
        with pytest.raises(module.MemoryQuotaReductionRequiresMaintenance):
            await service.save(tenant, actor, 2, default_platform_settings())
        await session.rollback()
        with pytest.raises(module.MemoryQuotaReductionRequiresMaintenance):
            await service.restore(tenant, actor, 1)
        await session.rollback()
        current = await service.read_current(tenant)
        assert current.revision == 2 and getattr(current.settings.memory, quota) == higher
        total = await session.scalar(select(func.count()).select_from(PlatformSetting)
                                     .where(PlatformSetting.tenant_id == tenant))
        assert total == 2
        audit_count = await session.scalar(select(func.count()).select_from(ConfigAuditLog)
                                          .where(ConfigAuditLog.tenant_id == tenant))
        assert audit_count == 2


@pytest.mark.integration
@pytest.mark.parametrize("endpoint", ["save", "restore"])
async def test_b22_api_reports_maintenance_error_for_lowering_quota(async_tool_database, endpoint):
    from muad_api import AppError, ErrorCode
    from muad_console_platform.api.platform_settings import (
        PlatformSettingsSaveRequest,
        _apply_restore,
        _apply_save,
    )

    tenant, actor = "memory-api-" + uuid4().hex, AuditActor(account_id=uuid4())
    async with AsyncSession(async_tool_database) as session:
        service = PlatformSettingsService(session)
        await service.save(tenant, actor, 0, default_platform_settings())
        raised = parse_platform_settings({"memory": {"max_entries_per_user": 300}})
        await service.save(tenant, actor, 1, raised)
        await session.commit()
        with pytest.raises(AppError) as error:
            if endpoint == "save":
                payload = PlatformSettingsSaveRequest(
                    revision=2, settings=asdict(default_platform_settings())
                )
                await _apply_save(
                    session, tenant, actor, payload, None, SharedSettings()
                )
            else:
                await _apply_restore(session, tenant, actor, 1, None)
        assert error.value.code == ErrorCode.MEMORY_QUOTA_REDUCTION_REQUIRES_MAINTENANCE
        await session.rollback()
        assert (await service.read_current(tenant)).revision == 2
