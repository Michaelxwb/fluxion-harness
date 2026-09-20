import uuid
from typing import Any

from httpx import AsyncClient
from muad_console_platform.infrastructure.db import get_session_factory
from muad_console_platform.infrastructure.repositories.model_repository import ModelRepository
from sqlalchemy import text

from console_platform.conftest import TenantContext
from console_platform.test_users_api import _headers


def _payload(key: str | None = None, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "key": key or f"model-{uuid.uuid4().hex[:8]}",
        "name": "GPT Test",
        "base_url": "https://api.example.com/v1",
        "model_id": "gpt-4o-mini",
        "api_key": "sk-plaintext-e2e",
        "params": {"temperature": 0.3},
    }
    body.update(overrides)
    return body


async def test_s01_create_persists_plaintext_key_without_exposing_it(
    client: AsyncClient, tenant: TenantContext
) -> None:
    key = f"model-{uuid.uuid4().hex[:8]}"
    created = await client.post("/api/v1/models", json=_payload(key), headers=_headers(tenant))
    assert created.status_code == 200
    model = created.json()["data"]
    assert model["key"] == key
    assert model["api_key_configured"] is True
    assert model["revision"] == 1
    assert model["last_test_status"] == "UNTESTED"
    assert "api_key" not in model

    listing = await client.get(
        "/api/v1/models", params={"keyword": key}, headers=_headers(tenant)
    )
    items = listing.json()["data"]["items"]
    assert len(items) == 1
    assert items[0]["api_key_configured"] is True
    assert "api_key" not in items[0]

    detail = await client.get(f"/api/v1/models/{model['id']}", headers=_headers(tenant))
    assert "api_key" not in detail.json()["data"]

    session_factory = get_session_factory()
    async with session_factory() as session:
        api_key = await session.scalar(
            text("SELECT api_key FROM control.model_definition WHERE id = :id"),
            {"id": uuid.UUID(model["id"])},
        )
    assert api_key == "sk-plaintext-e2e"


async def test_s03_disable_and_soft_delete_model(client: AsyncClient, tenant: TenantContext) -> None:
    key = f"model-{uuid.uuid4().hex[:8]}"
    created = await client.post("/api/v1/models", json=_payload(key), headers=_headers(tenant))
    model = created.json()["data"]

    disabled = await client.put(
        f"/api/v1/models/{model['id']}",
        json={"expected_revision": 1, "enabled": False},
        headers=_headers(tenant),
    )
    assert disabled.status_code == 200
    assert disabled.json()["data"]["enabled"] is False
    assert disabled.json()["data"]["revision"] == 2

    filtered = await client.get(
        "/api/v1/models", params={"enabled": "false"}, headers=_headers(tenant)
    )
    assert key in [item["key"] for item in filtered.json()["data"]["items"]]

    deleted = await client.delete(f"/api/v1/models/{model['id']}", headers=_headers(tenant))
    assert deleted.status_code == 200
    listing = await client.get(
        "/api/v1/models", params={"keyword": key}, headers=_headers(tenant)
    )
    assert listing.json()["data"]["total"] == 0


