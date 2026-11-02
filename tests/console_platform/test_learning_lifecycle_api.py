"""TASK-004 / S-04：Console → Runtime（真实 ASGI 路由）生命周期闭环。"""

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
from muad_agent_runtime.application.learning_jobs import LearningJobRunner
from muad_agent_runtime.application.learning_replay import (
    CaseOutcome,
    LearningReplay,
)
from muad_agent_runtime.application.ports import PlatformSettingsSnapshot
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import (
    EvalCase,
    MemoryCommandReceipt,
    UserMemory,
    UserMemoryHistory,
)
from muad_api import install_api_foundation
from muad_console_platform.api.deps import get_runtime_learning_client
from muad_console_platform.infrastructure.runtime_learning_client import RuntimeLearningClient
from muad_console_platform.main import app as console_app

from console_platform.conftest import TenantContext
from tests.agent_runtime.learning_wiring_support import seed_runs
from tests.internal_service import TOKEN as INTERNAL_SERVICE_TOKEN


class _Settings:
    async def fetch_snapshot(self, *, tenant_id: str, trace_id: str = "") -> PlatformSettingsSnapshot:
        return PlatformSettingsSnapshot(
            revision=1,
            settings={"learning": {"learning_enabled": True, "eval_min_cases": 1}},
        )


def _runtime_app() -> FastAPI:
    module = import_module("muad_agent_runtime.api.learning")
    runtime_app = FastAPI()
    install_api_foundation(runtime_app)
    runtime_app.state.platform_settings_client = _Settings()
    runtime_app.include_router(module.router)
    return runtime_app


class _Runner:
    async def __call__(self, case, experience_key, experience_user_id) -> CaseOutcome:
        return CaseOutcome(experience_key is not None, "", 1, 1 if experience_key else 0)


async def _seed(tenant_id: str, user_id: str, key: str) -> None:
    runs = await seed_runs(tenant_id, uuid.UUID(user_id), 2)
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
        for index, run_id in enumerate(runs):
            session.add(
                EvalCase(
                    tenant_id=tenant_id,
                    task_family="skill:demo",
                    source_run_id=run_id,
                    input_ref_json={"input": "x"},
                    criteria_json={"items": []},
                    item_count=1,
                    content_hash=f"hash-{index}-{uuid.uuid4()}",
                    version=1,
                )
            )
        await session.commit()


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


def _override(client: RuntimeLearningClient):
    console_app.state.runtime_learning_client = None
    console_app.dependency_overrides[get_runtime_learning_client] = lambda: client


def _clear() -> None:
    console_app.dependency_overrides.pop(get_runtime_learning_client, None)
    console_app.state.runtime_learning_client = None


async def test_s04_lifecycle_through_console_and_runtime(
    client: AsyncClient, tenant: TenantContext, learning_client: RuntimeLearningClient
) -> None:
    created = await client.post(
        "/api/v1/users",
        json={"user_code": f"user-{uuid.uuid4().hex[:8]}", "display_name": "Lifecycle"},
        headers={"X-Tenant-Id": tenant.tenant_id},
    )
    user_id = created.json()["data"]["id"]
    key = "exp.lifecycle"
    await _seed(tenant.tenant_id, user_id, key)
    _override(learning_client)
    headers = {"X-Tenant-Id": tenant.tenant_id, "Idempotency-Key": str(uuid.uuid4())}
    try:
        replay = await client.post(
            f"/api/v1/learning/experiences/{user_id}/{key}/replay", headers=headers
        )
        assert replay.status_code == 202, replay.text
        job_id = replay.json()["data"]["id"]
        settings = _Settings()
        runner = LearningJobRunner(
            get_session_factory(),
            LearningReplay(get_session_factory(), _Runner()),
            settings_provider=lambda tenant: _learning_settings(settings, tenant),
        )
        assert await runner.run_once() is True
        while await runner.run_once():
            pass
        status = await client.get(
            f"/api/v1/learning/jobs/{job_id}", headers={"X-Tenant-Id": tenant.tenant_id}
        )
        assert status.json()["data"]["status"] == "SUCCEEDED"

        activated = await client.post(
            f"/api/v1/learning/experiences/{user_id}/{key}/activate",
            headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
            json={"reason": "回放通过"},
        )
        assert activated.status_code == 200, activated.text
        assert activated.json()["data"]["activation_state"] == "ACTIVE"

        async with get_session_factory()() as session:
            row = await session.scalar(
                sa.select(UserMemory).where(
                    UserMemory.tenant_id == tenant.tenant_id,
                    UserMemory.user_id == uuid.UUID(user_id),
                    UserMemory.memory_key == key,
                )
            )
            history = await session.scalar(
                sa.select(sa.func.count())
                .select_from(UserMemoryHistory)
                .where(UserMemoryHistory.tenant_id == tenant.tenant_id)
            )
            receipts = await session.scalar(
                sa.select(sa.func.count())
                .select_from(MemoryCommandReceipt)
                .where(MemoryCommandReceipt.tenant_id == tenant.tenant_id)
            )
        assert row is not None and row.activation_state == "ACTIVE" and row.version >= 2
        assert int(history or 0) >= 1 and int(receipts or 0) >= 1

        retired = await client.post(
            f"/api/v1/learning/experiences/{user_id}/{key}/retire",
            headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
            json={"reason": "degraded"},
        )
        assert retired.json()["data"]["activation_state"] == "RETIRED"
        regenerated = await client.post(
            f"/api/v1/learning/experiences/{user_id}/{key}/regenerate",
            headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
            json={"reason": "重新沉淀"},
        )
        assert regenerated.json()["data"]["activation_state"] == "PROPOSED"
    finally:
        _clear()


async def _learning_settings(settings: _Settings, tenant: str):
    from muad_contracts.platform_settings import parse_platform_settings

    snapshot = await settings.fetch_snapshot(tenant_id=tenant)
    return parse_platform_settings(snapshot.settings).learning
