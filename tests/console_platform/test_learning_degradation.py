"""TASK-004 / E-01：学习关闭与 Runtime 不可达的降级。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from importlib import import_module

import httpx
from fastapi import FastAPI
from httpx import AsyncClient
from muad_agent_runtime.application.ports import PlatformSettingsSnapshot
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import UserMemory
from muad_api import install_api_foundation
from muad_console_platform.api.deps import get_runtime_learning_client
from muad_console_platform.infrastructure.runtime_learning_client import RuntimeLearningClient
from muad_console_platform.main import app as console_app

from console_platform.conftest import TenantContext
from tests.internal_service import TOKEN as INTERNAL_SERVICE_TOKEN


class _Disabled:
    async def fetch_snapshot(self, *, tenant_id: str, trace_id: str = "") -> PlatformSettingsSnapshot:
        return PlatformSettingsSnapshot(
            revision=1, settings={"learning": {"learning_enabled": False}}
        )


def _runtime_app(settings) -> FastAPI:
    module = import_module("muad_agent_runtime.api.learning")
    runtime_app = FastAPI()
    install_api_foundation(runtime_app)
    runtime_app.state.platform_settings_client = settings
    runtime_app.include_router(module.router)
    return runtime_app


def _override(client: RuntimeLearningClient) -> None:
    console_app.state.runtime_learning_client = None
    console_app.dependency_overrides[get_runtime_learning_client] = lambda: client


def _clear() -> None:
    console_app.dependency_overrides.pop(get_runtime_learning_client, None)
    console_app.state.runtime_learning_client = None


async def _create_user(client: AsyncClient, tenant: TenantContext) -> str:
    created = await client.post(
        "/api/v1/users",
        json={"user_code": f"user-{uuid.uuid4().hex[:8]}", "display_name": "Degrade"},
        headers={"X-Tenant-Id": tenant.tenant_id},
    )
    return created.json()["data"]["id"]


async def _seed(tenant_id: str, user_id: str, key: str) -> None:
    async with get_session_factory()() as session:
        session.add(
            UserMemory(
                tenant_id=tenant_id,
                user_id=uuid.UUID(user_id),
                memory_key=key,
                category="PROCEDURE",
                content_json={"value": "做法"},
                author="AGENT",
                authority="AGENT_INFERRED",
                confidence=2,
                explicit_request=False,
                expires_at=datetime.now(UTC) + timedelta(days=365),
                activation_state="PROPOSED",
                task_signature="skill:demo",
                trigger_text="触发",
            )
        )
        await session.commit()


async def test_e01_reads_survive_and_writes_fail_loudly(
    client: AsyncClient, tenant: TenantContext, internal_service_token: None
) -> None:
    user_id = await _create_user(client, tenant)
    key = "exp.degrade"
    await _seed(tenant.tenant_id, user_id, key)

    disabled = RuntimeLearningClient(
        "http://runtime",
        service_token=INTERNAL_SERVICE_TOKEN,
        transport=httpx.ASGITransport(app=_runtime_app(_Disabled())),
    )
    _override(disabled)
    try:
        # 读取不依赖学习开关：经验列表/详情仍可用。
        listed = await client.get(
            "/api/v1/learning/experiences",
            params={"user_id": user_id},
            headers={"X-Tenant-Id": tenant.tenant_id},
        )
        assert listed.status_code == 200
        assert listed.json()["data"]["items"][0]["memory_key"] == key

        blocked = await client.post(
            f"/api/v1/learning/experiences/{user_id}/{key}/activate",
            headers={
                "X-Tenant-Id": tenant.tenant_id,
                "Idempotency-Key": str(uuid.uuid4()),
            },
            json={"reason": "x"},
        )
        assert blocked.status_code == 409
        assert blocked.json()["code"] == "LEARNING_DISABLED"
    finally:
        _clear()
        await disabled.aclose()

    unreachable = RuntimeLearningClient(
        "http://127.0.0.1:9",
        service_token=INTERNAL_SERVICE_TOKEN,
        timeout_sec=0.5,
    )
    _override(unreachable)
    try:
        failed = await client.post(
            f"/api/v1/learning/experiences/{user_id}/{key}/replay",
            headers={
                "X-Tenant-Id": tenant.tenant_id,
                "Idempotency-Key": str(uuid.uuid4()),
            },
        )
        assert failed.status_code >= 500
        assert failed.json()["code"] == "COMMON_INTERNAL_ERROR"
        # 不返回缓存旧状态：读取仍是数据库事实（PROPOSED）。
        detail = await client.get(
            f"/api/v1/learning/experiences/{user_id}/{key}",
            headers={"X-Tenant-Id": tenant.tenant_id},
        )
        assert detail.json()["data"]["activation_state"] == "PROPOSED"
    finally:
        _clear()
        await unreachable.aclose()
