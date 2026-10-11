"""Historical model nodes: frozen identity and explicitly scoped Run audit totals."""

import json
import uuid
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from muad_agent_runtime.infrastructure.models.runtime import ModelInvocationAudit, RuntimeSnapshot
from muad_console_platform.infrastructure.db import get_session_factory
from sqlalchemy import text

from console_platform import test_run_event_details as event_fixtures
from console_platform.conftest import TenantContext
from console_platform.test_run_event_details import read_detail, seed_event
from console_platform.test_runs_api import RunSeed

cleanup_event_details = event_fixtures.cleanup_event_details
run = event_fixtures.run


def audit(owner: str, run: RunSeed, **overrides: object) -> ModelInvocationAudit:
    values: dict[str, object] = {
        "tenant_id": owner,
        "run_id": run.run_id,
        "provider": "openai-compatible",
        "model": "audited-model",
        "attempt": 0,
        "status": "OK",
    }
    return ModelInvocationAudit(**(values | overrides))


async def seed_snapshot(tenant: TenantContext, run: RunSeed, mode: str = "valid") -> None:
    snapshot_id = uuid.uuid4()
    async with get_session_factory()() as session:
        session.add(
            RuntimeSnapshot(
                id=snapshot_id,
                tenant_id=f"{tenant.tenant_id}-other" if mode == "other-tenant" else tenant.tenant_id,
                run_id=uuid.uuid4() if mode == "other-run" else run.run_id,
                agent_revision=1,
                model_revision=7,
                agent_json={},
                model_json={
                    "model_id": "frozen-model",
                    "protocol": "OPENAI",
                    "api_key": "canary-snapshot-key",
                    "base_url": "https://canary-private-host.example/v1",
                    "params_json": {"password": "canary-snapshot-param"},
                },
                skill_catalog_json=[],
                mcp_catalog_json=[],
                prompt_template_version="v1",
                content_hash=f"sha256:{snapshot_id.hex}",
                is_deleted=mode == "deleted",
            )
        )
        await session.execute(
            text(
                "UPDATE runtime.run_record SET snapshot_id = :snapshot, update_time = now() "
                "WHERE id = :run AND tenant_id = :tenant"
            ),
            {"snapshot": snapshot_id, "run": run.run_id, "tenant": tenant.tenant_id},
        )
        await session.commit()


async def test_historical_model_uses_frozen_identity_not_current_definition(
    client: AsyncClient, tenant: TenantContext, run: RunSeed
) -> None:
    await seed_snapshot(tenant, run)
    await seed_event(tenant, run, "MODEL_CALL_COMPLETED", {})
    async with get_session_factory()() as session:
        await session.execute(
            text(
                "UPDATE control.model_definition SET model_id = :model, revision = 99, "
                "update_time = :now WHERE tenant_id = :tenant AND id = :id"
            ),
            {
                "model": "canary-current-model",
                "now": datetime.now(UTC),
                "tenant": tenant.tenant_id,
                "id": tenant.model_id,
            },
        )
        await session.commit()
    detail = await read_detail(client, run)
    assert {field["key"]: field["value"] for field in detail["fields"]} == {
        "model": "frozen-model",
        "protocol": "OPENAI",
        "model_revision": 7,
    }
    assert detail["model_state"] == "UNLINKED" and detail["model_summary"] is None
    assert "canary" not in json.dumps(detail)
    assert all(key not in detail for key in ("model_json", "params_json", "base_url", "api_key"))


async def test_historical_model_summary_counts_statuses_and_preserves_recorded_zero(
    client: AsyncClient, tenant: TenantContext, run: RunSeed
) -> None:
    await seed_event(tenant, run, "MODEL_CALL_COMPLETED", {})
    async with get_session_factory()() as session:
        session.add_all(
            [
                audit(tenant.tenant_id, run, input_tokens=0, output_tokens=0, latency_ms=0),
                audit(tenant.tenant_id, run, status="OK", input_tokens=12, output_tokens=3, latency_ms=400),
                audit(tenant.tenant_id, run, status="RETRY", attempt=1),
                audit(tenant.tenant_id, run, status="ERROR", attempt=2, latency_ms=25),
            ]
        )
        await session.commit()
    detail = await read_detail(client, run)
    assert detail["model_state"] == "UNLINKED" and detail["fields"] == []
    assert detail["model_summary"] == {
        "call_count": 4,
        "success_count": 2,
        "retry_count": 1,
        "error_count": 1,
        "input_tokens": 12,
        "output_tokens": 3,
        "duration_ms": 425,
    }


@pytest.mark.parametrize("recorded", [None, 0])
async def test_historical_model_summary_distinguishes_unknown_from_zero(
    client: AsyncClient, tenant: TenantContext, run: RunSeed, recorded: int | None
) -> None:
    await seed_event(tenant, run, "MODEL_CALL_STARTED", {})
    async with get_session_factory()() as session:
        session.add(
            audit(tenant.tenant_id, run, input_tokens=recorded, output_tokens=recorded, latency_ms=recorded)
        )
        await session.commit()
    summary = (await read_detail(client, run))["model_summary"]
    assert summary == {
        "call_count": 1,
        "success_count": 1,
        "retry_count": 0,
        "error_count": 0,
        "input_tokens": recorded,
        "output_tokens": recorded,
        "duration_ms": recorded,
    }


