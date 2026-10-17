"""[ADR-06] 摘要跑在**自己那条模型定义**上：endpoint / 模型名 / 凭据都取自 `summary.model_ref`。

不得 Mock 的真实边界：真实 `POST /v1/runs` → 真实 `RunService` 建 Run + 真实快照冻结 → 真实
`default_executor_factory` 装配 → 真实 `OpenAICompatibleProvider` / 真实 `RuntimeContextCompactor`
→ 真实 PostgreSQL（`runtime.runtime_snapshot` / `canonical_event` / `artifact`）。

只在 HTTP 出口换掉 `httpx.MockTransport`（把"打到哪个 endpoint、带哪把凭据"记下来），以及按
ADR-06 换掉内部解析端点（`resolve-model`）与 API-09 凭据端点——这两个是本用例要断言的**真实契约**。

修复前的行为：`summary.model_ref` 完全不生效，摘要始终打主模型的 endpoint 与凭据。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from muad_agent_runtime.api.deps import get_credentials_client, get_executor_factory
from muad_agent_runtime.application.executor import default_executor_factory
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import RunRecord, RuntimeSnapshot
from muad_agent_runtime.main import app
from muad_contracts import ResolvedModel
from muad_contracts.model_pool import derive_model_key_resource_id

from agent_runtime.conftest import FakeResolveClient, TenantContext, parse_sse

SUMMARY_MODEL_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
SUMMARY_BASE_URL = "https://summary.example/v1"
SUMMARY_MODEL_NAME = "cheap-summary-model"
SUMMARY_KEY = "summary-secret-key"
ANSWER = "收到，我接着聊。"

SUMMARY_JSON = json.dumps(
    {
        "user_goal": "把长会话压成摘要",
        "constraints": [],
        "progress": [],
        "open_items": [],
        "artifacts": [],
    },
    ensure_ascii=False,
)


def _completion(content: str) -> dict[str, Any]:
    return {
        "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 7},
    }


MAIN_BASE_URL = "https://api.example.com/v1"
MAIN_MEMBER_KEY = "sk-main-member"
MAX_TOKENS = 64


@dataclass(frozen=True, slots=True)
class _MainMember:
    """主模型的真实成员事实（TASK-025 起主链路按冻结引用解析这把 Key）。"""

    deployment_id: uuid.UUID
    credential_id: uuid.UUID
    digest: str

    def key_resource_id(self, tenant_id: str) -> uuid.UUID:
        return derive_model_key_resource_id(tenant_id, "openai", self.digest)


class _Credentials:
    """API-09 替身：按 `model_id` 回密钥，并记下**被问了哪些模型**。

    冻结引用（`deployment_id`+`credential_id`+`expected_revision`）给齐时返回完整的
    `SelectedModelCredential` 投影——主链路（TASK-025）就按它解析并校验预占身份；
    缺省（摘要模型 / Run 创建边界的过渡解析）仍是过渡投影。
    """

    def __init__(self, member: _MainMember, model_id: uuid.UUID) -> None:
        self.model_ids: list[str] = []
        self._member, self._model_id = member, model_id

    async def resolve_credentials(
        self, *, tenant_id: str, payload: dict[str, Any], trace_id: str = ""
    ) -> dict[str, Any]:
        model_id = str(payload["model_id"])
        self.model_ids.append(model_id)
        if payload.get("deployment_id") is None:
            return {"model": {"id": model_id, "api_key": f"key-for-{model_id}"}, "mcp_servers": []}
        return {
            "model": {
                "model_id": model_id,
                "deployment_id": str(self._member.deployment_id),
                "credential_id": str(self._member.credential_id),
                "credential_revision": int(payload["expected_revision"]),
                "key_resource_id": str(self._member.key_resource_id(tenant_id)),
                "quota_group_id": None,
                "provider_key": "openai",
                "base_url": MAIN_BASE_URL,
                "api_key": MAIN_MEMBER_KEY,
            },
            "mcp_servers": [],
        }


def _transport(seen: list[httpx.Request]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.host == "summary.example":
            return httpx.Response(200, json=_completion(SUMMARY_JSON))
        return httpx.Response(200, json=_completion(ANSWER))

    return httpx.MockTransport(handler)


def _enable_summary(fake_resolve: FakeResolveClient) -> None:
    """从 Agent 的 `runtime_config` 打开摘要层（生产里平台设置也走这条合并路径）。"""
    agent = fake_resolve.response.agent
    fake_resolve.response = fake_resolve.response.model_copy(
        update={
            "agent": agent.model_copy(
                update={
                    "runtime_config": {
                        **agent.runtime_config,
                        "budget": {
                            "compaction": {
                                "summary": {
                                    "enabled": True,
                                    "threshold_bytes": 1,
                                    "model_ref": str(SUMMARY_MODEL_ID),
                                }
                            }
                        },
                    }
                }
            )
        }
    )


async def _send(client: AsyncClient, tenant: TenantContext, text: str) -> uuid.UUID:
    response = await client.post(
        "/v1/runs",
        json={
            "agent_id": str(tenant.agent_id),
            "platform_user_id": str(tenant.platform_user_id),
            "channel": {"type": "WECOM", "bot_id": "bot-summary"},
            "message": {"id": f"msg-{uuid.uuid4()}", "type": "text", "text": text},
        },
        headers={"X-Tenant-Id": tenant.tenant_id},
    )
    assert response.status_code == 200, response.text
    events = parse_sse(response.text)
    assert events[-1]["type"] == "run.completed", json.dumps(events[-1], ensure_ascii=False)
    return uuid.UUID(str(events[0]["run_id"]))


async def _seed_main_member(tenant: TenantContext, model_id: uuid.UUID) -> _MainMember:
    """主模型的真实控制面行：成员关系 + 成员凭据（TASK-025 起冻结候选的来源）。

    TASK-024 起 Run 创建边界会冻结成员的非密钥引用；TASK-025 起主链路按冻结引用解析密钥，
    因此本用例必须给出真实成员事实，而不是让主模型停在"没有执行入口"。
    """
    member = _MainMember(
        deployment_id=uuid.uuid4(),
        credential_id=uuid.uuid4(),
        digest=hashlib.sha256(MAIN_MEMBER_KEY.encode()).hexdigest(),
    )
    async with get_session_factory()() as session:
        await session.execute(
            sa.text(
                "INSERT INTO control.model_definition (id, tenant_id, key, name, model_id, provider_key,"
                " params_json) VALUES (:id, :tenant_id, :key, :name, 'gpt-4o-mini', 'openai',"
                " CAST(:params AS jsonb))"
            ).bindparams(
                id=model_id,
                tenant_id=tenant.tenant_id,
                key=f"model-{uuid.uuid4().hex[:10]}",
                name="summary-main-model",
                params=json.dumps({"temperature": 0.0, "max_tokens": MAX_TOKENS}),
            )
        )
        await session.execute(
            sa.text(
                "INSERT INTO control.model_credential (id, tenant_id, key, provider_key, base_url, api_key,"
                " api_key_digest, max_inflight, enabled, revision)"
                " VALUES (:id, :tenant_id, :key, 'openai', :base_url, :api_key, :digest, 5, true, 1)"
            ).bindparams(
                id=member.credential_id,
                tenant_id=tenant.tenant_id,
                key=f"key-{uuid.uuid4().hex[:10]}",
                base_url=MAIN_BASE_URL,
                api_key=MAIN_MEMBER_KEY,
                digest=member.digest,
            )
        )
        await session.execute(
            sa.text(
                "INSERT INTO control.model_deployment (id, tenant_id, key, model_id, credential_id, weight,"
                " enabled) VALUES (:id, :tenant_id, :key, :model_id, :credential_id, 1, true)"
            ).bindparams(
                id=member.deployment_id,
                tenant_id=tenant.tenant_id,
                key=f"member-{uuid.uuid4().hex[:10]}",
                model_id=model_id,
                credential_id=member.credential_id,
            )
        )
        await session.commit()
    return member


@pytest.fixture()
async def _stack(
    fake_resolve: FakeResolveClient, tenant: TenantContext, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[AsyncClient, list[httpx.Request], _Credentials]]:
    from httpx import ASGITransport
    from muad_agent_runtime.api.deps import get_resolve_client

    # 真实产物根换到 tmp_path：compaction transcript 会真的落盘（别写进 dev 的共享产物根）
    monkeypatch.setenv("ARTIFACT_ROOT", str(tmp_path))
    # 输出预算是池化链路的预占输入（design §3.7：启用池时显式填写、不读未发送的同义字段）
    resolved_model = fake_resolve.response.model
    fake_resolve.response = fake_resolve.response.model_copy(
        update={
            "model": resolved_model.model_copy(
                update={"params": {**resolved_model.params, "max_tokens": MAX_TOKENS}}
            )
        }
    )
    model_id = fake_resolve.response.model.id
    member = await _seed_main_member(tenant, model_id)
    seen: list[httpx.Request] = []
    credentials = _Credentials(member, model_id)
    app.dependency_overrides[get_resolve_client] = lambda: fake_resolve
    app.dependency_overrides[get_executor_factory] = lambda: partial(
        default_executor_factory, model_transport=_transport(seen)
    )
    app.dependency_overrides[get_credentials_client] = lambda: credentials
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client, seen, credentials
    finally:
        app.dependency_overrides.pop(get_resolve_client, None)
        app.dependency_overrides.pop(get_executor_factory, None)
        app.dependency_overrides.pop(get_credentials_client, None)
        async with get_session_factory()() as session:
            for statement in (
                "DELETE FROM runtime.model_invocation_audit WHERE tenant_id = :tenant_id",
                "DELETE FROM runtime.model_dispatch_state WHERE tenant_id = :tenant_id",
                "DELETE FROM runtime.model_call_lease WHERE tenant_id = :tenant_id",
                "DELETE FROM runtime.model_capacity WHERE tenant_id = :tenant_id",
                "DELETE FROM control.model_deployment WHERE tenant_id = :tenant_id",
                "DELETE FROM control.model_credential WHERE tenant_id = :tenant_id",
                "DELETE FROM control.model_definition WHERE tenant_id = :tenant_id",
            ):
                await session.execute(sa.text(statement), {"tenant_id": tenant.tenant_id})
            await session.commit()


def _main_requests(seen: list[httpx.Request]) -> list[httpx.Request]:
    return [request for request in seen if request.url.host != "summary.example"]


def _summary_requests(seen: list[httpx.Request]) -> list[httpx.Request]:
    return [request for request in seen if request.url.host == "summary.example"]


async def test_summary_call_goes_to_the_frozen_summary_model(
    _stack: tuple[AsyncClient, list[httpx.Request], _Credentials],
    tenant: TenantContext,
    fake_resolve: FakeResolveClient,
) -> None:
    """摘要请求打到 `model_ref` 那条模型的 endpoint / 模型名 / 凭据上，主模型不参与。"""
    client, seen, credentials = _stack
    fake_resolve.summary_models[SUMMARY_MODEL_ID] = ResolvedModel(
        id=SUMMARY_MODEL_ID,
        revision=1,
        model_id=SUMMARY_MODEL_NAME,
        base_url=SUMMARY_BASE_URL,
        api_key=None,  # 凭据只走 API-09（定义里不带）
        params={},
    )
    _enable_summary(fake_resolve)

    await _send(client, tenant, "第一轮：先说一句")
    second = await _send(client, tenant, "第二轮：接着聊")

    # ① 解析发生在**每个 Run 的创建边界**，用的是 `model_ref` 这个主键
    assert [request.model_id for request in fake_resolve.model_calls] == [
        SUMMARY_MODEL_ID,
        SUMMARY_MODEL_ID,
    ]
    # ② 凭据按**两个**模型各取一次（主模型 + 摘要模型），摘要那把来自它自己的 id
    assert str(fake_resolve.response.model.id) in credentials.model_ids
    assert str(SUMMARY_MODEL_ID) in credentials.model_ids

    summary_calls = _summary_requests(seen)
    assert summary_calls, "摘要必须真的发起过请求"
    for request in summary_calls:
        assert request.url.path == "/v1/chat/completions"
        assert json.loads(request.content)["model"] == SUMMARY_MODEL_NAME, (
            "摘要请求的模型名必须取自摘要模型定义"
        )
        assert request.headers["authorization"] == f"Bearer key-for-{SUMMARY_MODEL_ID}"
    for request in _main_requests(seen):
        assert str(request.url.host) == "api.example.com"

    # ③ 冻结：快照里存的是**非密钥**字段（`api_key` 剥离），且参与内容 hash
    async with get_session_factory()() as session:
        run = await session.get(RunRecord, second)
        assert run is not None and run.snapshot_id is not None
        snapshot = await session.get(RuntimeSnapshot, run.snapshot_id)
        assert snapshot is not None
    frozen = snapshot.policy_json["summary_model"]
    assert frozen["base_url"] == SUMMARY_BASE_URL
    assert frozen["model_id"] == SUMMARY_MODEL_NAME
    assert "api_key" not in frozen, "密钥不得进快照"


async def test_unresolvable_summary_model_keeps_the_run_alive_and_skips_the_layer(
    _stack: tuple[AsyncClient, list[httpx.Request], _Credentials],
    tenant: TenantContext,
    fake_resolve: FakeResolveClient,
) -> None:
    """解析不出来（模型被删/停用）⇒ 摘要层本轮不跑，Run 照常完成、且**不退回主模型**。"""
    client, seen, _credentials = _stack
    _enable_summary(fake_resolve)  # summary_models 为空 ⇒ resolve_model 抛 COMMON_NOT_FOUND

    await _send(client, tenant, "第一轮：先说一句")
    await _send(client, tenant, "第二轮：接着聊")

    assert _summary_requests(seen) == [], "解析不到模型就不得发摘要请求（更不能打主模型）"
    assert _main_requests(seen), "主链路照常"
    async with get_session_factory()() as session:
        rows = (
            await session.scalars(
                sa.select(RuntimeSnapshot).where(RuntimeSnapshot.tenant_id == tenant.tenant_id)
            )
        ).all()
    assert all("summary_model" not in row.policy_json for row in rows)
