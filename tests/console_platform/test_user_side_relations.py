import json
import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from muad_console_platform.application.grant_service import GrantService
from muad_console_platform.application.memory_service import MemoryService
from muad_console_platform.infrastructure.db import get_session_factory
from sqlalchemy import text

from console_platform.conftest import TenantContext
from console_platform.test_users_api import _cleanup_seeded, _headers, _payload


async def _create_user(client: AsyncClient, tenant: TenantContext) -> str:
    created = await client.post("/api/v1/users", json=_payload(), headers=_headers(tenant))
    return created.json()["data"]["id"]


async def _create_agent(client: AsyncClient, tenant: TenantContext) -> dict[str, Any]:
    agent = await client.post(
        "/api/v1/agents",
        json={
            "key": f"agent-{uuid.uuid4().hex[:8]}",
            "name": "Side Relation Agent",
            "instructions": "You are helpful.",
            "model_id": str(tenant.model_id),
            "runtime_config": {},
        },
        headers=_headers(tenant),
    )
    return agent.json()["data"]


async def _seed_memory(tenant: TenantContext, user_id: str, count: int) -> list[str]:
    session_factory = get_session_factory()
    memory_ids: list[str] = []
    async with session_factory() as session:
        for index in range(count):
            memory_id = uuid.uuid4()
            memory_ids.append(str(memory_id))
            # 内容形状要与真实写入一致（Runtime 读回时会按 `MemoryCandidate` 校验）。
            await session.execute(
                text(
                    "INSERT INTO runtime.user_memory "
                    "(id, tenant_id, user_id, memory_key, category, content_json, author) "
                    "VALUES (:id, :tenant_id, :user_id, :memory_key, 'PREFERENCE', "
                    "CAST(:content AS jsonb), 'ADMIN')"
                ),
                {
                    "id": memory_id,
                    "tenant_id": tenant.tenant_id,
                    "user_id": user_id,
                    "memory_key": f"memory-{index}-{uuid.uuid4().hex[:6]}",
                    "content": json.dumps({"value": f"seeded value {index}"}),
                },
            )
        await session.commit()
    return memory_ids


async def test_s04_grant_and_revoke_share_application_service(
    client: AsyncClient, tenant: TenantContext
) -> None:
    user_id = await _create_user(client, tenant)
    agent = await _create_agent(client, tenant)
    try:
        granted = await client.post(
            f"/api/v1/users/{user_id}/agents/{agent['id']}", headers=_headers(tenant)
        )
        assert granted.status_code == 200
        grant = granted.json()["data"]
        assert grant["user_id"] == user_id
        assert grant["agent_id"] == agent["id"]
        assert grant["granted_at"]
        assert grant["granted_by"]

        session_factory = get_session_factory()
        async with session_factory() as session:
            items, total = await GrantService(session).list_by_user(
                tenant.tenant_id, uuid.UUID(user_id), 1, 20
            )
        assert total == 1
        assert str(items[0]["agent_id"]) == agent["id"]

        listing = await client.get(f"/api/v1/users/{user_id}/agents", headers=_headers(tenant))
        assert listing.json()["data"]["total"] == 1

        revoked = await client.delete(
            f"/api/v1/users/{user_id}/agents/{agent['id']}", headers=_headers(tenant)
        )
        assert revoked.status_code == 200
        assert revoked.json()["data"]["revoked"] is True
        empty = await client.get(f"/api/v1/users/{user_id}/agents", headers=_headers(tenant))
        assert empty.json()["data"]["total"] == 0

        regranted = await client.post(
            f"/api/v1/users/{user_id}/agents/{agent['id']}", headers=_headers(tenant)
        )
        assert regranted.status_code == 200
        again = await client.get(f"/api/v1/users/{user_id}/agents", headers=_headers(tenant))
        assert again.json()["data"]["total"] == 1

        async with session_factory() as session:
            audit_count = await session.scalar(
                text(
                    "SELECT count(*) FROM control.config_audit_log "
                    "WHERE resource_id = :rid AND resource_type = 'AGENT_ACCESS_GRANT'"
                ),
                {"rid": uuid.UUID(agent["id"])},
            )
        assert audit_count == 3
    finally:
        await _cleanup_seeded(tenant.tenant_id)


