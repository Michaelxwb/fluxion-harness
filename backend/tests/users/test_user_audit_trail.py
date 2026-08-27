"""TASK-021 Audit/Trace 收尾验收测试。

规则 24「日志不等于 Audit」：User Domain 高影响变更（Profile 更新/偏好更新/
授权授予/授权撤销）必须进独立 AuditLog，且载荷不含敏感明文。
Trace 关联（request_id/trace_id 贯穿）由 test_trace.py / test_agent_test_run.py
既有用例承载，本文件引用不再重复造测。
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from fluxion.api.console import create_app as create_console_app
from fluxion.registry import SQLiteRegistryStore
from fluxion.registry.schema import audit_logs
from fluxion.resources import ResourceDefinition, ResourceKind, ResourceStatus
from fluxion.services.console_app import ConsoleApplicationService
from fluxion.users import UserDomainService


async def _admin_client(store: SQLiteRegistryStore):
    console = ConsoleApplicationService(store)
    users = UserDomainService(store)
    app = create_console_app(console, user_service=users)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://console")


def _headers(request_id: str) -> dict[str, str]:
    return {
        "X-Tenant-ID": "tenant-a",
        "X-Actor-ID": "admin-a",
        "X-Request-ID": request_id,
        "X-Trace-ID": f"trace-{request_id}",
    }


@pytest.mark.asyncio
async def test_be_s_08_extension_user_mutations_write_audit_rows() -> None:
    store = SQLiteRegistryStore("sqlite+aiosqlite:///:memory:")
    await store.initialize()
    async with await _admin_client(store) as client:
        await client.post(
            "/admin/users",
            json={"platform_user_id": "u-a1", "display_name": "审计用户"},
            headers=_headers("req-audit-create"),
        )
        profile = await client.put(
            "/admin/users/u-a1/profile",
            json={"display_name": "昵称·审计"},
            headers=_headers("req-audit-profile"),
        )
        assert profile.status_code == 200
        prefs = await client.put(
            "/admin/users/u-a1/preferences",
            json={"theme": "dark", "notification_enabled": False},
            headers=_headers("req-audit-pref"),
        )
        assert prefs.status_code == 200
        grant = await client.post(
            "/admin/users/u-a1/grants",
            json={
                "type": "mcp",
                "capability_ref": "weather",
                "version_pin": "1",
                "granted_scope": "invoke",
            },
            headers=_headers("req-audit-grant"),
        )
        assert grant.status_code == 200
        revoke = await client.delete(
            "/admin/users/u-a1/grants/weather",
            headers=_headers("req-audit-revoke"),
        )
        assert revoke.status_code == 200 and revoke.json()["data"]["revoked"] == 1

    async with store.engine.connect() as conn:
        rows = (
            await conn.execute(select(audit_logs).order_by(audit_logs.c.audit_id))
        ).mappings().all()

    actions = [r["action"] for r in rows]
    for expected in (
        "user.profile.update",
        "user.preference.update",
        "user.capability.grant",
        "user.capability.revoke",
    ):
        assert expected in actions, f"AuditLog 缺 {expected} 记录"
    # 独立 Audit 记录必须可追溯到请求与执行者。
    for r in rows:
        if r["action"].startswith("user."):
            assert r["actor_id"] == "admin-a"
            assert r["request_id"].startswith("req-audit-")
            assert r["tenant_id"] == "tenant-a"
    grant_row = next(r for r in rows if r["action"] == "user.capability.grant")
    assert grant_row["target_id"] == "u-a1"
    assert grant_row["after_json"]["capability_ref"] == "weather"
    await store.close()


@pytest.mark.asyncio
async def test_user_360_activity_region_backed_by_audit_log() -> None:
    """360 Activity 区的数据源就是独立 AuditLog（非普通日志）。"""
    store = SQLiteRegistryStore("sqlite+aiosqlite:///:memory:")
    await store.initialize()
    async with await _admin_client(store) as client:
        await client.post(
            "/admin/users",
            json={"platform_user_id": "u-a2", "display_name": "活动用户"},
            headers=_headers("req-act-create"),
        )
        await client.put(
            "/admin/users/u-a2/profile",
            json={"display_name": "活动昵称"},
            headers=_headers("req-act-profile"),
        )
        view = await client.get("/admin/users/u-a2/360", headers=_headers("req-act-360"))
        assert view.status_code == 200
        data = view.json()["data"]
        assert data["activity_count"] >= 1, "Activity 区应至少包含 profile 变更审计"

    async with store.engine.connect() as conn:
        n_rows = len((await conn.execute(select(audit_logs.c.action))).all())
    assert n_rows >= 2  # create + profile.update 至少两条
    await store.close()


@pytest.mark.asyncio
async def test_agent_publish_still_writes_governance_audit_row() -> None:
    """回归哨兵：Studio 治理发布链的独立 Audit 不因 A105 改名丢失。"""
    from tests.console_helpers import console_stack, tenant_headers

    async with console_stack() as stack:
        from fluxion.agents.definitions import AgentDefinition

        draft = ResourceDefinition(
            kind=ResourceKind.AGENT_DEFINITION, id="assistant", tenant_id="tenant-a",
            version="1", status=ResourceStatus.DRAFT,
            spec_json=AgentDefinition(name="assistant", system_prompt="哨兵。",
                                      owner="fixture",
                                      model_ref={"id":"dev.echo","version":"1"}).model_dump(mode="json"),
        )
        await stack.store.put(draft)
        pub = await stack.client.post(
            "/studio/agents/assistant/versions/1:publish",
            headers=tenant_headers(request_id="req-agent-publish"),
        )
        assert pub.status_code == 200

        rows, total = await stack.store.list_audit(
            tenant_id="tenant-a", offset=0, limit=10
        )
        publish_rows = [
            r for r in rows if r.action == "publish" and r.target_id == "assistant"
        ]
        assert total >= 1 and publish_rows, "AgentDefinition 发布审计缺失"
        assert publish_rows[0].target_type == "agent_definition"
        # 审计行必须关联发行请求与执行者。
        assert publish_rows[0].request_id == "req-agent-publish"
        assert publish_rows[0].actor_id == "admin-a"
