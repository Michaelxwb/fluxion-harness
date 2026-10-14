"""TASK-025 辅助验收：Console 写操作转 Runtime 并保持命令回执（LOCAL，integration）。

真实边界（不得 mock）：
- 真实 Console HTTP：ASGI + 真实会话鉴权 + 管理员角色 + CSRF（`console_platform.conftest` 的 `client`）；
- 真实 Runtime HTTP：`runtime_memory_override` 提供——真实 Runtime 记忆管理路由、真实服务身份门控、
  真实唯一事务写服务（`MemoryAdminService` → `MemoryService.execute_in_session`）；
- 真实 PostgreSQL：迁移到 0020 head 的 `runtime.user_memory*` / `memory_command_receipt` / `memory_scope`。

Console 的 Runtime 客户端、Runtime 的写服务与数据库都不 mock；Runtime 的 platform-settings 端口见
`tests/console_platform/conftest.py` 的说明（仓库自带 Null 实现，避免连 dev Console）。
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
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


def _headers(tenant: TenantContext, command_id: str | None = None) -> dict[str, str]:
    headers = {"X-Tenant-Id": tenant.tenant_id}
    if command_id is not None:
        headers["Idempotency-Key"] = command_id
    return headers


def _runtime_client_for(token: str) -> RuntimeMemoryClient:
    """把 Console 的客户端指到真实 Runtime 路由，但换一个服务身份（用于 403 边界）。"""
    return RuntimeMemoryClient(
        "http://runtime", service_token=token, transport=httpx.ASGITransport(app=runtime_memory_app())
    )


class RuntimeSqlCapture:
    """监听 Console 自己的引擎：对 runtime 记忆表的任何写语句都是违规。"""

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


async def _memory_row(tenant_id: str, user_id: str, memory_key: str) -> dict[str, Any]:
    session_factory = get_session_factory()
    async with session_factory() as session:
        row = (
            (
                await session.execute(
                    text(
                        "SELECT enabled, version, is_deleted, expires_at, author, category, content_json "
                        "FROM runtime.user_memory WHERE tenant_id = :tenant_id AND user_id = :user_id "
                        "AND memory_key = :memory_key"
                    ),
                    {"tenant_id": tenant_id, "user_id": uuid.UUID(user_id), "memory_key": memory_key},
                )
            )
            .mappings()
            .one()
        )
        return dict(row)


async def _expire_now(tenant_id: str, user_id: str, memory_key: str) -> None:
    """把有效期推成过期（测试前置，不经产品路径）。"""
    session_factory = get_session_factory()
    async with session_factory() as session:
        await session.execute(
            text(
                "UPDATE runtime.user_memory SET expires_at = statement_timestamp() - interval '1 hour' "
                "WHERE tenant_id = :tenant_id AND user_id = :user_id AND memory_key = :memory_key"
            ),
            {"tenant_id": tenant_id, "user_id": uuid.UUID(user_id), "memory_key": memory_key},
        )
        await session.commit()


async def _create_user(client: AsyncClient, tenant: TenantContext) -> str:
    created = await client.post(
        "/api/v1/users",
        json={"user_code": f"user-{uuid.uuid4().hex[:8]}", "display_name": "Memory Writer"},
        headers=_headers(tenant),
    )
    assert created.status_code == 200
    return created.json()["data"]["id"]


async def _create_memory(
    client: AsyncClient,
    tenant: TenantContext,
    user_id: str,
    *,
    command_id: str,
    memory_key: str = "pref-answer",
    value: str = "concise answers",
    **extra: Any,
) -> httpx.Response:
    payload: dict[str, Any] = {"memory_key": memory_key, "category": "PREFERENCE", "value": value}
    payload.update(extra)
    return await client.post(
        f"/api/v1/users/{user_id}/memory", json=payload, headers=_headers(tenant, command_id)
    )


async def _patch_memory(
    client: AsyncClient,
    tenant: TenantContext,
    user_id: str,
    *,
    command_id: str,
    memory_key: str = "pref-answer",
    **body: Any,
) -> httpx.Response:
    return await client.patch(
        f"/api/v1/users/{user_id}/memory/{memory_key}",
        json=body,
        headers=_headers(tenant, command_id),
    )


async def _delete_memory(
    client: AsyncClient,
    tenant: TenantContext,
    user_id: str,
    *,
    command_id: str,
    memory_id: str,
    expected_version: int,
) -> httpx.Response:
    return await client.delete(
        f"/api/v1/users/{user_id}/memory/{memory_id}",
        params={"expected_version": expected_version},
        headers=_headers(tenant, command_id),
    )


def _created_item(response: httpx.Response) -> dict[str, Any]:
    items = response.json()["data"]["items"]
    assert len(items) == 1
    return items[0]


async def test_local_01_same_command_replays_without_second_effect(
    client: AsyncClient, tenant: TenantContext
) -> None:
    user_id = await _create_user(client, tenant)
    command_id = uuid.uuid4().hex
    with RuntimeSqlCapture() as capture:
        first = await _create_memory(client, tenant, user_id, command_id=command_id)
        assert first.status_code == 200, first.text
        data = first.json()["data"]
        assert data["command_id"] == command_id, "Runtime 命令号必须就是 Console 收到的 Idempotency-Key"
        assert data["status"] == "APPLIED" and data["changed_count"] == 1
        item = _created_item(first)
        assert item["memory_key"] == "pref-answer" and item["version"] == 1
        assert item["status"] == "CREATED" and item["reason"] is None
        assert data["forget_epoch"] == 0 and data["scope_revision"] >= 1

        replay = await _create_memory(client, tenant, user_id, command_id=command_id)
        assert replay.status_code == 200
        assert replay.json()["data"] == data, "同 command_id 重放必须返回首次回执"

        mismatch = await _create_memory(
            client, tenant, user_id, command_id=command_id, value="another value"
        )
        assert mismatch.status_code == 409
        assert mismatch.json()["code"] == "IDEMPOTENCY_MISMATCH"

        assert await _counts(tenant.tenant_id, user_id) == Counts(
            memories=1, live=1, history=1, receipts=1, audits=2, forget_epoch=0
        ), "同命令重放不得产生第二次效果（本地操作日志按 Console 收到的成功请求计数，靠 command_id 关联）"
    memory_statements = capture.statements
    assert memory_statements, "监听器必须真的看到 Console 对 runtime 记忆表的访问"
    lowered = [statement.lower().lstrip() for statement in memory_statements]
    assert all(statement.startswith(("select", "with")) for statement in lowered), (
        "Console 不得直接写 runtime 记忆表：" + "; ".join(statement[:80] for statement in memory_statements)
    )


async def test_local_02_version_conflict_and_missing_memory_pass_through(
    client: AsyncClient, tenant: TenantContext
) -> None:
    user_id = await _create_user(client, tenant)
    created = await _create_memory(client, tenant, user_id, command_id=uuid.uuid4().hex)
    assert created.status_code == 200, created.text
    memory_id = _created_item(created)["id"]

    overwrite_without_version = await _create_memory(
        client, tenant, user_id, command_id=uuid.uuid4().hex, value="overwritten"
    )
    assert overwrite_without_version.status_code == 409
    assert overwrite_without_version.json()["code"] == "MEMORY_VERSION_CONFLICT"
    assert (await _memory_row(tenant.tenant_id, user_id, "pref-answer"))["content_json"] == {
        "value": "concise answers"
    }, "版本冲突不覆盖"

    stale_patch = await _patch_memory(
        client, tenant, user_id, command_id=uuid.uuid4().hex, enabled=False, expected_version=99
    )
    assert stale_patch.status_code == 409
    assert stale_patch.json()["code"] == "MEMORY_VERSION_CONFLICT"
    row = await _memory_row(tenant.tenant_id, user_id, "pref-answer")
    assert row["enabled"] is True and row["version"] == 1

    unknown = await _delete_memory(
        client,
        tenant,
        user_id,
        command_id=uuid.uuid4().hex,
        memory_id=str(uuid.uuid4()),
        expected_version=1,
    )
    assert unknown.status_code == 404
    assert unknown.json()["code"] == "MEMORY_NOT_FOUND"

    stale_delete = await _delete_memory(
        client, tenant, user_id, command_id=uuid.uuid4().hex, memory_id=memory_id, expected_version=7
    )
    assert stale_delete.status_code == 409
    assert stale_delete.json()["code"] == "MEMORY_VERSION_CONFLICT"
    assert await _counts(tenant.tenant_id, user_id) == Counts(
        memories=1, live=1, history=1, receipts=1, audits=1, forget_epoch=0
    ), "失败的写不得产生事实、历史、回执或成功的本地审计"


async def test_local_03_enable_disable_and_expiry_semantics(
    client: AsyncClient, tenant: TenantContext
) -> None:
    user_id = await _create_user(client, tenant)
    created = await _create_memory(
        client,
        tenant,
        user_id,
        command_id=uuid.uuid4().hex,
        expires_at=(datetime.now(UTC) + timedelta(days=3)).isoformat(),
    )
    assert created.status_code == 200, created.text

    disabled = await _patch_memory(
        client, tenant, user_id, command_id=uuid.uuid4().hex, enabled=False, expected_version=1
    )
    assert disabled.status_code == 200, disabled.text
    assert _created_item(disabled)["status"] == "DISABLED"
    assert _created_item(disabled)["version"] == 2
    assert (await _memory_row(tenant.tenant_id, user_id, "pref-answer"))["enabled"] is False

    new_expiry = datetime.now(UTC) + timedelta(days=1)
    enabled = await _patch_memory(
        client,
        tenant,
        user_id,
        command_id=uuid.uuid4().hex,
        enabled=True,
        expected_version=2,
        expires_at=new_expiry.isoformat(),
    )
    assert enabled.status_code == 200, enabled.text
    assert _created_item(enabled)["status"] == "ENABLED"
    row = await _memory_row(tenant.tenant_id, user_id, "pref-answer")
    assert row["enabled"] is True and row["version"] == 3
    assert row["expires_at"] < new_expiry + timedelta(minutes=1), "启用时给出的新有效期必须落到事实"

    past = await _patch_memory(
        client,
        tenant,
        user_id,
        command_id=uuid.uuid4().hex,
        enabled=True,
        expected_version=3,
        expires_at=(datetime.now(UTC) - timedelta(hours=1)).isoformat(),
    )
    assert past.status_code == 400
    assert past.json()["code"] == "MEMORY_VALUE_INVALID", "已经过去的时间不能当有效期（过期语义明确）"


async def test_local_04_expired_entry_requires_new_expiry_and_clear_is_one_command(
    client: AsyncClient, tenant: TenantContext
) -> None:
    user_id = await _create_user(client, tenant)
    for index in range(3):
        created = await _create_memory(
            client,
            tenant,
            user_id,
            command_id=uuid.uuid4().hex,
            memory_key=f"pref-{index}",
            value=f"value {index}",
        )
        assert created.status_code == 200, created.text
    await _expire_now(tenant.tenant_id, user_id, "pref-0")

    without_expiry = await _patch_memory(
        client,
        tenant,
        user_id,
        command_id=uuid.uuid4().hex,
        memory_key="pref-0",
        enabled=True,
        expected_version=1,
    )
    assert without_expiry.status_code == 400
    assert without_expiry.json()["code"] == "MEMORY_VALUE_INVALID", "过期条目启用必须显式给新有效期"

    revived = await _patch_memory(
        client,
        tenant,
        user_id,
        command_id=uuid.uuid4().hex,
        memory_key="pref-0",
        enabled=True,
        expected_version=1,
        expires_at=(datetime.now(UTC) + timedelta(days=2)).isoformat(),
    )
    assert revived.status_code == 200, revived.text
    row = await _memory_row(tenant.tenant_id, user_id, "pref-0")
    # 过期条目先按 EXPIRE 退休、再按显式 ENABLE 生效：两条真实变更 ⇒ version 1 → 3。
    assert row["enabled"] is True and row["version"] == 3
    assert row["expires_at"] > datetime.now(UTC)

    cleared = await client.delete(
        f"/api/v1/users/{user_id}/memory", headers=_headers(tenant, uuid.uuid4().hex)
    )
    assert cleared.status_code == 200, cleared.text
    data = cleared.json()["data"]
    assert data["changed_count"] == 3 and data["status"] == "APPLIED"
    assert sorted(item["status"] for item in data["items"]) == ["DELETED", "DELETED", "DELETED"]
    counts = await _counts(tenant.tenant_id, user_id)
    assert (counts.memories, counts.live) == (3, 0), "一命令原子清空"
    assert counts.history == 8, "3 建 + 过期退休/显式启用 2 + 清空 3，每次变更都有完整历史"
    assert counts.receipts == 5, "清空只写一个回执"
    assert counts.forget_epoch == 1, "管理员删除递增 forget_epoch"
    assert counts.audits == 5

    again = await client.delete(
        f"/api/v1/users/{user_id}/memory", headers=_headers(tenant, uuid.uuid4().hex)
    )
    assert again.status_code == 200
    assert again.json()["data"]["changed_count"] == 0
    assert again.json()["data"]["status"] == "UNCHANGED"
    assert (await _counts(tenant.tenant_id, user_id)).history == 8, "空清空不得再写历史"


async def test_local_05_unreachable_or_forbidden_runtime_never_reports_success(
    client: AsyncClient,
    tenant: TenantContext,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = await _create_user(client, tenant)

    # 开发机/CI 的系统级 HTTP 代理会把 127.0.0.1 也代理出去，NO_PROXY 才走真实 TCP 连接失败。
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    unreachable = RuntimeMemoryClient("http://127.0.0.1:1", service_token=TOKEN)
    try:
        console_app.dependency_overrides[get_runtime_memory_client] = lambda: unreachable
        with caplog.at_level(logging.WARNING):
            response = await _create_memory(client, tenant, user_id, command_id=uuid.uuid4().hex)
    finally:
        await unreachable.aclose()
    assert response.status_code == 500
    assert response.json()["code"] == "COMMON_INTERNAL_ERROR"
    assert response.json()["data"] is None
    assert "runtime_memory_request_failed" in caplog.text
    assert await _counts(tenant.tenant_id, user_id) == EMPTY, "Runtime 不可达时 Console 不得落成功态"

    rejected = _runtime_client_for("wrong-service-token")
    try:
        console_app.dependency_overrides[get_runtime_memory_client] = lambda: rejected
        forbidden = await _create_memory(client, tenant, user_id, command_id=uuid.uuid4().hex)
    finally:
        await rejected.aclose()
    assert forbidden.status_code == 403
    assert forbidden.json()["code"] == "FORBIDDEN"
    assert await _counts(tenant.tenant_id, user_id) == EMPTY, (
        "被 Runtime 服务身份拒绝的命令不得产生事实，也不得记成功审计"
    )


async def test_local_06_missing_runtime_configuration_fails_explicitly(
    client: AsyncClient,
    tenant: TenantContext,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = await _create_user(client, tenant)
    console_app.dependency_overrides.pop(get_runtime_memory_client, None)
    # 空值也覆盖仓库 `.env`（与部署里 `INTERNAL_SERVICE_TOKEN=` 的形态一致）。
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", "")
    monkeypatch.setenv("AGENT_RUNTIME_URL", "")
    with caplog.at_level(logging.WARNING):
        response = await _create_memory(client, tenant, user_id, command_id=uuid.uuid4().hex)
    assert response.status_code == 500
    assert response.json()["code"] == "COMMON_INTERNAL_ERROR"
    assert "INTERNAL_SERVICE_TOKEN" in caplog.text, "缺 Runtime 连接配置必须明确指名缺的是哪一项"
    assert "AGENT_RUNTIME_URL" in caplog.text
    assert await _counts(tenant.tenant_id, user_id) == EMPTY


async def test_local_07_console_dto_whitelist_and_tenant_scope(
    client: AsyncClient, tenant: TenantContext
) -> None:
    user_id = await _create_user(client, tenant)
    invalid_payloads: dict[str, dict[str, Any]] = {
        "author": {"author": "ADMIN"},
        "task_state_category": {"category": "TASK_STATE"},
        "task_namespace_key": {"memory_key": f"task.{uuid.uuid4().hex}"},
        "blank_value": {"value": "   "},
    }
    for name, override in invalid_payloads.items():
        rejected = await _create_memory(client, tenant, user_id, command_id=uuid.uuid4().hex, **override)
        assert rejected.status_code == 422, f"{name} 必须在 Console 边界被拒绝"
        assert rejected.json()["code"] == "COMMON_VALIDATION_ERROR"

    created = await _create_memory(client, tenant, user_id, command_id=uuid.uuid4().hex)
    assert created.status_code == 200, created.text
    memory_id = _created_item(created)["id"]

    no_command = await client.post(
        f"/api/v1/users/{user_id}/memory",
        json={"memory_key": "pref-no-command", "category": "PREFERENCE", "value": "v"},
        headers=_headers(tenant),
    )
    assert no_command.status_code == 422 and no_command.json()["code"] == "COMMON_VALIDATION_ERROR"

    patch_without_version = await _patch_memory(
        client, tenant, user_id, command_id=uuid.uuid4().hex, enabled=False
    )
    assert patch_without_version.status_code == 422

    delete_without_version = await client.delete(
        f"/api/v1/users/{user_id}/memory/{memory_id}", headers=_headers(tenant, uuid.uuid4().hex)
    )
    assert delete_without_version.status_code == 422

    disable_with_expiry = await _patch_memory(
        client,
        tenant,
        user_id,
        command_id=uuid.uuid4().hex,
        enabled=False,
        expected_version=1,
        expires_at=(datetime.now(UTC) + timedelta(days=1)).isoformat(),
    )
    assert disable_with_expiry.status_code == 422, "禁用不得携带新有效期"

    unknown_user = str(uuid.uuid4())
    foreign = await _create_memory(client, tenant, unknown_user, command_id=uuid.uuid4().hex)
    assert foreign.status_code == 404
    assert foreign.json()["code"] == "COMMON_NOT_FOUND"
    assert unknown_user not in foreign.text
    assert await _counts(tenant.tenant_id, unknown_user) == EMPTY, (
        "租户内不存在用户时不得发出 Runtime 命令"
    )

    listed = await client.get(f"/api/v1/users/{user_id}/memory", headers=_headers(tenant))
    assert listed.status_code == 200
    assert listed.json()["data"]["total"] == 1
