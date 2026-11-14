"""Console governance writes go through the single Runtime command bus."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from httpx import AsyncClient
from muad_console_platform.api.deps import get_runtime_memory_client
from muad_console_platform.infrastructure.db import get_engine, get_session_factory
from muad_console_platform.infrastructure.runtime_memory_client import RuntimeMemoryClient
from muad_console_platform.main import app as console_app
from sqlalchemy import event, text

from console_platform.conftest import TenantContext, runtime_memory_app
from tests.internal_service import TOKEN

pytestmark = [
    pytest.mark.integration,
    pytest.mark.usefixtures("runtime_memory_override"),
]

RUNTIME_TABLES = ("runtime.user_memory", "runtime.user_memory_history")
AUDIT_RESOURCE_TYPE = "USER_MEMORY"
CANDIDATE = {
    "memory_kind": "PREFERENCE",
    "predicate": "output_style",
    "content": {"value": "concise answers"},
    "title": "输出风格",
}


def _headers(tenant: TenantContext, command_id: str | None = None) -> dict[str, str]:
    headers = {"X-Tenant-Id": tenant.tenant_id}
    if command_id is not None:
        headers["Idempotency-Key"] = command_id
    return headers


def _runtime_client_for(token: str) -> RuntimeMemoryClient:
    return RuntimeMemoryClient(
        "http://runtime", service_token=token, transport=httpx.ASGITransport(app=runtime_memory_app())
    )


class RuntimeSqlCapture:
    """Console's own engine must never write a runtime memory table."""

    def __init__(self) -> None:
        self.statements: list[str] = []
        self._engine = get_engine()

    def __enter__(self) -> RuntimeSqlCapture:
        event.listen(self._engine.sync_engine, "before_cursor_execute", self._capture)
        return self

    def __exit__(self, *exc_info: object) -> None:
        event.remove(self._engine.sync_engine, "before_cursor_execute", self._capture)

    def _capture(self, *args: Any) -> None:
        statement = str(args[2])
        if any(table in statement for table in RUNTIME_TABLES):
            self.statements.append(statement)


@dataclass(frozen=True)
class Counts:
    memories: int
    live: int
    history: int
    receipts: int
    audits: int
    forget_epoch: int | None


EMPTY = Counts(memories=0, live=0, history=0, receipts=0, audits=0, forget_epoch=None)


async def _counts(tenant_id: str, user_id: str) -> Counts:
    session_factory = get_session_factory()
    async with session_factory() as session:
        scope = {"tenant_id": tenant_id, "user_id": uuid.UUID(user_id)}
        return Counts(
            memories=await session.scalar(
                text(
                    "SELECT count(*) FROM runtime.user_memory "
                    "WHERE tenant_id = :tenant_id AND user_id = :user_id"
                ),
                scope,
            ),
            live=await session.scalar(
                text(
                    "SELECT count(*) FROM runtime.user_memory WHERE tenant_id = :tenant_id "
                    "AND user_id = :user_id AND is_deleted = false"
                ),
                scope,
            ),
            history=await session.scalar(
                text(
                    "SELECT count(*) FROM runtime.user_memory_history "
                    "WHERE tenant_id = :tenant_id AND user_id = :user_id"
                ),
                scope,
            ),
            receipts=await session.scalar(
                text(
                    "SELECT count(*) FROM runtime.memory_command_receipt "
                    "WHERE tenant_id = :tenant_id AND user_id = :user_id"
                ),
                scope,
            ),
            audits=await session.scalar(
                text(
                    "SELECT count(*) FROM control.config_audit_log WHERE tenant_id = :tenant_id "
                    "AND resource_type = :resource_type AND resource_id = :user_id"
                ),
                {**scope, "resource_type": AUDIT_RESOURCE_TYPE},
            ),
            forget_epoch=await session.scalar(
                text(
                    "SELECT forget_epoch FROM runtime.memory_scope WHERE tenant_id = :tenant_id "
                    "AND user_id = :user_id"
                ),
                scope,
            ),
        )