async def test_e01_stale_revision_conflicts_without_overwrite(
    client: AsyncClient, tenant: TenantContext
) -> None:
    created = await client.post("/api/v1/models", json=_payload(), headers=_headers(tenant))
    model = created.json()["data"]

    first = await client.put(
        f"/api/v1/models/{model['id']}",
        json={"expected_revision": 1, "name": "Renamed"},
        headers=_headers(tenant),
    )
    assert first.status_code == 200
    assert first.json()["data"]["revision"] == 2

    stale = await client.put(
        f"/api/v1/models/{model['id']}",
        json={"expected_revision": 1, "name": "Should Not Apply"},
        headers=_headers(tenant),
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "REVISION_CONFLICT"

    detail = await client.get(f"/api/v1/models/{model['id']}", headers=_headers(tenant))
    assert detail.json()["data"]["name"] == "Renamed"


async def test_e03_delete_referenced_model_returns_conflict(
    client: AsyncClient, tenant: TenantContext
) -> None:
    created = await client.post("/api/v1/models", json=_payload(), headers=_headers(tenant))
    model = created.json()["data"]
    agent = await client.post(
        "/api/v1/agents",
        json={
            "key": f"agent-{uuid.uuid4().hex[:8]}",
            "name": "Model Ref Agent",
            "instructions": "help",
            "model_id": model["id"],
            "runtime_config": {},
        },
        headers=_headers(tenant),
    )
    assert agent.status_code == 200

    blocked = await client.delete(f"/api/v1/models/{model['id']}", headers=_headers(tenant))
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "MODEL_IN_USE"
    # 专用错误码必须把引用数带到 msg 里（通用 COMMON_CONFLICT 无占位符，做不到）
    blocked_msg = blocked.json()["msg"]
    assert model["key"] in blocked_msg
    assert "1" in blocked_msg

    detail = await client.get(f"/api/v1/models/{model['id']}", headers=_headers(tenant))
    assert detail.status_code == 200


async def test_e02_e05_schema_rejects_reserved_and_immutable_fields(
    client: AsyncClient, tenant: TenantContext
) -> None:
    with_default = await client.post(
        "/api/v1/models", json=_payload(is_default=True), headers=_headers(tenant)
    )
    assert with_default.status_code == 422
    assert with_default.json()["code"] == "COMMON_VALIDATION_ERROR"

    non_openai = await client.post(
        "/api/v1/models", json=_payload(protocol="ANTHROPIC"), headers=_headers(tenant)
    )
    assert non_openai.status_code == 422
    assert non_openai.json()["code"] == "COMMON_VALIDATION_ERROR"

    created = await client.post("/api/v1/models", json=_payload(), headers=_headers(tenant))
    model = created.json()["data"]
    for body in (
        {"expected_revision": 1, "protocol": "OPENAI"},
        {"expected_revision": 1, "key": "changed-key"},
    ):
        response = await client.put(
            f"/api/v1/models/{model['id']}", json=body, headers=_headers(tenant)
        )
        assert response.status_code == 422
        assert response.json()["code"] == "COMMON_VALIDATION_ERROR"


async def test_duplicate_key_conflict_and_validation(
    client: AsyncClient, tenant: TenantContext
) -> None:
    key = f"model-{uuid.uuid4().hex[:8]}"
    first = await client.post("/api/v1/models", json=_payload(key), headers=_headers(tenant))
    assert first.status_code == 200
    duplicate = await client.post("/api/v1/models", json=_payload(key), headers=_headers(tenant))
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "MODEL_KEY_EXISTS"
    # 专用码必须把冲突的 key 带到 msg（通用 COMMON_CONFLICT 无占位符）
    assert key in duplicate.json()["msg"]

    bad_url = await client.post(
        "/api/v1/models", json=_payload(base_url="ftp://api.example.com"), headers=_headers(tenant)
    )
    assert bad_url.status_code == 422


async def test_e01_cas_primitive_rejects_stale_revision(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """CAS 原语层：条件更新带 revision 谓词 —— 陈旧 revision 必须不命中。

    注意：不要用"两个并发 HTTP 请求"来测这个保证 —— ASGI 传输下两个请求往往在关键点
    并不交错，先读后写的实现也能通过，等于没有区分度。这里直接在原语层构造"A 已推进、
    B 仍按旧 revision 提交"的丢失更新场景。
    """
    created = await client.post("/api/v1/models", json=_payload(), headers=_headers(tenant))
    model = created.json()["data"]
    model_id = uuid.UUID(model["id"])
    session_factory = get_session_factory()

    async with session_factory() as session_a:
        assert await ModelRepository(session_a).cas_update(
            tenant.tenant_id, model_id, 1, {"name": "Winner A"}
        )
        await session_a.commit()

    async with session_factory() as session_b:
        # B 仍按 revision=1 提交 → 必须被拒（A 已把它推进到 2）
        assert not await ModelRepository(session_b).cas_update(
            tenant.tenant_id, model_id, 1, {"name": "Loser B"}
        )
        await session_b.commit()

    detail = await client.get(f"/api/v1/models/{model['id']}", headers=_headers(tenant))
    assert detail.json()["data"]["revision"] == 2
    assert detail.json()["data"]["name"] == "Winner A"


async def test_e01_cas_update_sql_filters_on_revision() -> None:
    """结构断言：CAS 的 UPDATE 必须带 revision 谓词，防止退回无条件 UPDATE。"""

    class _CapturingSession:
        def __init__(self) -> None:
            self.statement: Any = None

        async def execute(self, statement: Any) -> Any:
            self.statement = statement

            class _Result:
                rowcount = 0

            return _Result()

    session = _CapturingSession()
    updated = await ModelRepository(session).cas_update(
        "tenant-1", uuid.uuid4(), 7, {"name": "X"}
    )

    assert updated is False
    rendered = str(session.statement)
    assert "model_definition.revision" in rendered
    assert "model_definition.is_deleted" in rendered