async def test_s04_unknown_agent_or_user_returns_not_found(
    client: AsyncClient, tenant: TenantContext
) -> None:
    user_id = await _create_user(client, tenant)
    agent = await _create_agent(client, tenant)
    unknown_agent = await client.post(
        f"/api/v1/users/{user_id}/agents/{uuid.uuid4()}", headers=_headers(tenant)
    )
    assert unknown_agent.status_code == 404
    assert unknown_agent.json()["code"] == "AGENT_NOT_FOUND"

    unknown_user = await client.post(
        f"/api/v1/users/{uuid.uuid4()}/agents/{uuid.uuid4()}", headers=_headers(tenant)
    )
    assert unknown_user.status_code == 404

    try:
        # Agent 存在但无 ACTIVE grant：走 API-07 的 COMMON_NOT_FOUND 分支（非 AGENT_NOT_FOUND）
        revoke_without_grant = await client.delete(
            f"/api/v1/users/{user_id}/agents/{agent['id']}", headers=_headers(tenant)
        )
        assert revoke_without_grant.status_code == 404
        assert revoke_without_grant.json()["code"] == "COMMON_NOT_FOUND"

        granted = await client.post(
            f"/api/v1/users/{user_id}/agents/{agent['id']}", headers=_headers(tenant)
        )
        assert granted.status_code == 200
        assert (
            await client.delete(
                f"/api/v1/users/{user_id}/agents/{agent['id']}", headers=_headers(tenant)
            )
        ).status_code == 200

        revoked_again = await client.delete(
            f"/api/v1/users/{user_id}/agents/{agent['id']}", headers=_headers(tenant)
        )
        assert revoked_again.status_code == 404
        assert revoked_again.json()["code"] == "COMMON_NOT_FOUND"
    finally:
        await _cleanup_seeded(tenant.tenant_id)


@pytest.mark.usefixtures("runtime_memory_override")
async def test_s05_memory_delete_and_clear_share_application_service(
    client: AsyncClient, tenant: TenantContext
) -> None:
    user_id = await _create_user(client, tenant)
    memory_ids = await _seed_memory(tenant, user_id, 2)
    try:
        session_factory = get_session_factory()
        async with session_factory() as session:
            _, total = await MemoryService(session).list_by_user(
                tenant.tenant_id, uuid.UUID(user_id), 1, 20
            )
        assert total == 2

        delete_command = uuid.uuid4().hex
        removed = await client.delete(
            f"/api/v1/users/{user_id}/memory/{memory_ids[0]}",
            params={"expected_version": 1},
            headers={**_headers(tenant), "Idempotency-Key": delete_command},
        )
        assert removed.status_code == 200
        assert removed.json()["data"]["command_id"] == delete_command
        assert [item["status"] for item in removed.json()["data"]["items"]] == ["DELETED"]

        replayed = await client.delete(
            f"/api/v1/users/{user_id}/memory/{memory_ids[0]}",
            params={"expected_version": 1},
            headers={**_headers(tenant), "Idempotency-Key": delete_command},
        )
        assert replayed.json()["data"] == removed.json()["data"], "同命令重放返回首次回执"

        remaining = await client.get(f"/api/v1/users/{user_id}/memory", headers=_headers(tenant))
        assert remaining.json()["data"]["total"] == 1

        clear_command = uuid.uuid4().hex
        cleared = await client.delete(
            f"/api/v1/users/{user_id}/memory",
            headers={**_headers(tenant), "Idempotency-Key": clear_command},
        )
        assert cleared.status_code == 200
        assert cleared.json()["data"]["changed_count"] == 1

        empty = await client.get(f"/api/v1/users/{user_id}/memory", headers=_headers(tenant))
        assert empty.json()["data"]["total"] == 0

        cleared_again = await client.delete(
            f"/api/v1/users/{user_id}/memory",
            headers={**_headers(tenant), "Idempotency-Key": uuid.uuid4().hex},
        )
        assert cleared_again.json()["data"]["changed_count"] == 0

        async with session_factory() as session:
            audit_after = list(
                await session.scalars(
                    text(
                        "SELECT after_json FROM control.config_audit_log "
                        "WHERE tenant_id = :tenant_id AND resource_type = 'USER_MEMORY' "
                        "AND resource_id = :user_id ORDER BY create_time"
                    ),
                    {"tenant_id": tenant.tenant_id, "user_id": uuid.UUID(user_id)},
                )
            )
            receipts = await session.scalar(
                text(
                    "SELECT count(*) FROM runtime.memory_command_receipt "
                    "WHERE tenant_id = :tenant_id AND user_id = :user_id"
                ),
                {"tenant_id": tenant.tenant_id, "user_id": uuid.UUID(user_id)},
            )
            history_actors = list(
                await session.scalars(
                    text(
                        "SELECT DISTINCT actor_type || ':' || command_id FROM runtime.user_memory_history "
                        "WHERE tenant_id = :tenant_id AND user_id = :user_id"
                    ),
                    {"tenant_id": tenant.tenant_id, "user_id": uuid.UUID(user_id)},
                )
            )
        cleared_again_command = cleared_again.json()["data"]["command_id"]
        assert sorted(entry["command_id"] for entry in audit_after) == sorted(
            [delete_command, delete_command, clear_command, cleared_again_command]
        ), "本地操作日志按 Console 收到的每个成功请求记一条，并用 command_id 关联远端事实（重放也记）"
        assert receipts == 3, "三条命令各一份 Runtime 回执（同命令重放不再新增）"
        assert sorted(history_actors) == sorted(
            [f"ADMIN:{delete_command}", f"ADMIN:{clear_command}"]
        ), "权威 actor/command 在 Runtime history，Console 不另写一份事实"
    finally:
        await _cleanup_seeded(tenant.tenant_id)