async def _memory_row(tenant_id: str, user_id: str) -> dict[str, Any]:
    session_factory = get_session_factory()
    async with session_factory() as session:
        row = (
            (
                await session.execute(
                    text(
                        "SELECT id, enabled, version, is_deleted, expires_at, author, content_json "
                        "FROM runtime.user_memory WHERE tenant_id = :tenant_id AND user_id = :user_id"
                    ),
                    {"tenant_id": tenant_id, "user_id": uuid.UUID(user_id)},
                )
            )
            .mappings()
            .one()
        )
        return dict(row)


async def _create_user(client: AsyncClient, tenant: TenantContext) -> str:
    created = await client.post(
        "/api/v1/users",
        json={"user_code": f"user-{uuid.uuid4().hex[:8]}", "display_name": "Memory Writer"},
        headers=_headers(tenant),
    )
    assert created.status_code == 200
    return created.json()["data"]["id"]


async def _command(
    client: AsyncClient,
    tenant: TenantContext,
    user_id: str,
    *,
    command_id: str,
    **payload: Any,
) -> httpx.Response:
    return await client.post(
        f"/api/v1/users/{user_id}/memory/commands",
        json=payload,
        headers=_headers(tenant, command_id),
    )


async def _create_memory(
    client: AsyncClient,
    tenant: TenantContext,
    user_id: str,
    *,
    command_id: str,
    value: str = "concise answers",
    **candidate: Any,
) -> httpx.Response:
    body = {**CANDIDATE, "content": {"value": value}, **candidate}
    return await _command(client, tenant, user_id, command_id=command_id, action="CREATE", candidate=body)


async def _memory_id(client: AsyncClient, tenant: TenantContext, user_id: str) -> str:
    listing = await client.get(
        f"/api/v1/users/{user_id}/memory", headers=_headers(tenant)
    )
    items = listing.json()["data"]["items"]
    assert len(items) == 1
    return items[0]["id"]


def _data(response: httpx.Response) -> dict[str, Any]:
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def test_local_01_same_command_replays_without_second_effect(
    client: AsyncClient, tenant: TenantContext
) -> None:
    user_id = await _create_user(client, tenant)
    command_id = uuid.uuid4().hex
    with RuntimeSqlCapture() as capture:
        first = _data(await _create_memory(client, tenant, user_id, command_id=command_id))
        assert first["status"] == "CREATED" and first["version"] == 1
        assert first["memory_key"].startswith("v1.slot.preference.output_style.")

        replay = _data(await _create_memory(client, tenant, user_id, command_id=command_id))
        assert replay["status"] == "CREATED" and replay["replayed"] is True
        assert replay["memory_key"] == first["memory_key"]

        mismatch = await _create_memory(
            client, tenant, user_id, command_id=command_id, value="another value"
        )
        assert mismatch.status_code == 409
        assert mismatch.json()["code"] == "MEMORY_IDEMPOTENCY_CONFLICT"

        assert await _counts(tenant.tenant_id, user_id) == Counts(
            memories=1, live=1, history=1, receipts=1, audits=2, forget_epoch=0
        )
    lowered = [statement.lower().lstrip() for statement in capture.statements]
    assert all(statement.startswith(("select", "with")) for statement in lowered), (
        "Console must not write runtime memory tables itself"
    )


