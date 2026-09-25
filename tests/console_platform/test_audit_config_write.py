"""[S-04 / E-04 / RULE-07] 配置审计与业务变更同事务写入、actor 语义与回滚不落库。

不得 Mock 的真实边界：真实 Console HTTP（ASGI 全栈 + 登录会话/CSRF）→ 真实 PostgreSQL
（`control.config_audit_log` 与 `control.agent_definition` 逐行回读）。验收类不制造 RED。
"""

from __future__ import annotations

import uuid
from typing import Any

from httpx import AsyncClient
from muad_common import SharedSettings
from muad_console_platform.application.audit_service import AuditActor
from muad_console_platform.application.agent_service import AgentService
from muad_console_platform.application.dto import AgentCreateRequest
from muad_console_platform.infrastructure.db import get_session_factory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from console_platform.conftest import TenantContext


def _headers(tenant: TenantContext) -> dict[str, str]:
    return {"X-Tenant-Id": tenant.tenant_id}


def _payload(tenant: TenantContext, key: str) -> dict[str, object]:
    return {
        "key": key,
        "name": "Audit Agent",
        "description": "config audit acceptance",
        "instructions": "You are helpful.",
        "model_id": str(tenant.model_id),
        "runtime_config": {"temperature": 0.2},
    }


async def _rows(statement: str, params: dict[str, object]) -> list[tuple[Any, ...]]:
    engine = create_async_engine(SharedSettings().require_database_url())
    try:
        async with engine.connect() as connection:
            return list((await connection.execute(text(statement), params)).all())
    finally:
        await engine.dispose()


async def _scalar(statement: str, params: dict[str, object]) -> Any:
    rows = await _rows(statement, params)
    return rows[0][0] if rows else None


async def _account_id(tenant: TenantContext) -> str:
    """测试租户内由 client fixture 创建的登录账号（唯一）。"""
    account_id = await _scalar(
        "SELECT id::text FROM control.console_account WHERE tenant_id = :t ORDER BY create_time LIMIT 1",
        {"t": tenant.tenant_id},
    )
    assert account_id, "租户内未找到登录账号"
    return str(account_id)


async def test_s04_config_audit_shares_business_transaction(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """S-04：真实 Console HTTP 变更 Agent 配置 → 审计与业务同事务提交，actor 为登录账号。"""
    key = f"audit-{uuid.uuid4().hex[:8]}"
    created = await client.post("/api/v1/agents", json=_payload(tenant, key), headers=_headers(tenant))
    assert created.status_code == 200, created.text
    agent = created.json()["data"]

    updated = await client.put(
        f"/api/v1/agents/{agent['id']}",
        json={"instructions": "updated instructions", "expected_revision": 1},
        headers=_headers(tenant),
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["data"]["revision"] == 2

    # 业务行与审计行均可在提交后读到（同一次提交）
    business = await _scalar(
        "SELECT revision FROM control.agent_definition WHERE tenant_id = :t AND id = :id",
        {"t": tenant.tenant_id, "id": agent["id"]},
    )
    assert int(business or 0) == 2

    rows = await _rows(
        "SELECT actor_user_id::text, action, resource_type, resource_id::text, after_json::text "
        "FROM control.config_audit_log WHERE tenant_id = :t AND resource_id = :id "
        "ORDER BY create_time",
        {"t": tenant.tenant_id, "id": agent["id"]},
    )
    assert rows, "变更未写入 config_audit_log"
    account_id = await _account_id(tenant)
    actions = [row[1] for row in rows]
    assert "CREATE" in actions and "UPDATE" in actions, actions
    for actor_user_id, _action, resource_type, resource_id, _after in rows:
        assert actor_user_id == account_id, "actor_user_id 必须是登录的 console_account.id"
        assert resource_type == "AGENT"
        assert resource_id == agent["id"]
    # 差异快照存在且不含 Secret
    assert rows[-1][4] and "updated instructions" in rows[-1][4]


async def test_e04_rolled_back_business_change_leaves_no_audit(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """E-04：业务事务回滚 → config_audit_log 不产生记录（同事务语义）。"""
    account_id = uuid.UUID(await _account_id(tenant))
    key = f"rollback-{uuid.uuid4().hex[:8]}"
    session_factory = get_session_factory()
    async with session_factory() as session:
        service = AgentService(session)
        created = await service.create_agent(
            tenant.tenant_id,
            AgentCreateRequest(
                key=key,
                name="Rollback Agent",
                instructions="You are helpful.",
                model_id=tenant.model_id,
                runtime_config={"temperature": 0.1},
            ),
            AuditActor(account_id=account_id),
        )
        agent_id = str(created.id)
        # 同一事务内已可见（证明审计 INSERT 属于业务事务，而非独立提交）
        inside = await session.scalar(
            text("SELECT count(*) FROM control.config_audit_log WHERE tenant_id = :t AND resource_id = :id"),
            {"t": tenant.tenant_id, "id": agent_id},
        )
        assert int(inside or 0) == 1, "审计未在业务事务内写入"
        await session.rollback()

    # 回滚后：业务变更与审计记录都不存在
    assert (
        await _scalar(
            "SELECT count(*) FROM control.agent_definition WHERE tenant_id = :t AND key = :k",
            {"t": tenant.tenant_id, "k": key},
        )
        == 0
    )
    assert (
        await _scalar(
            "SELECT count(*) FROM control.config_audit_log WHERE tenant_id = :t AND resource_id = :id",
            {"t": tenant.tenant_id, "id": agent_id},
        )
        == 0
    ), "业务回滚后审计仍被持久化（同事务语义被破坏）"
