"""[ADR-06] 摘要跑在**自己那条模型定义**上：endpoint / 模型名 / 凭据都取自 `summary.model_ref`。

不得 Mock 的真实边界：真实 `POST /v1/runs` → 真实 `RunService` 建 Run + 真实快照冻结 → 真实
`default_executor_factory` 装配 → 真实 `OpenAICompatibleProvider` / 真实 `RuntimeContextCompactor`
→ 真实 PostgreSQL（`runtime.runtime_snapshot` / `canonical_event` / `artifact`）。

只在 HTTP 出口换掉 `httpx.MockTransport`（把"打到哪个 endpoint、带哪把凭据"记下来），以及按
ADR-06 换掉内部解析端点（`resolve-model`）与 API-09 凭据端点——这两个是本用例要断言的**真实契约**。

修复前的行为：`summary.model_ref` 完全不生效，摘要始终打主模型的 endpoint 与凭据。
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
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


class _Credentials:
    """API-09 替身：按 `model_id` 回密钥，并记下**被问了哪些模型**。"""

    def __init__(self) -> None:
        self.model_ids: list[str] = []

    async def resolve_credentials(
        self, *, tenant_id: str, payload: dict[str, Any], trace_id: str = ""
    ) -> dict[str, Any]:
        model_id = str(payload["model_id"])
        self.model_ids.append(model_id)
        return {
            "model": {"id": model_id, "api_key": f"key-for-{model_id}"},
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


@pytest.fixture()
async def _stack(
    fake_resolve: FakeResolveClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[AsyncClient, list[httpx.Request], _Credentials]]:
    from httpx import ASGITransport
    from muad_agent_runtime.api.deps import get_resolve_client

    # 真实产物根换到 tmp_path：compaction transcript 会真的落盘（别写进 dev 的共享产物根）
    monkeypatch.setenv("ARTIFACT_ROOT", str(tmp_path))
    seen: list[httpx.Request] = []
    credentials = _Credentials()
    app.dependency_overrides[get_resolve_client] = lambda: fake_resolve
    app.dependency_overrides[get_executor_factory] = lambda: partial(
        default_executor_factory, model_transport=_transport(seen)
    )
    app.dependency_overrides[get_credentials_client] = lambda: credentials
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, seen, credentials
    app.dependency_overrides.pop(get_resolve_client, None)
    app.dependency_overrides.pop(get_executor_factory, None)
    app.dependency_overrides.pop(get_credentials_client, None)


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