async def test_local_02_version_conflict_and_missing_memory_pass_through(
    client: AsyncClient, tenant: TenantContext
) -> None:
    user_id = await _create_user(client, tenant)
    _data(await _create_memory(client, tenant, user_id, command_id=uuid.uuid4().hex))
    memory_id = await _memory_id(client, tenant, user_id)

    existing = await _create_memory(client, tenant, user_id, command_id=uuid.uuid4().hex)
    assert existing.status_code == 409
    assert existing.json()["code"] == "MEMORY_ALREADY_EXISTS"

    stale_update = await _command(
        client,
        tenant,
        user_id,
        command_id=uuid.uuid4().hex,
        action="UPDATE",
        memory_id=memory_id,
        expected_version=99,
        updates={"title": "新标题"},
    )
    assert stale_update.status_code == 409
    assert stale_update.json()["code"] == "MEMORY_VERSION_CONFLICT"

    unknown = await _command(
        client,
        tenant,
        user_id,
        command_id=uuid.uuid4().hex,
        action="DELETE",
        memory_id=str(uuid.uuid4()),
        expected_version=1,
    )
    assert unknown.status_code == 404
    assert unknown.json()["code"] == "MEMORY_NOT_FOUND"
    assert await _counts(tenant.tenant_id, user_id) == Counts(
        memories=1, live=1, history=1, receipts=1, audits=1, forget_epoch=0
    )


async def test_local_03_enable_disable_keeps_version_chain(
    client: AsyncClient, tenant: TenantContext
) -> None:
    user_id = await _create_user(client, tenant)
    _data(await _create_memory(client, tenant, user_id, command_id=uuid.uuid4().hex))
    memory_id = await _memory_id(client, tenant, user_id)

    disabled = _data(
        await _command(
            client,
            tenant,
            user_id,
            command_id=uuid.uuid4().hex,
            action="DISABLE",
            memory_id=memory_id,
            expected_version=1,
        )
    )
    assert disabled["status"] == "PATCHED" and disabled["version"] == 2
    assert (await _memory_row(tenant.tenant_id, user_id))["enabled"] is False

    enabled = _data(
        await _command(
            client,
            tenant,
            user_id,
            command_id=uuid.uuid4().hex,
            action="ENABLE",
            memory_id=memory_id,
            expected_version=2,
        )
    )
    assert enabled["version"] == 3
    assert (await _memory_row(tenant.tenant_id, user_id))["enabled"] is True


async def test_local_04_clear_is_one_command_and_increments_epoch(
    client: AsyncClient, tenant: TenantContext
) -> None:
    user_id = await _create_user(client, tenant)
    _data(await _create_memory(client, tenant, user_id, command_id=uuid.uuid4().hex))
    memory_id = await _memory_id(client, tenant, user_id)
    _data(
        await _command(
            client,
            tenant,
            user_id,
            command_id=uuid.uuid4().hex,
            action="DELETE",
            memory_id=memory_id,
            expected_version=1,
        )
    )
    _data(
        await _command(
            client,
            tenant,
            user_id,
            command_id=uuid.uuid4().hex,
            action="CREATE",
            candidate={**CANDIDATE, "predicate": "language", "content": {"value": "zh-CN"}, "title": "语言"},
        )
    )

    cleared = _data(
        await _command(client, tenant, user_id, command_id=uuid.uuid4().hex, action="CLEAR")
    )
    assert cleared["status"] == "CLEARED"
    counts = await _counts(tenant.tenant_id, user_id)
    assert counts.live == 0 and counts.forget_epoch == 1


