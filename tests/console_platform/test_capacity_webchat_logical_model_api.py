"""[B-219] 逻辑模型配置去除执行密钥并要求真实输出预算。

真实边界（不 mock 业务路径）：
- **真实 Console HTTP/应用服务**：ASGI 直连真实应用，真实登录会话裁决 ADMIN 角色与 CSRF，
  写路径走真实请求事务；
- **真实 PostgreSQL**：逻辑模型、物理凭据、模型成员与审计都是真实行；「末段 revision 已物理
  删列」直接读 `information_schema`（真实库结构），ORM 属性同步为缺失；
- **真实 HTTP 协议探针**：批量测试用本地 HTTPServer 记录真实 Authorization 头，证明执行
  入口只剩 `model_credential`（旧列不是执行入口，也已不存在）。

断言要点：Agent 继续显式选择 `model_id`、无平台默认模型；只能 OPENAI 协议；
`params.max_tokens` 必须是**实际发送**的字段（拒收同义未发送字段）；旧 `base_url`/`api_key`
的写入口与读取投影都已删除，物理列在末段 revision 被删（删列生命周期在本任务收口）。
"""

from __future__ import annotations

import hashlib
import json
import threading
import uuid
from collections.abc import AsyncIterator, Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from httpx import AsyncClient
from muad_console_platform.infrastructure.db import get_session_factory
from muad_console_platform.infrastructure.models.control import (
    ModelCredential,
    ModelDefinition,
    ModelDeployment,
)
from sqlalchemy import inspect, text

from console_platform.conftest import TenantContext
from console_platform.test_users_api import _headers

URL = "/api/v1/models"
AGENTS_URL = "/api/v1/agents"
PROVIDER = "openai"
SECRET = "sk-canary-logical-model-api"


class ProbeEndpoint:
    """真实本地 HTTP 端点：/v1 需要 `Bearer good-key`，并记录每次真实请求的路径与鉴权头。"""

    def __init__(self) -> None:
        self.paths: list[str] = []
        self.authorizations: list[str | None] = []
        endpoint = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: object) -> None:
                return

            def _record(self) -> None:
                endpoint.paths.append(self.path)
                endpoint.authorizations.append(self.headers.get("Authorization"))

            def _json(self, status: int) -> None:
                body = json.dumps({"data": []}).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802
                self._record()
                if self.headers.get("Authorization") != "Bearer good-key":
                    self._json(401)
                    return
                self._json(200)

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture()
def probe_endpoint() -> Iterator[ProbeEndpoint]:
    endpoint = ProbeEndpoint()
    try:
        yield endpoint
    finally:
        endpoint.stop()


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


async def _purge_tenant(tenant_id: str) -> None:
    """按外键顺序删本用例租户的 Agent/成员/凭据/模型与审计行，不影响其他租户。"""
    factory = get_session_factory()
    async with factory() as session:
        for statement in (
            "DELETE FROM control.agent_access_grant WHERE agent_id IN "
            "(SELECT id FROM control.agent_definition WHERE tenant_id = :tenant_id)",
            "DELETE FROM control.agent_definition WHERE tenant_id = :tenant_id",
            "DELETE FROM control.model_deployment WHERE tenant_id = :tenant_id",
            "DELETE FROM control.model_credential WHERE tenant_id = :tenant_id",
            "DELETE FROM control.model_definition WHERE tenant_id = :tenant_id",
            "DELETE FROM control.config_audit_log WHERE tenant_id = :tenant_id",
        ):
            await session.execute(text(statement), {"tenant_id": tenant_id})
        await session.commit()


@pytest.fixture
async def logical_tenant(tenant: TenantContext) -> AsyncIterator[TenantContext]:
    try:
        yield tenant
    finally:
        await _purge_tenant(tenant.tenant_id)


def _payload(key: str | None = None, **overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "key": key or f"model-{uuid.uuid4().hex[:8]}",
        "name": "Logical Model",
        "provider_key": PROVIDER,
        "model_id": "gpt-4o-mini",
        "params": {"temperature": 0.3, "max_tokens": 2048},
    }
    body.update(overrides)
    return body


