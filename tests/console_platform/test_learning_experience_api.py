"""TASK-003：经验管理读模型（S-01/S-02/S-03/S-05/B-02）。"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import (
    CanonicalEvent,
    Conversation,
    EvalReport,
    FamilyRunStat,
    RunRecord,
    UserMemory,
    UserMemoryHistory,
)

from console_platform.conftest import TenantContext

ADMIN_PASSWORD = "console-admin-password"


def _headers(tenant: TenantContext) -> dict[str, str]:
    return {"X-Tenant-Id": tenant.tenant_id}


async def _create_user(client: AsyncClient, tenant: TenantContext) -> str:
    created = await client.post(
        "/api/v1/users",
        json={"user_code": f"user-{uuid.uuid4().hex[:8]}", "display_name": "Learning User"},
        headers=_headers(tenant),
    )
    assert created.status_code == 200
    return created.json()["data"]["id"]


async def _seed_experience(
    tenant_id: str,
    user_id: str,
    key: str,
    *,
    state: str = "PROPOSED",
    family: str = "skill:demo",
    category: str = "PROCEDURE",
    authority: str = "AGENT_INFERRED",
) -> None:
    async with get_session_factory()() as session:
        session.add(
            UserMemory(
                tenant_id=tenant_id,
                user_id=uuid.UUID(user_id),
                memory_key=key,
                category=category,
                content_json={"value": f"做法 {key}"},
                author="AGENT",
                authority=authority,
                confidence=2,
                explicit_request=False,
                expires_at=datetime.now(UTC) + timedelta(days=365),
                activation_state=state,
                task_signature=family,
                trigger_text=f"触发 {key}",
            )
        )
        await session.commit()


async def _seed_report(
    tenant_id: str,
    key: str,
    *,
    user_id: str | None,
    family: str = "skill:demo",
    status: str = "PASSED",
) -> None:
    async with get_session_factory()() as session:
        session.add(
            EvalReport(
                tenant_id=tenant_id,
                task_family=family,
                experience_key=key,
                experience_user_id=uuid.UUID(user_id) if user_id else None,
                base_config_hash="base-1",
                metrics_json={"cases": 2, "passed": 2, "baseline": {"cases": 2, "passed": 1}},
                decision_json={"status": status, "improvement": 0.5, "limits": {"min_cases": 1}},
                report_json={"cases": [{"case_id": "c1", "item_count": 1}]},
                content_hash=f"hash-{uuid.uuid4()}",
                version=1,
            )
        )
        await session.commit()


async def _seed_stat(tenant_id: str, family: str, count: int, *, day: date) -> None:
    async with get_session_factory()() as session:
        session.add(
            FamilyRunStat(
                tenant_id=tenant_id,
                family=family,
                day=day,
                run_count=count,
                last_run_at=datetime.now(UTC),
            )
        )
        await session.commit()


async def _seed_run(
    tenant_id: str, user_id: str, family: str | None
) -> tuple[uuid.UUID, uuid.UUID]:
    async with get_session_factory()() as session:
        conversation = Conversation(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            user_id=uuid.UUID(user_id),
            agent_id=uuid.uuid4(),
        )
        session.add(conversation)
        await session.flush()
        run = RunRecord(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            conversation_id=conversation.id,
            user_id=uuid.UUID(user_id),
            agent_id=uuid.uuid4(),
            status="COMPLETED",
            input_text="测试",
            trace_id=f"trace-{uuid.uuid4()}",
            end_time=datetime.now(UTC),
            task_family=family,
        )
        session.add(run)
        await session.commit()
        return run.id, conversation.id


@pytest.mark.asyncio
async def test_s01_user_experience_list_filters_and_scope(
    client: AsyncClient, tenant: TenantContext
) -> None:
    user_id = await _create_user(client, tenant)
    other = await _create_user(client, tenant)
    for key, state, family in [
        ("exp.a", "ACTIVE", "skill:demo"),
        ("exp.b", "TRIAL", "skill:demo"),
        ("exp.c", "PROPOSED", "agent:x"),
        ("exp.d", "RETIRED", "skill:other"),
    ]:
        await _seed_experience(tenant.tenant_id, user_id, key, state=state, family=family)
    await _seed_experience(tenant.tenant_id, other, "exp.a", state="ACTIVE")
    await _seed_report(tenant.tenant_id, "exp.a", user_id=user_id)

    response = await client.get(
        "/api/v1/learning/experiences", params={"user_id": user_id}, headers=_headers(tenant)
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 4
    assert data["counts"]["ACTIVE"] == 1 and data["counts"]["total"] == 4
    keys = {item["memory_key"] for item in data["items"]}
    assert "exp.a" in keys and all(item["user_id"] == user_id for item in data["items"])
    latest = next(item for item in data["items"] if item["memory_key"] == "exp.a")
    assert latest["latest_report"]["status"] == "PASSED"

    filtered = await client.get(
        "/api/v1/learning/experiences",
        params={"user_id": user_id, "state": "TRIAL", "family": "skill:demo"},
        headers=_headers(tenant),
    )
    assert [item["memory_key"] for item in filtered.json()["data"]["items"]] == ["exp.b"]

    missing = await client.get(
        "/api/v1/learning/experiences",
        params={"user_id": str(uuid.uuid4())},
        headers=_headers(tenant),
    )
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_s02_family_aggregation_and_cross_view_consistency(
    client: AsyncClient, tenant: TenantContext
) -> None:
    user_a = await _create_user(client, tenant)
    user_b = await _create_user(client, tenant)
    await _seed_experience(tenant.tenant_id, user_a, "exp.a", state="ACTIVE")
    await _seed_experience(tenant.tenant_id, user_a, "exp.a2", state="TRIAL")
    await _seed_experience(tenant.tenant_id, user_b, "exp.a", state="PROPOSED")
    await _seed_experience(tenant.tenant_id, user_b, "exp.other", state="ACTIVE", family="agent:x")
    await _seed_report(tenant.tenant_id, "exp.a", user_id=user_a)
    await _seed_stat(tenant.tenant_id, "skill:demo", 7, day=date.today())
    await _seed_stat(tenant.tenant_id, "agent:x", 3, day=date.today())
    await _seed_run(tenant.tenant_id, user_a, None)

    families = await client.get("/api/v1/learning/families", headers=_headers(tenant))
    assert families.status_code == 200
    body = families.json()["data"]
    demo = next(item for item in body["items"] if item["family"] == "skill:demo")
    assert demo["source"] == "skill" and demo["run_count"] == 7
    assert demo["states"]["ACTIVE"] == 1 and demo["states"]["TRIAL"] == 1 and demo["states"]["PROPOSED"] == 1
    assert demo["pass_rate"] == {"passed": 1, "total": 1}
    assert body["unclassified_runs"] == 1

    detail = await client.get("/api/v1/learning/families/skill:demo", headers=_headers(tenant))
    assert detail.status_code == 200
    grouped = {entry["user_id"]: entry["items"] for entry in detail.json()["data"]["experiences"]}
    assert len(grouped[user_a]) == 2 and len(grouped[user_b]) == 1

    user_view = await client.get(
        "/api/v1/learning/experiences", params={"user_id": user_a}, headers=_headers(tenant)
    )
    item = next(i for i in user_view.json()["data"]["items"] if i["memory_key"] == "exp.a")
    detail_item = next(i for i in grouped[user_a] if i["memory_key"] == "exp.a")
    assert item["activation_state"] == detail_item["activation_state"] == "ACTIVE"
    assert item["version"] == detail_item["version"]


@pytest.mark.asyncio
async def test_s03_detail_explains_evidence_reports_injections_and_audits(
    client: AsyncClient, tenant: TenantContext
) -> None:
    user_id = await _create_user(client, tenant)
    await _seed_experience(tenant.tenant_id, user_id, "exp.detail", state="ACTIVE", authority="USER_EXPLICIT")
    await _seed_report(tenant.tenant_id, "exp.detail", user_id=user_id)
    await _seed_report(tenant.tenant_id, "exp.detail", user_id=None, status="BASELINE")
    run_id, conversation_id = await _seed_run(tenant.tenant_id, user_id, "skill:demo")
    async with get_session_factory()() as session:
        session.add(
            CanonicalEvent(
                tenant_id=tenant.tenant_id,
                conversation_id=conversation_id,
                run_id=run_id,
                seq=1,
                event_type="MEMORY_INJECTED",
                payload_json={
                    "items": [
                        {
                            "memory_key": "exp.detail",
                            "version": 1,
                            "score": 0.9,
                            "channel": "FAMILY",
                            "decision": "INJECTED",
                        }
                    ],
                    "selection_hash": "hash",
                },
            )
        )
        session.add(
            UserMemoryHistory(
                tenant_id=tenant.tenant_id,
                user_id=uuid.UUID(user_id),
                memory_key="exp.detail",
                origin_id=(
                    await session.execute(
                        sa.text("SELECT id FROM runtime.user_memory WHERE memory_key='exp.detail'")
                    )
                ).scalar_one(),
                version=2,
                change_reason="ACTIVATION",
                actor_type="ADMIN",
                actor_id="admin",
                command_id="cmd",
                after_json={"activation_state": "ACTIVE"},
            )
        )
        await session.execute(
            sa.text(
                "INSERT INTO control.config_audit_log"
                " (tenant_id, actor_user_id, resource_type, resource_id, action, before_json, after_json)"
                " VALUES (:t, :a, 'MEMORY_EXPERIENCE', :r, 'ACTIVATE', '{}'::jsonb,"
                " CAST(:after AS jsonb))"
            ),
            {
                "t": tenant.tenant_id,
                "a": uuid.uuid4(),
                "r": uuid.uuid4(),
                "after": f'{{"experience_key":"exp.detail","user_id":"{user_id}"}}',
            },
        )
        await session.commit()

    response = await client.get(
        "/api/v1/learning/experiences/" + user_id + "/exp.detail", headers=_headers(tenant)
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["authority"] == "USER_EXPLICIT"
    assert data["reports"][0]["owner"] == "OWNED"
    assert any(report["owner"] == "UNKNOWN" for report in data["reports"])
    assert data["injections"][0]["score"] == 0.9 and data["injections"][0]["channel"] == "FAMILY"
    assert data["audits"] and data["history"]


@pytest.mark.asyncio
async def test_s05_memory_list_and_counts_exclude_experiences(
    client: AsyncClient, tenant: TenantContext
) -> None:
    user_id = await _create_user(client, tenant)
    async with get_session_factory()() as session:
        session.add(
            UserMemory(
                tenant_id=tenant.tenant_id,
                user_id=uuid.UUID(user_id),
                memory_key="pref.one",
                category="PREFERENCE",
                content_json={"value": "简洁"},
                author="AGENT",
                confidence=2,
                explicit_request=False,
                expires_at=datetime.now(UTC) + timedelta(days=30),
            )
        )
        await session.commit()
    await _seed_experience(tenant.tenant_id, user_id, "exp.one", state="ACTIVE")

    memory = await client.get(f"/api/v1/users/{user_id}/memory", headers=_headers(tenant))
    keys = {item["memory_key"] for item in memory.json()["data"]["items"]}
    assert keys == {"pref.one"}
    detail = await client.get(f"/api/v1/users/{user_id}", headers=_headers(tenant))
    assert detail.json()["data"]["memory_count"] == 1
    assert detail.json()["data"]["experience_count"] == 1

    rejected = await client.post(
        f"/api/v1/users/{user_id}/memory",
        json={"memory_key": "exp.via.admin", "category": "PROCEDURE", "value": "x"},
        headers={**_headers(tenant), "Idempotency-Key": str(uuid.uuid4())},
    )
    assert rejected.status_code == 422


@pytest.mark.asyncio
async def test_b02_aggregate_boundaries(client: AsyncClient, tenant: TenantContext) -> None:
    user_id = await _create_user(client, tenant)
    await _seed_experience(tenant.tenant_id, user_id, "exp.unknown", family="weird:family")
    await _seed_run(tenant.tenant_id, user_id, None)
    body = (
        await client.get("/api/v1/learning/families", headers=_headers(tenant))
    ).json()["data"]
    unknown = next(item for item in body["items"] if item["family"] == "weird:family")
    assert unknown["source"] == "unknown"
    assert body["unclassified_runs"] >= 1
