"""TASK-006：API-09 的 Task execution_ref（真实 HTTP + 真实 PostgreSQL，design §3.1.5/§4.4 item 9）。

Task 链上凭据解析的口径与 Run 链不同：

- tenant：`task.task_execution` 必须属于请求租户（跨租户解析不到别人的密钥）；
- actor：请求必须声明操作者且等于 Execution 的 actor —— Run 链上它只是审计关联，这里它是门控；
- agent：只解析该 Execution **冻结**的模型（`execution_snapshot_json.model.model_id`），拿别的
  模型的 Execution 身份解析密钥即越权；
- 密钥实时解析、只进调用内存：响应体（内部专用）含明文，但去重指纹/快照/错误响应都不带密钥。

真实边界：真实 `main.app`（ASGITransport，真实路由与封套）、真实服务身份门控、真实
`control.model_*` Owner 表与真实 `task.task_execution` 行；不 mock 存储或授权事实。
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
import sqlalchemy as sa
from httpx import AsyncClient, Response
from muad_console_platform.infrastructure.db import get_session_factory
from muad_console_platform.infrastructure.settings import reset_settings_cache

from console_internal.conftest import MODEL_API_KEY, MODEL_BASE_URL, TenantContext

URL = "/internal/runtime/resolve-credentials"
SERVICE_TOKEN = "test-internal-token"


@dataclass(frozen=True, slots=True)
class TaskExecution:
    """一条真实 `task.task_execution` 行（归属事实的来源）。"""

    id: uuid.UUID
    tenant_id: str
    actor_user_id: uuid.UUID
    agent_id: uuid.UUID
    frozen_model_id: uuid.UUID


@pytest.fixture(autouse=True)
def _service_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", SERVICE_TOKEN)
    reset_settings_cache()
    yield
    reset_settings_cache()


def _headers(tenant: TenantContext, *, token: str | None = SERVICE_TOKEN) -> dict[str, str]:
    headers = {"X-Tenant-Id": tenant.tenant_id}
    if token is not None:
        headers["X-Internal-Service"] = token
    return headers


async def _insert_execution(
    *,
    tenant_id: str,
    actor_user_id: uuid.UUID,
    agent_id: uuid.UUID,
    frozen_model_id: uuid.UUID,
    is_deleted: bool = False,
) -> TaskExecution:
    execution_id = uuid.uuid4()
    snapshot = (
        '{"schema_version": 4, "mode": "AGENTIC", "agent": {"id": "'
        + str(agent_id)
        + '"}, "model": {"model_id": "'
        + str(frozen_model_id)
        + '"}}'
    )
    async with get_session_factory()() as session:
        await session.execute(
            sa.text(
                "INSERT INTO task.task_execution (id, tenant_id, agent_id, actor_user_id,"
                " trigger_type, status, input_json, execution_snapshot_json, snapshot_hash,"
                " idempotency_key, is_deleted) VALUES (:id, :tenant_id, :agent_id,"
                " :actor_user_id, 'SCHEDULE', 'RUNNING', CAST(:input AS jsonb),"
                " CAST(:snapshot AS jsonb), :snapshot_hash, :idempotency_key, :is_deleted)"
            ).bindparams(
                id=execution_id,
                tenant_id=tenant_id,
                agent_id=agent_id,
                actor_user_id=actor_user_id,
                input="{}",
                snapshot=snapshot,
                snapshot_hash="sha256:" + hashlib.sha256(snapshot.encode()).hexdigest(),
                idempotency_key=f"task-{uuid.uuid4().hex}",
                is_deleted=is_deleted,
            )
        )
        await session.commit()
    return TaskExecution(
        id=execution_id,
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        agent_id=agent_id,
        frozen_model_id=frozen_model_id,
    )


async def _credential_revision(tenant: TenantContext) -> int:
    async with get_session_factory()() as session:
        value = await session.scalar(
            sa.text("SELECT revision FROM control.model_credential WHERE id = :id").bindparams(
                id=tenant.model_credential_id
            )
        )
    assert value is not None, "seeded credential row missing"
    return int(value)


async def _delete_executions(tenant_id: str) -> None:
    async with get_session_factory()() as session:
        await session.execute(
            sa.text("DELETE FROM task.task_execution WHERE tenant_id = :tenant_id"),
            {"tenant_id": tenant_id},
        )
        await session.commit()


@pytest.fixture
async def execution(tenant: TenantContext) -> AsyncIterator[TaskExecution]:
    """租户内一条 AGENTIC Execution：冻结本租户的启用模型。"""
    row = await _insert_execution(
        tenant_id=tenant.tenant_id,
        actor_user_id=tenant.actor_user_id,
        agent_id=tenant.agent_id,
        frozen_model_id=tenant.model_id,
    )
    try:
        yield row
    finally:
        await _delete_executions(tenant.tenant_id)


def _task_payload(
    tenant: TenantContext,
    execution: TaskExecution,
    *,
    actor_user_id: uuid.UUID | None = None,
    model_id: uuid.UUID | None = None,
    execution_id: uuid.UUID | None = None,
    expected_revision: int | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "execution_ref": {
            "type": "TASK",
            "id": str(execution_id or execution.id),
        },
        "actor_user_id": str(tenant.actor_user_id if actor_user_id is None else actor_user_id),
        "model_id": str(model_id or execution.frozen_model_id),
        "mcp_server_ids": [],
    }
    if expected_revision is not None:
        payload |= {
            "deployment_id": str(tenant.model_deployment_id),
            "credential_id": str(tenant.model_credential_id),
            "expected_revision": expected_revision,
        }
    return payload


def _error_code(response: Response) -> str:
    return str(response.json()["code"])


async def test_task_execution_resolves_current_credential_for_frozen_model(
    client: AsyncClient, tenant: TenantContext, execution: TaskExecution
) -> None:
    """Task 归属齐备：解析当前认证值；响应含明文但绝不含去重指纹。"""
    revision = await _credential_revision(tenant)
    response = await client.post(
        URL,
        json=_task_payload(tenant, execution, expected_revision=revision),
        headers=_headers(tenant),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["code"] == "0"
    model = body["data"]["model"]
    assert model["api_key"] == MODEL_API_KEY
    assert model["base_url"] == MODEL_BASE_URL
    assert model["credential_revision"] == revision
    assert model["credential_id"] == str(tenant.model_credential_id)
    # 指纹与派生计量身份不是线协议内容（RULE-secret-001）
    assert hashlib.sha256(MODEL_API_KEY.encode()).hexdigest() not in response.text


async def test_task_execution_in_another_tenant_is_not_found(
    client: AsyncClient, tenant: TenantContext, execution: TaskExecution
) -> None:
    """跨租户：路径上的 Execution 不属于该租户 ⇒ 404，且不解析出密钥。"""
    response = await client.post(
        URL,
        json=_task_payload(tenant, execution),
        headers={
            "X-Tenant-Id": tenant.other_tenant_id,
            "X-Internal-Service": SERVICE_TOKEN,
        },
    )
    assert response.status_code == 404, response.text
    assert MODEL_API_KEY not in response.text


async def test_task_execution_actor_must_match_the_execution(
    client: AsyncClient, tenant: TenantContext, execution: TaskExecution
) -> None:
    """跨用户：声明别人的 actor（含完全不声明）⇒ TASK_ACCESS_DENIED。"""
    spoofed = await client.post(
        URL,
        json=_task_payload(tenant, execution, actor_user_id=tenant.other_actor_user_id),
        headers=_headers(tenant),
    )
    assert spoofed.status_code == 403, spoofed.text
    assert _error_code(spoofed) == "TASK_ACCESS_DENIED"

    anonymous = _task_payload(tenant, execution)
    anonymous.pop("actor_user_id")
    missing = await client.post(URL, json=anonymous, headers=_headers(tenant))
    assert missing.status_code == 403, missing.text
    assert _error_code(missing) == "TASK_ACCESS_DENIED"
    assert MODEL_API_KEY not in spoofed.text


async def test_task_execution_only_resolves_its_frozen_model(
    client: AsyncClient, tenant: TenantContext, execution: TaskExecution
) -> None:
    """跨 Agent/模型：拿该 Execution 的身份解析另一个模型 ⇒ TASK_ACCESS_DENIED。"""
    response = await client.post(
        URL,
        json=_task_payload(tenant, execution, model_id=tenant.disabled_model_id),
        headers=_headers(tenant),
    )
    assert response.status_code == 403, response.text
    assert _error_code(response) == "TASK_ACCESS_DENIED"


async def test_deleted_task_execution_is_not_found(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """软删的 Execution 不再能解析凭据。"""
    deleted = await _insert_execution(
        tenant_id=tenant.tenant_id,
        actor_user_id=tenant.actor_user_id,
        agent_id=tenant.agent_id,
        frozen_model_id=tenant.model_id,
        is_deleted=True,
    )
    try:
        response = await client.post(
            URL, json=_task_payload(tenant, deleted), headers=_headers(tenant)
        )
        assert response.status_code == 404, response.text
    finally:
        await _delete_executions(tenant.tenant_id)


async def test_task_execution_requires_service_identity(
    client: AsyncClient, tenant: TenantContext, execution: TaskExecution
) -> None:
    """Task 链与 Run 链同一道服务身份门控：缺失/错误身份一律拒绝。"""
    for token in (None, "wrong-token"):
        response = await client.post(
            URL,
            json=_task_payload(tenant, execution),
            headers=_headers(tenant, token=token),
        )
        assert response.status_code in (401, 403), response.text
        assert MODEL_API_KEY not in response.text


async def test_run_execution_ref_keeps_its_audit_only_actor_semantics(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """[Checklist 5] Run 链行为不变：`actor_user_id` 仍只作审计关联，不是门控。"""
    response = await client.post(
        URL,
        json={
            "execution_ref": {"type": "RUN", "id": str(uuid.uuid4())},
            "actor_user_id": str(tenant.other_actor_user_id),
            "model_id": str(tenant.model_id),
            "mcp_server_ids": [],
        },
        headers=_headers(tenant),
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["model"]["api_key"] == MODEL_API_KEY
