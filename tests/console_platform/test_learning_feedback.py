"""TASK-011 / S-14：差回答反馈幂等进入学习信号。"""

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
    CanonicalEvent,
    LearningJob,
    UserMemory,
)
from muad_api import install_api_foundation
from muad_console_platform.api.deps import get_runtime_learning_client
from muad_console_platform.infrastructure.runtime_learning_client import RuntimeLearningClient
from muad_console_platform.main import app as console_app

from console_platform.conftest import TenantContext
from tests.agent_runtime.learning_wiring_support import cleanup, seed_runs
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


async def _seed(tenant_id: str, user_id: uuid.UUID) -> uuid.UUID:
    run_id = (await seed_runs(tenant_id, user_id, 1))[0]
    async with get_session_factory()() as session:
        conversation_id = await session.scalar(
            sa.text("SELECT conversation_id FROM runtime.run_record WHERE id=:id"), {"id": run_id}
        )
        session.add(
            UserMemory(
                tenant_id=tenant_id,
                user_id=user_id,
                memory_key="exp.feedback",
                category="PROCEDURE",
                content_json={"value": "做法"},
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
        session.add(
            CanonicalEvent(
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                run_id=run_id,
                seq=999,
                event_type="MEMORY_INJECTED",
                payload_json={
                    "items": [
                        {
                            "memory_key": "exp.feedback",
                            "version": 1,
                            "score": 1.0,
                            "channel": "FAMILY",
                        }
                    ]
                },
            )
        )
        await session.commit()
    return run_id


async def test_s14_feedback_is_idempotent_and_enqueues_review(
    client: AsyncClient, tenant: TenantContext, internal_service_token: None
) -> None:
    user_id = uuid.uuid4()
    run_id = await _seed(tenant.tenant_id, user_id)
    runtime = RuntimeLearningClient(
        "http://runtime",
        service_token=INTERNAL_SERVICE_TOKEN,
        transport=httpx.ASGITransport(app=_runtime_app()),
    )
    console_app.state.runtime_learning_client = None
    console_app.dependency_overrides[get_runtime_learning_client] = lambda: runtime
    headers = {"X-Tenant-Id": tenant.tenant_id, "Idempotency-Key": str(uuid.uuid4())}
    try:
        first = await client.post(
            f"/api/v1/learning/runs/{run_id}/feedback",
            headers=headers,
            json={"reason": "回答不对"},
        )
        assert first.status_code == 200, first.text
        again = await client.post(
            f"/api/v1/learning/runs/{run_id}/feedback",
            headers=headers,
            json={"reason": "回答不对"},
        )
        assert again.json()["data"] == first.json()["data"]
        async with get_session_factory()() as session:
            signals = await session.scalar(
                sa.select(sa.func.count())
                .select_from(CanonicalEvent)
                .where(
                    CanonicalEvent.tenant_id == tenant.tenant_id,
                    CanonicalEvent.event_type == "LEARNING_SIGNAL",
                )
            )
            jobs = await session.scalar(
                sa.select(sa.func.count())
                .select_from(LearningJob)
                .where(LearningJob.tenant_id == tenant.tenant_id)
            )
        assert int(signals or 0) == 1
        assert int(jobs or 0) >= 1
    finally:
        console_app.dependency_overrides.pop(get_runtime_learning_client, None)
        console_app.state.runtime_learning_client = None
        await runtime.aclose()
        await cleanup(tenant.tenant_id)
