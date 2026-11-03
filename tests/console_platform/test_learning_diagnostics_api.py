"""TASK-007 / S-09：Run 学习诊断忠实呈现。"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from httpx import AsyncClient
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import CanonicalEvent

from console_platform.conftest import TenantContext
from tests.agent_runtime.learning_wiring_support import seed_runs


async def _inject(
    tenant_id: str, run_id: uuid.UUID, conversation_id: uuid.UUID, *, diagnostics: bool
) -> None:
    async with get_session_factory()() as session:
        seq = int(
            await session.scalar(
                sa.text(
                    "SELECT COALESCE(max(seq), 0) + 1 FROM runtime.canonical_event"
                    " WHERE conversation_id=:conversation_id"
                ),
                {"conversation_id": conversation_id},
            )
        )
        payload = {
            "items": [
                {
                    "memory_key": "exp.a",
                    "version": 1,
                    "score": 0.9,
                    "channel": "FAMILY",
                    "decision": "INJECTED",
                }
            ],
            "selection_hash": "hash",
        }
        if diagnostics:
            payload["diagnostics"] = {
                "learning_enabled": True,
                "min_score": 0.35,
                "allowed_experience_keys": 1,
                "skipped": [
                    {
                        "memory_key": "exp.b",
                        "activation_state": "PROPOSED",
                        "reason": "NOT_ACTIVE",
                        "score": 0.0,
                    }
                ],
                "dropped_by_budget": [{"memory_key": "exp.c", "version": 1}],
            }
        session.add(
            CanonicalEvent(
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                run_id=run_id,
                seq=seq,
                event_type="MEMORY_INJECTED",
                payload_json=payload,
            )
        )
        await session.commit()


async def test_s09_diagnostics_records_real_reasons_and_history(
    client: AsyncClient, tenant: TenantContext
) -> None:
    user = uuid.uuid4()
    runs = await seed_runs(tenant.tenant_id, user, 1)
    run_id = runs[0]
    async with get_session_factory()() as session:
        conversation_id = await session.scalar(
            sa.text("SELECT conversation_id FROM runtime.run_record WHERE id=:id"), {"id": run_id}
        )
    await _inject(tenant.tenant_id, run_id, conversation_id, diagnostics=True)

    response = await client.get(
        f"/api/v1/learning/runs/{run_id}/diagnostics",
        headers={"X-Tenant-Id": tenant.tenant_id},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["recorded"] is True
    assert data["task_family"] == "skill:demo"
    assert data["learning_enabled"] is True
    assert data["injected"][0]["channel"] == "FAMILY"
    assert data["skipped"][0]["reason"] == "NOT_ACTIVE"
    assert data["dropped_by_budget"][0]["memory_key"] == "exp.c"

    historical_user = uuid.uuid4()
    historical = (await seed_runs(tenant.tenant_id, historical_user, 1))[0]
    async with get_session_factory()() as session:
        historical_conversation = await session.scalar(
            sa.text("SELECT conversation_id FROM runtime.run_record WHERE id=:id"),
            {"id": historical},
        )
    await _inject(tenant.tenant_id, historical, historical_conversation, diagnostics=False)
    old = await client.get(
        f"/api/v1/learning/runs/{historical}/diagnostics",
        headers={"X-Tenant-Id": tenant.tenant_id},
    )
    assert old.json()["data"]["recorded"] is False
    assert old.json()["data"]["skipped"] == []