async def _create_model(
    client: AsyncClient, tenant: TenantContext, **overrides: object
) -> dict:
    response = await client.post(URL, json=_payload(**overrides), headers=_headers(tenant))
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def _bind_member(
    tenant_id: str,
    model_id: uuid.UUID,
    *,
    provider_key: str = PROVIDER,
    base_url: str = "http://127.0.0.1:9/v1",
    api_key: str = SECRET,
    enabled: bool = True,
) -> tuple[uuid.UUID, uuid.UUID]:
    """真实 PostgreSQL 写入一条物理凭据与一条模型成员（发布/迁移同一数据形状）。"""
    factory = get_session_factory()
    async with factory() as session:
        credential = ModelCredential(
            tenant_id=tenant_id,
            key=f"cred-{uuid.uuid4().hex[:8]}",
            provider_key=provider_key,
            base_url=base_url,
            api_key=api_key,
            api_key_digest=_digest(api_key),
            max_inflight=8,
            enabled=enabled,
        )
        session.add(credential)
        await session.flush()
        deployment = ModelDeployment(
            tenant_id=tenant_id,
            key=f"member-{uuid.uuid4().hex[:8]}",
            model_id=model_id,
            credential_id=credential.id,
            weight=1,
        )
        session.add(deployment)
        await session.commit()
        return credential.id, deployment.id


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"temperature": 0.3},
        {"max_tokens": 0},
        {"max_tokens": -1},
        {"max_tokens": True},
        {"max_tokens": 2.5},
        {"max_tokens": "2048"},
        {"max_tokens": None},
        # 同义但**不会被发送**的字段：模型调用真实生效的预算键是 `max_tokens`，
        # 用 `max_output_tokens` 冒充输出预算必须在写入口被拒。
        {"max_output_tokens": 2048},
    ],
)
async def test_b219_create_requires_positive_integer_max_tokens(
    client: AsyncClient, logical_tenant: TenantContext, params: dict[str, object]
) -> None:
    response = await client.post(
        URL, json=_payload(params=params), headers=_headers(logical_tenant)
    )
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "COMMON_VALIDATION_ERROR"


async def test_b219_create_persists_the_sent_output_budget(
    client: AsyncClient, logical_tenant: TenantContext
) -> None:
    model = await _create_model(client, logical_tenant, params={"temperature": 0.3, "max_tokens": 2048})
    assert model["params"] == {"temperature": 0.3, "max_tokens": 2048}

    factory = get_session_factory()
    async with factory() as session:
        stored = await session.scalar(
            text("SELECT params_json FROM control.model_definition WHERE id = :id"),
            {"id": uuid.UUID(model["id"])},
        )
    assert stored == {"temperature": 0.3, "max_tokens": 2048}


async def test_b219_update_requires_positive_integer_max_tokens(
    client: AsyncClient, logical_tenant: TenantContext
) -> None:
    model = await _create_model(client, logical_tenant)
    for params in ({"max_tokens": 0}, {"max_output_tokens": 2048}, {"temperature": 0.5}):
        response = await client.put(
            f"{URL}/{model['id']}",
            json={"expected_revision": 1, "params": params},
            headers=_headers(logical_tenant),
        )
        assert response.status_code == 422, response.text
        assert response.json()["code"] == "COMMON_VALIDATION_ERROR"
    detail = await client.get(f"{URL}/{model['id']}", headers=_headers(logical_tenant))
    assert detail.json()["data"]["params"]["max_tokens"] == 2048


async def test_b219_legacy_execution_fields_rejected_on_write(
    client: AsyncClient, logical_tenant: TenantContext
) -> None:
    for field, value in (("base_url", "https://api.example.com/v1"), ("api_key", "sk-legacy-write")):
        created = await client.post(
            URL, json=_payload(**{field: value}), headers=_headers(logical_tenant)
        )
        assert created.status_code == 422, created.text
        assert created.json()["code"] == "COMMON_VALIDATION_ERROR"

    model = await _create_model(client, logical_tenant)
    for field, value in (("base_url", "https://api.example.com/v1"), ("api_key", "sk-legacy-write")):
        updated = await client.put(
            f"{URL}/{model['id']}",
            json={"expected_revision": 1, field: value},
            headers=_headers(logical_tenant),
        )
        assert updated.status_code == 422, updated.text
        assert updated.json()["code"] == "COMMON_VALIDATION_ERROR"


async def test_b219_projection_carries_no_execution_secrets_or_endpoints(
    client: AsyncClient, logical_tenant: TenantContext
) -> None:
    model = await _create_model(client, logical_tenant)
    assert {"base_url", "api_key", "api_key_configured"} & set(model) == set()
    assert model["provider_key"] == PROVIDER

    detail = await client.get(f"{URL}/{model['id']}", headers=_headers(logical_tenant))
    assert {"base_url", "api_key", "api_key_configured"} & set(detail.json()["data"]) == set()

    listing = await client.get(URL, params={"keyword": model["key"]}, headers=_headers(logical_tenant))
    items = listing.json()["data"]["items"]
    assert len(items) == 1
    assert {"base_url", "api_key", "api_key_configured"} & set(items[0]) == set()


async def test_b219_final_revision_drops_legacy_execution_columns(
    logical_tenant: TenantContext,
) -> None:
    """末段 revision 之后，库与 ORM 都不再有旧执行列；连接/密钥只在 `model_credential`。"""
    factory = get_session_factory()
    async with factory() as session:
        schema = await session.run_sync(
            lambda sync: {
                table: {column["name"] for column in inspect(sync.connection()).get_columns(table, "control")}
                for table in ("model_definition", "model_credential")
            }
        )
    definition_columns = schema["model_definition"]
    assert "provider_key" in definition_columns
    assert {"base_url", "api_key", "is_default"} & definition_columns == set()
    # 键的连接与明文只在物理凭据表：删列不是"丢执行入口"，而是入口归一。
    assert {"base_url", "api_key"} <= schema["model_credential"]
    assert not hasattr(ModelDefinition, "base_url") and not hasattr(ModelDefinition, "api_key")