async def test_historical_summary_excludes_other_tenant_run_and_deleted_audits(
    client: AsyncClient, tenant: TenantContext, run: RunSeed
) -> None:
    await seed_event(tenant, run, "MODEL_CALL_COMPLETED", {})
    async with get_session_factory()() as session:
        session.add_all(
            [
                audit(tenant.tenant_id, run, input_tokens=2),
                audit(f"{tenant.tenant_id}-other", run, input_tokens=10000),
                audit(tenant.tenant_id, run, run_id=uuid.uuid4(), input_tokens=10000),
                audit(tenant.tenant_id, run, is_deleted=True, input_tokens=10000),
            ]
        )
        await session.commit()
    detail = await read_detail(client, run)
    assert detail["model_summary"]["call_count"] == 1
    assert detail["model_summary"]["input_tokens"] == 2
    assert detail["fields"] == []


@pytest.mark.parametrize("mode", ["other-tenant", "other-run", "deleted"])
async def test_foreign_or_deleted_snapshot_never_supplies_model_identity(
    client: AsyncClient, tenant: TenantContext, run: RunSeed, mode: str
) -> None:
    await seed_snapshot(tenant, run, mode)
    await seed_event(tenant, run, "MODEL_CALL_STARTED", {})
    detail = await read_detail(client, run)
    assert detail["fields"] == []
    assert detail["model_state"] == "UNLINKED" and detail["model_summary"] is None
    assert "frozen-model" not in json.dumps(detail) and "canary" not in json.dumps(detail)


async def test_exact_audit_identity_wins_and_never_shows_run_totals_as_node_details(
    client: AsyncClient, tenant: TenantContext, run: RunSeed
) -> None:
    await seed_snapshot(tenant, run)
    audit_id = uuid.uuid4()
    async with get_session_factory()() as session:
        session.add_all(
            [
                audit(tenant.tenant_id, run, id=audit_id, attempt=1, input_tokens=15),
                audit(tenant.tenant_id, run, model="another-turn", input_tokens=9000),
            ]
        )
        await session.commit()
    await seed_event(tenant, run, "MODEL_CALL_COMPLETED", {"model_audit_id": str(audit_id)})
    detail = await read_detail(client, run)
    fields = {field["key"]: field["value"] for field in detail["fields"]}
    assert fields["model"] == "audited-model" and fields["input_tokens"] == 15
    assert fields["protocol"] == "OPENAI" and fields["model_revision"] == 7
    assert detail["model_state"] == "LINKED" and detail["model_summary"] is None


@pytest.mark.parametrize(
    "status,state",
    [
        ("CREATED", "PENDING"),
        ("RUNNING", "PENDING"),
        ("WAITING_TOOL", "PENDING"),
        ("WAITING_INPUT", "PENDING"),
        ("COMPLETED", "UNLINKED"),
        ("FAILED", "UNLINKED"),
        ("CANCELLED", "UNLINKED"),
    ],
)
async def test_missing_exact_audit_reports_pending_only_for_active_run(
    client: AsyncClient, tenant: TenantContext, run: RunSeed, status: str, state: str
) -> None:
    await seed_event(tenant, run, "MODEL_CALL_STARTED", {"model_audit_id": str(uuid.uuid4())})
    async with get_session_factory()() as session:
        await session.execute(
            text(
                "UPDATE runtime.run_record SET status = :status, update_time = now() "
                "WHERE id = :run AND tenant_id = :tenant"
            ),
            {"status": status, "run": run.run_id, "tenant": tenant.tenant_id},
        )
        await session.commit()
    detail = await read_detail(client, run)
    assert detail["model_state"] == state and detail["model_summary"] is None


@pytest.mark.parametrize(
    "kind,reference",
    [("MODEL_CALL_STARTED", "invalid-uuid"), ("MODEL_CALL_COMPLETED", str(uuid.uuid4()))],
)
async def test_bad_or_completed_model_reference_is_unlinked_for_active_run(
    client: AsyncClient, tenant: TenantContext, run: RunSeed, kind: str, reference: str
) -> None:
    await seed_event(tenant, run, kind, {"model_audit_id": reference})
    async with get_session_factory()() as session:
        await session.execute(
            text(
                "UPDATE runtime.run_record SET status = 'RUNNING', update_time = now() "
                "WHERE id = :run AND tenant_id = :tenant"
            ),
            {"run": run.run_id, "tenant": tenant.tenant_id},
        )
        await session.commit()
    assert (await read_detail(client, run))["model_state"] == "UNLINKED"


@pytest.mark.parametrize("kind", ["USER_MESSAGE", "ASSISTANT_TURN", "TOOL_CALL", "RUN_COMPLETED"])
async def test_non_model_nodes_do_not_project_model_context(
    client: AsyncClient, tenant: TenantContext, run: RunSeed, kind: str
) -> None:
    await seed_snapshot(tenant, run)
    async with get_session_factory()() as session:
        session.add(audit(tenant.tenant_id, run, input_tokens=12))
        await session.commit()
    await seed_event(tenant, run, kind, {})
    detail = await read_detail(client, run)
    assert detail["model_state"] is None and detail["model_summary"] is None
    assert "frozen-model" not in json.dumps(detail) and "audited-model" not in json.dumps(detail)