async def test_local_05_unreachable_or_forbidden_runtime_never_reports_success(
    client: AsyncClient,
    tenant: TenantContext,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = await _create_user(client, tenant)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    unreachable = RuntimeMemoryClient("http://127.0.0.1:1", service_token=TOKEN)
    try:
        console_app.dependency_overrides[get_runtime_memory_client] = lambda: unreachable
        with caplog.at_level(logging.WARNING):
            response = await _create_memory(client, tenant, user_id, command_id=uuid.uuid4().hex)
    finally:
        await unreachable.aclose()
        console_app.dependency_overrides.pop(get_runtime_memory_client, None)
    assert response.status_code == 500
    assert response.json()["code"] == "COMMON_INTERNAL_ERROR"
    assert "runtime_memory_request_failed" in caplog.text
    assert await _counts(tenant.tenant_id, user_id) == EMPTY

    rejected = _runtime_client_for("wrong-service-token")
    try:
        console_app.dependency_overrides[get_runtime_memory_client] = lambda: rejected
        forbidden = await _create_memory(client, tenant, user_id, command_id=uuid.uuid4().hex)
    finally:
        await rejected.aclose()
        console_app.dependency_overrides.pop(get_runtime_memory_client, None)
    assert forbidden.status_code == 403
    assert await _counts(tenant.tenant_id, user_id) == EMPTY


async def test_local_06_missing_runtime_configuration_fails_explicitly(
    client: AsyncClient,
    tenant: TenantContext,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = await _create_user(client, tenant)
    console_app.dependency_overrides.pop(get_runtime_memory_client, None)
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", "")
    monkeypatch.setenv("AGENT_RUNTIME_URL", "")
    with caplog.at_level(logging.WARNING):
        response = await _create_memory(client, tenant, user_id, command_id=uuid.uuid4().hex)
    assert response.status_code == 500
    assert "INTERNAL_SERVICE_TOKEN" in caplog.text
    assert "AGENT_RUNTIME_URL" in caplog.text
    assert await _counts(tenant.tenant_id, user_id) == EMPTY


async def test_local_07_dto_whitelist_and_tenant_scope(
    client: AsyncClient, tenant: TenantContext
) -> None:
    user_id = await _create_user(client, tenant)
    for override in (
        {"author": "ADMIN"},
        {"memory_key": "color", "category": "PREFERENCE", "value": "v"},
        {"content": {"value": "   "}},
    ):
        rejected = await _create_memory(
            client, tenant, user_id, command_id=uuid.uuid4().hex, **override
        )
        assert rejected.status_code == 422, override
        assert rejected.json()["code"] == "COMMON_VALIDATION_ERROR"

    created = await _create_memory(client, tenant, user_id, command_id=uuid.uuid4().hex)
    _data(created)

    unknown_user = str(uuid.uuid4())
    foreign = await _create_memory(client, tenant, unknown_user, command_id=uuid.uuid4().hex)
    assert foreign.status_code == 404
    assert foreign.json()["code"] == "COMMON_NOT_FOUND"

    listed = await client.get(f"/api/v1/users/{user_id}/memory", headers=_headers(tenant))
    assert listed.json()["data"]["total"] == 1


async def test_local_08_restore_keeps_identity_and_version(
    client: AsyncClient, tenant: TenantContext
) -> None:
    user_id = await _create_user(client, tenant)
    _data(await _create_memory(client, tenant, user_id, command_id=uuid.uuid4().hex))
    memory_id = await _memory_id(client, tenant, user_id)

    _data(
        await _command(
            client,
            tenant,
            user_id,
            command_id=uuid.uuid4().hex,
            action="DELETE",
            memory_id=memory_id,
            expected_version=1,
        )
    )
    row = await _memory_row(tenant.tenant_id, user_id)
    assert row["is_deleted"] is True and row["version"] == 2

    restored = _data(
        await _command(
            client,
            tenant,
            user_id,
            command_id=uuid.uuid4().hex,
            action="RESTORE",
            memory_id=memory_id,
            expected_version=2,
        )
    )
    assert restored["status"] == "RESTORED" and restored["version"] == 3
    row = await _memory_row(tenant.tenant_id, user_id)
    assert str(row["id"]) == memory_id and row["is_deleted"] is False and row["enabled"] is True


async def test_local_09_restore_version_conflict_is_visible(
    client: AsyncClient, tenant: TenantContext
) -> None:
    user_id = await _create_user(client, tenant)
    _data(await _create_memory(client, tenant, user_id, command_id=uuid.uuid4().hex))
    memory_id = await _memory_id(client, tenant, user_id)
    _data(
        await _command(
            client,
            tenant,
            user_id,
            command_id=uuid.uuid4().hex,
            action="DELETE",
            memory_id=memory_id,
            expected_version=1,
        )
    )
    stale = await _command(
        client,
        tenant,
        user_id,
        command_id=uuid.uuid4().hex,
        action="RESTORE",
        memory_id=memory_id,
        expected_version=1,
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "MEMORY_VERSION_CONFLICT"
