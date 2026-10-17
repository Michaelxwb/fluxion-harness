"""B-208: real Console API, auth, PostgreSQL versions, transactions and field metadata."""

import json
import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import asdict, fields
from pathlib import Path

import pytest
from httpx import AsyncClient, Response
from muad_common import SharedSettings
from muad_console_platform.application.audit_service import AuditActor
from muad_console_platform.application.platform_settings_service import PlatformSettingsService
from muad_console_platform.infrastructure.db import dispose_engine, get_session_factory
from muad_contracts.platform_settings import (
    PlatformSettingsError,
    default_platform_settings,
    parse_platform_settings,
)
from sqlalchemy import text

from console_platform.conftest import TenantContext
from tests.acceptance.datastores import create_datastore, drop_datastore

pytestmark = pytest.mark.integration
URL = "/api/v1/platform-settings"


@pytest.fixture(scope="module", autouse=True)
async def isolated_settings_store() -> AsyncIterator[None]:
    """Own a migrated database for this module, even under the manifest's bare pytest command."""
    info = create_datastore()
    previous = {key: os.environ.get(key) for key in ("DATABASE_URL", "REDIS_URL")}
    await dispose_engine()
    os.environ.update(DATABASE_URL=info["database_url"], REDIS_URL=info["redis_url"])
    try:
        yield
    finally:
        await dispose_engine()
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        drop_datastore(info["name"], info["admin_database_url"], info["redis_url"])


@pytest.fixture
async def settings_tenant(tenant: TenantContext) -> AsyncIterator[TenantContext]:
    try:
        yield tenant
    finally:
        async with get_session_factory()() as session:
            for table in ("platform_setting", "platform_setting_idempotency"):
                await session.execute(text(f"DELETE FROM control.{table} WHERE tenant_id = :tenant_id"),
                                      {"tenant_id": tenant.tenant_id})
            await session.commit()


async def _save(
    client: AsyncClient, revision: int, document: dict[str, object], key: str | None = None,
) -> Response:
    return await client.put(URL, json={"revision": revision, "settings": document},
                            headers={"Idempotency-Key": key or str(uuid.uuid4())})


async def _stored(tenant_id: str) -> list[dict[str, object]]:
    async with get_session_factory()() as session:
        rows = await session.execute(text(
            "SELECT revision, settings_json FROM control.platform_setting "
            "WHERE tenant_id = :tenant_id ORDER BY revision"
        ), {"tenant_id": tenant_id})
        return [dict(row._mapping) for row in rows]


def _field_map(response: Response) -> dict[str, dict[str, object]]:
    assert response.status_code == 200, response.text
    return {field["path"]: field for group in response.json()["data"]["groups"] for field in group["fields"]}


async def test_b208_metadata_derives_defaults_ranges_and_units_from_schema(
    client: AsyncClient, settings_tenant: TenantContext,
) -> None:
    response = await client.get(URL)
    metadata = _field_map(response)
    defaults = default_platform_settings()
    resources = SharedSettings()
    for group_name in ("capacity", "model_pool", "webchat"):
        group = getattr(defaults, group_name)
        for item in fields(group):
            field = metadata[f"{group_name}.{item.name}"]
            assert field["default"] == getattr(group, item.name) == field["value"]
            assert field["min"] == item.metadata["minimum"]
            maximum = item.metadata.get("maximum")
            if item.name == "upload_max_bytes":
                maximum = resources.chat_upload_hard_max_bytes
            assert field.get("max") == maximum
            if "unit" in item.metadata:
                assert field["unit_key"] == f"settings.unit.{item.metadata['unit']}"
            assert field["overridden_by_resources"] == 0
            assert field["effect_key"].startswith("settings.effect.")
    assert metadata["capacity.queue_wait_timeout_sec"]["applies_to"] == "new_run"
    assert metadata["capacity.max_open_runs_per_user"]["applies_to"] == "next_operation"
    assert metadata["webchat.access_ttl_days"]["effect_key"] == "settings.effect.token_issuance"
    assert metadata["webchat.session_ttl_hours"]["effect_key"] == "settings.effect.session_exchange"
    assert metadata["webchat.history_retention_days"]["effect_key"] == "settings.effect.next_cleanup"
    assert metadata["webchat.reconnect_base_ms"]["effect_key"] == "settings.effect.new_connection"
    # Startup budgets do not become editable business settings.
    assert all(not path.startswith(("db_pool", "model_http", "runtime_execution")) for path in metadata)
    assert await _stored(settings_tenant.tenant_id) == []


async def test_b208_effective_values_do_not_replace_schema_defaults(
    client: AsyncClient, settings_tenant: TenantContext,
) -> None:
    document = asdict(default_platform_settings())
    document["webchat"].update(access_ttl_days=15, session_ttl_hours=6, history_page_size=75)
    document["capacity"]["max_open_runs_per_user"] = 4
    saved = await _save(client, 0, document)
    assert saved.status_code == 200, saved.text
    metadata = _field_map(await client.get(URL))
    assert metadata["webchat.access_ttl_days"]["value"] == 15
    assert metadata["webchat.access_ttl_days"]["default"] == 30
    assert metadata["webchat.session_ttl_hours"]["value"] == 6
    assert metadata["webchat.session_ttl_hours"]["default"] == 12
    assert metadata["capacity.max_open_runs_per_user"]["value"] == 4
    assert metadata["capacity.max_open_runs_per_user"]["default"] == 2


