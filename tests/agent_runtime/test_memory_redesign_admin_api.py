"""LOCAL: real protected HTTP, scoped PG writer, permanent receipts and pagination."""

from contextlib import asynccontextmanager
from importlib import import_module
from uuid import uuid4

import httpx
import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from muad_agent_runtime.infrastructure.models.runtime import UserMemory, UserMemoryHistory
from muad_api import install_api_foundation
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.agent_runtime.memory_confirmation_resume_support import serve

pytestmark = pytest.mark.integration


@asynccontextmanager
async def admin_stack(engine, monkeypatch):
    module = import_module("muad_agent_runtime.api.memory")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", "admin-test-token")
    app = FastAPI()
    install_api_foundation(app)
    from muad_agent_runtime.application.ports import NullPlatformSettingsClient

    app.state.platform_settings_client = NullPlatformSettingsClient()
    app.include_router(module.router)

    async def database():
        async with factory() as session:
            yield session

    app.dependency_overrides[module.get_session] = database
    tenant, user, actor = "admin-api", uuid4(), uuid4()
    headers = {
        "X-Internal-Service": "admin-test-token",
        "X-Tenant-Id": tenant,
        "Idempotency-Key": uuid4().hex,
    }
    async with serve(app) as url, httpx.AsyncClient(base_url=url) as client:
        yield client, factory, user, actor, headers


def create_body(actor, **changes):
    return {
        "actor_id": str(actor),
        "mutation": {
            "action": "UPSERT",
            "memory_key": "color",
            "category": "PREFERENCE",
            "value": "blue",
            **changes,
        },
    }


async def post_mutation(client, user, actor, headers, **mutation):
    return await client.post(
        f"/internal/admin/users/{user}/memory/commands",
        headers={**headers, "Idempotency-Key": uuid4().hex},
        json={"actor_id": str(actor), "mutation": mutation},
    )


async def test_local_admin_live_service_credential_is_forbidden_and_never_echoed(async_tool_database, monkeypatch):
    async with admin_stack(async_tool_database, monkeypatch) as (client, factory, user, actor, headers):
        response = await client.post(f"/internal/admin/users/{user}/memory/commands", headers=headers,
            json=create_body(actor, value=headers["X-Internal-Service"]))
        assert response.status_code == 422 and response.json()["code"] == "MEMORY_FORBIDDEN_CONTENT"
        assert headers["X-Internal-Service"] not in response.text
        async with factory() as session:
            assert await session.scalar(sa.select(sa.func.count()).select_from(UserMemory)) == 0
            assert await session.scalar(sa.select(sa.func.count()).select_from(UserMemoryHistory)) == 0


async def test_local_admin_delete_replay_keeps_history_and_paged_actor_identity(
    async_tool_database, monkeypatch
):
    async with admin_stack(async_tool_database, monkeypatch) as (client, factory, user, actor, headers):
        path = f"/internal/admin/users/{user}/memory"
        created = (await client.post(path + "/commands", headers=headers, json=create_body(actor))).json()[
            "data"
        ]
        memory_id = created["items"][0]["id"]
        disabled = await post_mutation(
            client, user, actor, headers, action="DISABLE", memory_key="color", expected_version=1
        )
        assert disabled.status_code == 200 and disabled.json()["data"]["items"][0]["version"] == 2
        listed = (await client.get(path, headers=headers, params={"status": "DISABLED"})).json()["data"]
        assert listed["total"] == 1 and listed["items"][0]["author"] == "ADMIN"
        delete_headers = {**headers, "Idempotency-Key": uuid4().hex}
        body = {
            "actor_id": str(actor),
            "mutation": {"action": "DELETE", "memory_id": memory_id, "expected_version": 2},
        }
        first = await client.post(path + "/commands", headers=delete_headers, json=body)
        assert first.status_code == 200
        assert (await client.post(path + "/commands", headers=delete_headers, json=body)).json()[
            "data"
        ] == first.json()["data"]
        assert (await client.get(path, headers=headers)).json()["data"]["total"] == 0
        history = (
            await client.get(path + "/color/history", headers=headers, params={"page_size": 2})
        ).json()["data"]
        assert history["total"] == 3 and [row["version"] for row in history["items"]] == [3, 2]
        assert all(row["actor_type"] == "ADMIN" and row["actor_id"] == str(actor) for row in history["items"])
        assert history["items"][0]["after"]["is_deleted"] is True
        assert (await client.get(path, headers={**headers, "X-Tenant-Id": "other"})).json()["data"][
            "total"
        ] == 0
        assert (
            await client.get(path + "/color/history", headers={**headers, "X-Tenant-Id": "other"})
        ).status_code == 404


