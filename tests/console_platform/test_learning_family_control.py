"""TASK-009 / S-12：Run 止损与族级暂停。"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from importlib import import_module

import httpx
import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from httpx import AsyncClient
from muad_agent_runtime.application.memory_cache import MemoryCandidateCache
from muad_agent_runtime.application.memory_context import MemoryContextLoader
from muad_agent_runtime.application.memory_queries import MemoryQueries, MemoryReadScope
from muad_agent_runtime.application.ports import PlatformSettingsSnapshot
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import (
    CanonicalEvent,
    LearningFamilyControl,
    UserMemory,
)
from muad_api import install_api_foundation
from muad_console_platform.api.deps import get_runtime_learning_client
from muad_console_platform.infrastructure.runtime_learning_client import RuntimeLearningClient
from muad_console_platform.main import app as console_app
from muad_contracts.platform_settings import MemoryPolicySettings

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
        for key in ("exp.stop", "exp.keep"):
            session.add(
                UserMemory(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    memory_key=key,
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
                            "memory_key": "exp.stop",
                            "version": 1,
                            "score": 1.0,
                            "channel": "FAMILY",
                            "decision": "INJECTED",
                        }
                    ],
                    "selection_hash": "hash",
                },
            )
        )
        await session.commit()
    return run_id


@pytest.fixture
async def learning_client(internal_service_token: None) -> AsyncIterator[RuntimeLearningClient]:
    client = RuntimeLearningClient(
        "http://runtime",
        service_token=INTERNAL_SERVICE_TOKEN,
        transport=httpx.ASGITransport(app=_runtime_app()),
    )
    try:
        yield client
    finally:
        await client.aclose()


async def test_s12_stop_run_and_pause_family(
    client: AsyncClient,
    tenant: TenantContext,
    learning_client: RuntimeLearningClient,
) -> None:
    user_id = uuid.uuid4()
    run_id = await _seed(tenant.tenant_id, user_id)
    console_app.state.runtime_learning_client = None
    console_app.dependency_overrides[get_runtime_learning_client] = lambda: learning_client
    try:
        stopped = await client.post(
            f"/api/v1/learning/runs/{run_id}/stop",
            headers={
                "X-Tenant-Id": tenant.tenant_id,
                "Idempotency-Key": str(uuid.uuid4()),
            },
            json={"reason": "run 止损"},
        )
        assert stopped.status_code == 200, stopped.text
        results = stopped.json()["data"]["items"]
        assert results[0]["memory_key"] == "exp.stop"
        assert results[0]["activation_state"] == "RETIRED"

        paused = await client.post(
            "/api/v1/learning/families/skill:demo/control",
            headers={
                "X-Tenant-Id": tenant.tenant_id,
                "Idempotency-Key": str(uuid.uuid4()),
            },
            json={"paused": True, "reason": "止损"},
        )
        assert paused.status_code == 200, paused.text
        async with get_session_factory()() as session:
            row = await session.scalar(
                sa.select(LearningFamilyControl).where(
                    LearningFamilyControl.tenant_id == tenant.tenant_id,
                    LearningFamilyControl.family == "skill:demo",
                )
            )
        assert row is not None and row.paused is True

        # 暂停后注入冻结集合不再包含该族经验。
        scope = MemoryReadScope(tenant_id=tenant.tenant_id, user_id=user_id)
        loader = MemoryContextLoader(
            MemoryCandidateCache(MemoryQueries(get_session_factory()), scope=scope, run_id=run_id),
            settings=MemoryPolicySettings(),
            query="触发",
        )
        loader.set_task_family("skill:demo")
        component = await loader.refresh()
        keys = [] if component is None else [item.record.memory_key for item in component.block.items]
        assert "exp.keep" not in keys
    finally:
        console_app.dependency_overrides.pop(get_runtime_learning_client, None)
        console_app.state.runtime_learning_client = None
        await cleanup(tenant.tenant_id)