async def test_b208_save_replay_conflict_and_restore_preserve_all_version_rows(
    client: AsyncClient, settings_tenant: TenantContext,
) -> None:
    first = asdict(default_platform_settings())
    first["capacity"]["queue_wait_timeout_sec"] = 120
    first["model_pool"]["interactive_weight"] = 4
    first["webchat"]["access_ttl_days"] = 15
    key = str(uuid.uuid4())
    saved = await _save(client, 0, first, key)
    assert saved.status_code == 200 and saved.json()["data"]["revision"] == 1
    assert (await _save(client, 0, first, key)).json()["data"] == saved.json()["data"]
    baseline = await _stored(settings_tenant.tenant_id)
    stale = await _save(client, 0, first)
    assert stale.status_code == 409 and stale.json()["code"] == "PLATFORM_SETTINGS_VERSION_CONFLICT"
    assert (await _save(client, 1, asdict(default_platform_settings()))).status_code == 200
    restored = await client.post(f"{URL}/revisions/1/restore", headers={"Idempotency-Key": str(uuid.uuid4())})
    assert restored.status_code == 200 and restored.json()["data"] == {"revision": 3, "restored_from": 1}
    stored = await _stored(settings_tenant.tenant_id)
    assert [row["revision"] for row in stored] == [1, 2, 3]
    assert stored[0] == baseline[0] and stored[2]["settings_json"] == first
    listing = (await client.get(f"{URL}/revisions")).json()["data"]
    assert set(listing) == {"items", "page", "page_size", "total"} and listing["total"] == 3
    changed = {"capacity.queue_wait_timeout_sec", "model_pool.interactive_weight", "webchat.access_ttl_days"}
    assert changed <= set(listing["items"][0]["changed_keys"])


@pytest.mark.parametrize("group,key,value,path", [
    ("capacity", "max_open_runs_per_user", 1, "capacity.max_open_runs_per_user"),
    ("model_pool", "interactive_weight", True, "model_pool.interactive_weight"),
    ("webchat", "session_ttl_hours", 25, "webchat.session_ttl_hours"),
    ("webchat", "reconnect_cap_ms", 100, "webchat.reconnect_cap_ms"),
    ("webchat", "unknown", 1, "webchat.unknown"),
])
async def test_b208_rejected_values_locate_fields_and_write_no_revision_or_audit(
    client: AsyncClient, settings_tenant: TenantContext, group: str, key: str, value: object, path: str,
) -> None:
    document = asdict(default_platform_settings())
    document[group][key] = value
    rejected = await _save(client, 0, document)
    assert rejected.status_code == 400 and rejected.json()["code"] == "VALIDATION_FAILED"
    assert rejected.json()["data"]["details"][0]["path"] == path
    assert await _stored(settings_tenant.tenant_id) == []
    async with get_session_factory()() as session:
        count = await session.scalar(text(
            "SELECT count(*) FROM control.config_audit_log WHERE tenant_id = :tenant_id "
            "AND resource_type = 'PLATFORM_SETTING'"
        ), {"tenant_id": settings_tenant.tenant_id})
        assert count == 0


async def test_b208_startup_hard_caps_bound_metadata_and_reject_save_and_restore(
    client: AsyncClient, settings_tenant: TenantContext, monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = asdict(default_platform_settings())
    assert (await _save(client, 0, document)).status_code == 200
    monkeypatch.setenv("CHAT_UPLOAD_HARD_MAX_BYTES", "1024")
    metadata = _field_map(await client.get(URL))
    assert metadata["webchat.upload_max_bytes"]["max"] == 1024
    assert metadata["webchat.upload_max_bytes"]["value"] == document["webchat"]["upload_max_bytes"]
    rejected = await _save(client, 1, document)
    assert rejected.status_code == 400
    assert rejected.json()["data"]["details"][0]["path"] == "webchat.upload_max_bytes"
    document["webchat"]["upload_max_bytes"] = 512
    assert (await _save(client, 1, document)).status_code == 200
    rejected_restore = await client.post(f"{URL}/revisions/1/restore")
    assert rejected_restore.status_code == 400
    assert rejected_restore.json()["data"]["details"][0]["path"] == "webchat.upload_max_bytes"
    assert [row["revision"] for row in await _stored(settings_tenant.tenant_id)] == [1, 2]


async def test_b208_direct_owner_writes_cannot_bypass_startup_message_hard_cap(
    settings_tenant: TenantContext, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CHAT_MESSAGE_HARD_MAX_BYTES", "1024")
    settings = parse_platform_settings({"webchat": {"text_max_bytes": 2048}})
    async with get_session_factory()() as session:
        with pytest.raises(PlatformSettingsError, match="webchat.text_max_bytes"):
            await PlatformSettingsService(session).save(
                settings_tenant.tenant_id, AuditActor(account_id=uuid.uuid4()), 0, settings,
            )
        await session.rollback()
    assert await _stored(settings_tenant.tenant_id) == []


async def test_b208_effect_notes_and_units_are_available_in_both_languages(
    client: AsyncClient, settings_tenant: TenantContext,
) -> None:
    metadata = _field_map(await client.get(URL))
    visible = [field for path, field in metadata.items()
               if path.startswith(("capacity.", "model_pool.", "webchat."))]
    keys = {field["effect_key"] for field in visible}
    keys.update(field["unit_key"] for field in visible if "unit_key" in field)
    root = Path(__file__).resolve().parents[2] / "apps/console-platform/frontend/src/locales"
    for locale in ("zh-CN", "en-US"):
        translations = json.loads((root / f"{locale}.json").read_text())
        assert all(translations.get(key) for key in keys)
    assert metadata["capacity.queue_wait_timeout_sec"]["unit_key"] == "settings.unit.seconds"
    assert metadata["model_pool.aging_interval_sec"]["unit_key"] == "settings.unit.seconds"