async def test_local_admin_real_http_receipts_replay_and_version_conflict(async_tool_database, monkeypatch):
    async with admin_stack(async_tool_database, monkeypatch) as (client, factory, user, actor, headers):
        path = f"/internal/admin/users/{user}/memory/commands"
        first = await client.post(path, headers=headers, json=create_body(actor))
        assert first.status_code == 200
        result = first.json()["data"]
        assert result["changed_count"] == 1 and result["items"][0]["version"] == 1
        assert (await client.post(path, headers=headers, json=create_body(actor))).json()["data"] == result
        different = await client.post(path, headers=headers, json=create_body(actor, value="green"))
        assert different.status_code == 409 and different.json()["code"] == "IDEMPOTENCY_MISMATCH"
        conflict = await client.post(
            path,
            headers={**headers, "Idempotency-Key": uuid4().hex},
            json=create_body(actor, expected_version=99),
        )
        assert conflict.status_code == 409 and conflict.json()["code"] == "MEMORY_VERSION_CONFLICT"
        listed = await client.get(f"/internal/admin/users/{user}/memory", headers=headers)
        assert listed.json()["data"]["total"] == 1
        item = listed.json()["data"]["items"][0]
        assert (
            item["author"] == "ADMIN" and item["status"] == "ACTIVE" and item["content"] == {"value": "blue"}
        )
        async with factory() as session:
            assert await session.scalar(sa.select(sa.func.count()).select_from(UserMemoryHistory)) == 1


@pytest.mark.parametrize("attack", ["missing_service", "wrong_service", "author", "tenant", "user"])
async def test_local_admin_rejects_untrusted_identity_or_extra_authority(
    async_tool_database, monkeypatch, attack
):
    async with admin_stack(async_tool_database, monkeypatch) as (client, factory, user, actor, headers):
        body = create_body(actor)
        if attack == "missing_service":
            headers.pop("X-Internal-Service")
        elif attack == "wrong_service":
            headers["X-Internal-Service"] = "wrong"
        else:
            body[{"author": "author", "tenant": "tenant_id", "user": "user_id"}[attack]] = "ADMIN"
        response = await client.post(
            f"/internal/admin/users/{user}/memory/commands", headers=headers, json=body
        )
        assert response.status_code == (403 if attack.endswith("service") else 422)
        assert response.json()["code"] == (
            "FORBIDDEN" if attack.endswith("service") else "COMMON_VALIDATION_ERROR"
        )
        async with factory() as session:
            assert await session.scalar(sa.select(sa.func.count()).select_from(UserMemory)) == 0


async def test_local_admin_cross_user_and_tenant_id_never_delete_another_scope(
    async_tool_database, monkeypatch
):
    async with admin_stack(async_tool_database, monkeypatch) as (client, factory, user, actor, headers):
        path = f"/internal/admin/users/{user}/memory/commands"
        created = (await client.post(path, headers=headers, json=create_body(actor))).json()["data"]
        memory_id = created["items"][0]["id"]
        for target, tenant in ((uuid4(), headers["X-Tenant-Id"]), (user, "another-tenant")):
            deleted = await client.post(
                f"/internal/admin/users/{target}/memory/commands",
                headers={**headers, "X-Tenant-Id": tenant, "Idempotency-Key": uuid4().hex},
                json={
                    "actor_id": str(actor),
                    "mutation": {"action": "DELETE", "memory_id": memory_id, "expected_version": 1},
                },
            )
            assert deleted.status_code == 404 and deleted.json()["code"] == "MEMORY_NOT_FOUND"
        async with factory() as session:
            row = await session.scalar(sa.select(UserMemory))
            assert not row.is_deleted and row.version == 1