async def test_b219_only_openai_protocol_and_explicit_agent_model(
    client: AsyncClient, logical_tenant: TenantContext
) -> None:
    non_openai = await client.post(
        URL, json=_payload(protocol="ANTHROPIC"), headers=_headers(logical_tenant)
    )
    assert non_openai.status_code == 422, non_openai.text
    assert non_openai.json()["code"] == "COMMON_VALIDATION_ERROR"

    with_default = await client.post(
        URL, json=_payload(is_default=True), headers=_headers(logical_tenant)
    )
    assert with_default.status_code == 422, with_default.text
    assert with_default.json()["code"] == "COMMON_VALIDATION_ERROR"

    model = await _create_model(client, logical_tenant)
    assert model["protocol"] == "OPENAI", "协议不得暗换：默认即 OPENAI 且必须原样回显"

    anonymous = await client.post(
        AGENTS_URL,
        json={
            "key": f"agent-{uuid.uuid4().hex[:8]}",
            "name": "No Model Agent",
            "instructions": "help",
            "runtime_config": {},
        },
        headers=_headers(logical_tenant),
    )
    assert anonymous.status_code == 422, anonymous.text
    assert anonymous.json()["code"] == "COMMON_VALIDATION_ERROR"

    unknown = await client.post(
        AGENTS_URL,
        json={
            "key": f"agent-{uuid.uuid4().hex[:8]}",
            "name": "Unknown Model Agent",
            "instructions": "help",
            "model_id": str(uuid.uuid4()),
            "runtime_config": {},
        },
        headers=_headers(logical_tenant),
    )
    assert unknown.status_code == 404, unknown.text
    assert unknown.json()["code"] == "COMMON_NOT_FOUND"

    explicit = await client.post(
        AGENTS_URL,
        json={
            "key": f"agent-{uuid.uuid4().hex[:8]}",
            "name": "Explicit Model Agent",
            "instructions": "help",
            "model_id": model["id"],
            "runtime_config": {},
        },
        headers=_headers(logical_tenant),
    )
    assert explicit.status_code == 200, explicit.text
    assert explicit.json()["data"]["model_id"] == model["id"]


async def test_b219_execution_entry_is_the_bound_member_credential(
    client: AsyncClient, logical_tenant: TenantContext, probe_endpoint: ProbeEndpoint
) -> None:
    """真实 HTTP 探针：批量测试读取的是成员凭据的当前值；无成员的模型没有执行入口。"""
    bound = await _create_model(client, logical_tenant)
    await _bind_member(
        logical_tenant.tenant_id,
        uuid.UUID(bound["id"]),
        base_url=probe_endpoint.url,
        api_key="good-key",
    )
    unbound = await _create_model(client, logical_tenant)

    response = await client.post(
        "/api/v1/models/batch-test",
        json={"model_ids": [bound["id"], unbound["id"]]},
        headers=_headers(logical_tenant),
    )
    assert response.status_code == 200, response.text
    items = {item["model_id"]: item for item in response.json()["data"]["items"]}
    assert items[bound["id"]]["status"] == "AVAILABLE"
    assert items[unbound["id"]]["status"] == "FAILED"
    assert items[unbound["id"]]["error_code"] == "CREDENTIAL_MISSING"
    assert probe_endpoint.authorizations == ["Bearer good-key"], (
        "探针必须用成员凭据的当前明文鉴权，而不是已删除的旧列"
    )


async def test_b219_provider_declared_before_members_then_locked(
    client: AsyncClient, logical_tenant: TenantContext
) -> None:
    """供方标识随逻辑模型维护：无成员时可声明/更正，绑定成员后不得改动（成员必须同供方）。"""
    undeclared = await _create_model(client, logical_tenant, provider_key="")
    assert undeclared["provider_key"] == ""

    declared = await client.put(
        f"{URL}/{undeclared['id']}",
        json={"expected_revision": 1, "provider_key": PROVIDER},
        headers=_headers(logical_tenant),
    )
    assert declared.status_code == 200, declared.text
    assert declared.json()["data"]["provider_key"] == PROVIDER

    await _bind_member(logical_tenant.tenant_id, uuid.UUID(undeclared["id"]))
    locked = await client.put(
        f"{URL}/{undeclared['id']}",
        json={"expected_revision": 2, "provider_key": "azure"},
        headers=_headers(logical_tenant),
    )
    assert locked.status_code == 409, locked.text
    assert locked.json()["code"] == "COMMON_CONFLICT"

    same = await client.put(
        f"{URL}/{undeclared['id']}",
        json={"expected_revision": 2, "provider_key": PROVIDER},
        headers=_headers(logical_tenant),
    )
    assert same.status_code == 200, same.text
