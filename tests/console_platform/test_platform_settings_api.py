"""[平台设置 API] E-01 / E-06 / E-10 / E-17：真实 PostgreSQL + 真实 HTTP 封套。

真实边界（业务路径不 mock）：
- **真实 PostgreSQL**：版本表与幂等表都是真实行、真实 partial unique；
- **真实 Console API**：经 `ASGITransport(app=app)` 走真实路由、真实 CSRF、真实封套；
- **真实事务**：`get_session` 提交/回滚，保存与审计同事务。

清库隔离：conftest 的 `tenant` 清理不含 `platform_setting` / `platform_setting_idempotency`，
故本文件自带 `settings_tenant` 夹具在用例结束时删掉本租户的两张表行（tenant_id 每用例唯一）。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from httpx import AsyncClient, Response
from muad_common import SharedSettings
from muad_console_platform.application.platform_settings_idempotency import (
    ENDPOINT_SAVE,
)
from muad_console_platform.infrastructure.db import get_session_factory
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from console_platform.conftest import TenantContext

SETTINGS_URL = "/api/v1/platform-settings"
RESOURCE_TYPE = "PLATFORM_SETTING"


@pytest.fixture
async def settings_tenant(tenant: TenantContext) -> AsyncIterator[TenantContext]:
    try:
        yield tenant
    finally:
        factory = get_session_factory()
        async with factory() as session:
            for table in ("platform_setting", "platform_setting_idempotency"):
                await session.execute(
                    text(f"DELETE FROM control.{table} WHERE tenant_id = :tenant_id"),
                    {"tenant_id": tenant.tenant_id},
                )
            await session.commit()


async def _revisions(tenant_id: str) -> list[int]:
    factory = get_session_factory()
    async with factory() as session:
        rows = await session.scalars(
            text(
                "SELECT revision FROM control.platform_setting "
                "WHERE tenant_id = :tenant_id ORDER BY revision"
            ).bindparams(tenant_id=tenant_id)
        )
        return [int(value) for value in rows]


async def _audit_count(tenant_id: str) -> int:
    factory = get_session_factory()
    async with factory() as session:
        return int(
            await session.scalar(
                text(
                    "SELECT count(*) FROM control.config_audit_log "
                    "WHERE tenant_id = :tenant_id AND resource_type = :resource_type"
                ),
                {"tenant_id": tenant_id, "resource_type": RESOURCE_TYPE},
            )
        )


def _bad_payload(revision: int, settings: dict) -> dict:
    return {"revision": revision, "settings": settings}


async def _put(client: AsyncClient, payload: dict, key: str | None = None) -> Response:
    headers = {"Idempotency-Key": key} if key else None
    return await client.put(SETTINGS_URL, json=payload, headers=headers)


def _field_value(body: dict, path: str) -> object:
    for group in body["data"]["groups"]:
        for field in group["fields"]:
            if field["path"] == path:
                return field["value"]
    raise AssertionError(f"字段不存在：{path}")


# --------------------------------------------------------------------------- E-01
async def test_invalid_payload_returns_400_with_field_path_and_no_new_version(
    client: AsyncClient, settings_tenant: TenantContext
) -> None:
    """[E-01] 非法/越界/联动/未知键 ⇒ 400 + `details[].path`；版本数不变。"""
    limit = SharedSettings().batch_platform_limit
    cases = [
        (_bad_payload(0, {"task": {"max_attempts": 0}}), "task.max_attempts"),
        (_bad_payload(0, {"compaction": {"snip": {"max_groups": 1}}}), "snip.max_groups"),
        (
            _bad_payload(0, {"task": {"batch_max_concurrency": limit + 1}}),
            "task.batch_max_concurrency",
        ),
        (_bad_payload(0, {"task": {"nope": 1}}), "task.nope"),
        (_bad_payload(0, {"bogus": {"x": 1}}), "bogus"),
        (_bad_payload(0, {"compaction": {"snip": {"enabled": "yes"}}}), "snip.enabled"),
    ]
    for payload, expected_path in cases:
        response = await _put(client, payload)
        assert response.status_code == 400, response.text
        body = response.json()
        assert body["code"] == "VALIDATION_FAILED"
        details = body["data"]["details"]
        assert details[0]["path"] == expected_path, (payload, details)
    assert await _revisions(settings_tenant.tenant_id) == [], "被拒的提交不得产生版本"
    assert await _audit_count(settings_tenant.tenant_id) == 0


# --------------------------------------------------------------------------- E-06
async def test_restore_appends_new_revision_keeps_history_and_rejects_old_schema(
    client: AsyncClient, settings_tenant: TenantContext
) -> None:
    """[E-06] 回滚产生新 revision（内容等于目标版本）、历史全保留、旧 schema 内容被拒。"""
    tenant_id = settings_tenant.tenant_id
    assert (await _put(client, _bad_payload(0, {"task": {"max_attempts": 7}}))).status_code == 200
    assert (
        await _put(client, _bad_payload(1, {"task": {"max_attempts": 3}}))
    ).status_code == 200

    restore = await client.post(f"{SETTINGS_URL}/revisions/1/restore")
    assert restore.status_code == 200, restore.text
    assert restore.json()["data"] == {"revision": 3, "restored_from": 1}
    assert await _revisions(tenant_id) == [1, 2, 3], "历史行不得被改写或删除"

    current = await client.get(SETTINGS_URL)
    assert current.status_code == 200
    assert _field_value(current.json(), "task.max_attempts") == 7, "当前版本内容等于目标版本"

    missing = await client.post(f"{SETTINGS_URL}/revisions/99/restore")
    assert missing.status_code == 404
    assert missing.json()["code"] == "PLATFORM_SETTINGS_REVISION_NOT_FOUND"

    # 直接植入一条"旧 schema"版本（现行 schema 不认识 unknown_group）⇒ 回滚被拒
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                "INSERT INTO control.platform_setting (tenant_id, revision, settings_json) "
                "VALUES (:tenant_id, 4, CAST(:document AS jsonb))"
            ),
            {"tenant_id": tenant_id, "document": '{"unknown_group": {"x": 1}}'},
        )
        await session.commit()
    rejected = await client.post(f"{SETTINGS_URL}/revisions/4/restore")
    assert rejected.status_code == 400, rejected.text
    assert rejected.json()["code"] == "VALIDATION_FAILED"
    assert rejected.json()["data"]["details"][0]["path"] == "unknown_group"
    assert await _revisions(tenant_id) == [1, 2, 3, 4], "被拒的回滚不得产生新版本"


# --------------------------------------------------------------------------- E-10
async def test_secret_rejected_and_no_secret_value_in_response_audit_or_logs(
    client: AsyncClient, settings_tenant: TenantContext, caplog: pytest.LogCaptureFixture
) -> None:
    """[E-10] 敏感键被拒；响应体/审计/日志均不含敏感值。"""
    secret = "sk-LIVE-SECRET-6f2b"
    caplog.set_level("DEBUG")
    response = await _put(client, _bad_payload(0, {"task": {"api_key": secret}}))
    assert response.status_code == 400, response.text
    assert response.json()["code"] == "PLATFORM_SETTINGS_SECRET_REJECTED"
    assert secret not in response.text
    assert await _revisions(settings_tenant.tenant_id) == [], "被拒的提交不得落版本"
    assert await _audit_count(settings_tenant.tenant_id) == 0, "被拒的提交不得留审计行"
    assert secret not in caplog.text

    # 正常保存的审计行与响应体同样不得出现凭据值
    saved = await _put(client, _bad_payload(0, {"task": {"max_attempts": 5}}))
    assert saved.status_code == 200
    assert secret not in saved.text
    factory = get_session_factory()
    async with factory() as session:
        rows = list(
            await session.scalars(
                text(
                    "SELECT after_json::text FROM control.config_audit_log "
                    "WHERE tenant_id = :tenant_id AND resource_type = :resource_type"
                ).bindparams(tenant_id=settings_tenant.tenant_id, resource_type=RESOURCE_TYPE)
            )
        )
    assert secret not in " ".join(rows)


# --------------------------------------------------------------------------- E-17
async def _idempotency_rows(tenant_id: str) -> list[tuple]:
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            text(
                "SELECT idempotency_key, endpoint, request_fingerprint "
                "FROM control.platform_setting_idempotency WHERE tenant_id = :tenant_id"
            ),
            {"tenant_id": tenant_id},
        )
        return [tuple(row) for row in result]


async def test_idempotent_replay_returns_first_result_and_mismatch_is_409(
    client: AsyncClient, settings_tenant: TenantContext
) -> None:
    """[E-17] 同键同指纹重放首次结果（revision 不变）；同键不同指纹 ⇒ IDEMPOTENCY_MISMATCH。"""
    tenant_id = settings_tenant.tenant_id
    key = str(uuid.uuid4())
    first = await _put(client, _bad_payload(0, {"task": {"max_attempts": 5}}), key=key)
    assert first.status_code == 200, first.text
    assert first.json()["data"]["revision"] == 1

    replay = await _put(client, _bad_payload(0, {"task": {"max_attempts": 5}}), key=key)
    assert replay.status_code == 200
    assert replay.json()["data"] == first.json()["data"], "重放返回首次结果"
    assert await _revisions(tenant_id) == [1], "重放不得产生第二个版本"

    mismatch = await _put(client, _bad_payload(0, {"task": {"max_attempts": 6}}), key=key)
    assert mismatch.status_code == 409
    assert mismatch.json()["code"] == "IDEMPOTENCY_MISMATCH"
    assert await _revisions(tenant_id) == [1]

    rows = await _idempotency_rows(tenant_id)
    assert len(rows) == 1
    # 真实 partial unique：同 (tenant, key, endpoint) 的第二行被拒
    factory = get_session_factory()
    async with factory() as session:
        with pytest.raises(IntegrityError):
            await session.execute(
                text(
                    "INSERT INTO control.platform_setting_idempotency "
                    "(tenant_id, idempotency_key, endpoint, request_fingerprint, response_json) "
                    "VALUES (:tenant_id, :key, :endpoint, 'sha256:x', '{}'::jsonb)"
                ),
                {"tenant_id": tenant_id, "key": key, "endpoint": ENDPOINT_SAVE},
            )
        await session.rollback()

    # 回滚同样支持幂等：同键同目标重放不产生第二个版本
    restore_key = str(uuid.uuid4())
    assert (await _put(client, _bad_payload(1, {"task": {"max_attempts": 3}}))).status_code == 200
    restored = await client.post(
        f"{SETTINGS_URL}/revisions/1/restore", headers={"Idempotency-Key": restore_key}
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["data"] == {"revision": 3, "restored_from": 1}
    replay_restore = await client.post(
        f"{SETTINGS_URL}/revisions/1/restore", headers={"Idempotency-Key": restore_key}
    )
    assert replay_restore.status_code == 200
    assert replay_restore.json()["data"] == restored.json()["data"]
    assert await _revisions(tenant_id) == [1, 2, 3], "回滚重放不得产生第二个版本"

    restore_mismatch = await client.post(
        f"{SETTINGS_URL}/revisions/2/restore", headers={"Idempotency-Key": restore_key}
    )
    assert restore_mismatch.status_code == 409
    assert restore_mismatch.json()["code"] == "IDEMPOTENCY_MISMATCH"


# ----------------------------------------------------------- API-01/03/05 冒烟
async def test_read_settings_revisions_and_limits_envelope(
    client: AsyncClient, settings_tenant: TenantContext
) -> None:
    """API-01/03/05：统一封套、分组元数据、版本列表 `{items,page,page_size,total}`、非敏感限额。"""
    read = await client.get(SETTINGS_URL)
    assert read.status_code == 200
    data = read.json()["data"]
    assert data["revision"] == 0
    assert {group["key"] for group in data["groups"]} == {
        "compaction",
        "agent",
        "task",
        "memory",
        "artifact",
        "auth",
        "locale",
        "im",
        "mcp",
    }
    assert _field_value(read.json(), "task.max_attempts") == 3
    assert data["readonly_notes"], "非本页管理的项须给出归属说明"

    assert (await _put(client, _bad_payload(0, {"task": {"max_attempts": 7}}))).status_code == 200
    revisions = await client.get(f"{SETTINGS_URL}/revisions")
    assert revisions.status_code == 200
    listing = revisions.json()["data"]
    assert set(listing) == {"items", "page", "page_size", "total"}
    assert listing["total"] == 1
    assert listing["items"][0]["revision"] == 1
    assert "task.max_attempts" in listing["items"][0]["changed_keys"]

    limits = await client.get("/api/v1/platform-limits")
    assert limits.status_code == 200
    body = limits.json()["data"]
    assert set(body) == {"skill_import", "attachments"}
    assert set(body["skill_import"]) == {
        "zip_bytes_limit",
        "unpacked_bytes_limit",
        "entry_limit",
    }
    assert set(body["attachments"]) == {"max_bytes", "max_per_message"}

    bad_page = await client.get(f"{SETTINGS_URL}/revisions", params={"page_size": 0})
    assert bad_page.status_code == 422
