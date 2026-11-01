"""TASK-010 / S-13：经验内容修订。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from importlib import import_module

import httpx
import sqlalchemy as sa
from fastapi import FastAPI
from httpx import AsyncClient
from muad_agent_runtime.application.ports import PlatformSettingsSnapshot
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import (
    EvalReport,
    UserMemory,
    UserMemoryHistory,
)
from muad_api import install_api_foundation
from muad_console_platform.api.deps import get_runtime_learning_client
from muad_console_platform.infrastructure.runtime_learning_client import RuntimeLearningClient
from muad_console_platform.main import app as console_app

from console_platform.conftest import TenantContext
from tests.internal_service import TOKEN as INTERNAL_SERVICE_TOKEN


class _Settings:
    async def fetch_snapshot(self, *, tenant_id: str, trace_id: str = "") -> PlatformSettingsSnapshot:
        return PlatformSettingsSnapshot(
            revision=1, settings={"learning": {"learning_enabled": True}}
        )


def _runtime_app() -> FastAPI:
    module = import_module("muad_agent_runtime.api.learning")
    runtime_app = FastAPI()
    install_api_foundation(runtime_app)
    runtime_app.state.platform_settings_client = _Settings()
    runtime_app.include_router(module.router)
    return runtime_app


async def _seed(tenant_id: str, user_id: str, key: str) -> None:
    async with get_session_factory()() as session:
        session.add(
            UserMemory(
                tenant_id=tenant_id,
                user_id=uuid.UUID(user_id),
                memory_key=key,
                category="PROCEDURE",
                content_json={"value": "旧做法"},
                author="AGENT",
                authority="AGENT_INFERRED",
                confidence=2,
                explicit_request=False,
                expires_at=datetime.now(UTC) + timedelta(days=365),
                activation_state="ACTIVE",
                task_signature="skill:demo",
                trigger_text="触发",
            )
        )
        await session.commit()


async def test_s13_revision_creates_version_and_requires_replay(
    client: AsyncClient, tenant: TenantContext, internal_service_token: None
) -> None:
    created = await client.post(
        "/api/v1/users",
        json={"user_code": f"user-{uuid.uuid4().hex[:8]}", "display_name": "Revise"},
        headers={"X-Tenant-Id": tenant.tenant_id},
    )
    user_id = created.json()["data"]["id"]
    key = "exp.revise"
    await _seed(tenant.tenant_id, user_id, key)
    runtime = RuntimeLearningClient(
        "http://runtime",
        service_token=INTERNAL_SERVICE_TOKEN,
        transport=httpx.ASGITransport(app=_runtime_app()),
    )
    console_app.state.runtime_learning_client = None
    console_app.dependency_overrides[get_runtime_learning_client] = lambda: runtime
    headers = {"X-Tenant-Id": tenant.tenant_id, "Idempotency-Key": str(uuid.uuid4())}
    try:
        revised = await client.patch(
            f"/api/v1/learning/experiences/{user_id}/{key}/content",
            headers=headers,
            json={"value": "新做法", "expected_version": 1, "reason": "修正"},
        )
        assert revised.status_code == 200, revised.text
        assert revised.json()["data"]["activation_state"] == "PROPOSED"
        async with get_session_factory()() as session:
            row = await session.scalar(
                sa.select(UserMemory).where(
                    UserMemory.tenant_id == tenant.tenant_id,
                    UserMemory.memory_key == key,
                )
            )
            history = await session.scalar(
                sa.select(sa.func.count())
                .select_from(UserMemoryHistory)
                .where(UserMemoryHistory.tenant_id == tenant.tenant_id)
            )
        assert row is not None and row.version >= 2 and row.content_json["value"] == "新做法"
        assert row.authority == "ADMIN_EDIT" and int(history or 0) >= 1

        conflict = await client.patch(
            f"/api/v1/learning/experiences/{user_id}/{key}/content",
            headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
            json={"value": "再次", "expected_version": 1, "reason": "冲突"},
        )
        assert conflict.status_code == 409

        blocked = await client.post(
            f"/api/v1/learning/experiences/{user_id}/{key}/activate",
            headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
            json={"reason": "晋升"},
        )
        assert blocked.status_code == 409
        assert blocked.json()["code"] == "LEARNING_REPLAY_GATE_FAILED"

        # 回放通过后（该用户、正确 base hash）才可晋升。
        import hashlib

        base_hash = "sha256:" + hashlib.sha256(
            f"{tenant.tenant_id}:skill:demo".encode()
        ).hexdigest()[:16]
        async with get_session_factory()() as session:
            session.add(
                EvalReport(
                    tenant_id=tenant.tenant_id,
                    task_family="skill:demo",
                    experience_key=key,
                    experience_user_id=uuid.UUID(user_id),
                    base_config_hash=base_hash,
                    metrics_json={},
                    decision_json={"status": "PASSED"},
                    report_json={},
                    content_hash=f"hash-{uuid.uuid4()}",
                    version=1,
                )
            )
            await session.commit()
        activated = await client.post(
            f"/api/v1/learning/experiences/{user_id}/{key}/activate",
            headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
            json={"reason": "回放通过"},
        )
        assert activated.status_code == 200, activated.text
        assert activated.json()["data"]["activation_state"] == "ACTIVE"
    finally:
        console_app.dependency_overrides.pop(get_runtime_learning_client, None)
        console_app.state.runtime_learning_client = None
        await runtime.aclose()
