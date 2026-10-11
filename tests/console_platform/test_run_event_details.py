"""Lazy timeline details: real Console HTTP/auth → tenant-scoped PostgreSQL joins."""

import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from muad_agent_runtime.infrastructure.models.runtime import Artifact, ModelInvocationAudit, ToolCallAudit
from muad_console_platform.application.run_event_view import TEXT_LIMIT
from muad_console_platform.infrastructure.db import get_session_factory
from sqlalchemy import text

from console_platform.conftest import TenantContext
from console_platform.test_runs_api import (
    _CLEANUP,
    _INSERT_EVENT,
    RunSeed,
    _seed_operation,
    _seed_run,
    _seed_task,
)


@pytest.fixture(autouse=True)
async def cleanup_event_details(tenant: TenantContext) -> AsyncIterator[None]:
    yield
    async with get_session_factory()() as session:
        for table in ("tool_call_audit", "model_invocation_audit", "artifact"):
            await session.execute(
                text(f"DELETE FROM runtime.{table} WHERE tenant_id LIKE :prefix"),
                {"prefix": f"{tenant.tenant_id}%"},
            )
        for statement in _CLEANUP:
            await session.execute(text(statement), {"tenant_prefix": f"{tenant.tenant_id}%"})
        await session.commit()


@pytest.fixture
async def run(tenant: TenantContext) -> RunSeed:
    async with get_session_factory()() as session:
        seed = await _seed_run(session, tenant)
        await session.commit()
    return seed


async def seed_event(tenant: TenantContext, run: RunSeed, kind: str, data: object, seq: int = 10) -> None:
    async with get_session_factory()() as session:
        await session.execute(
            _INSERT_EVENT,
            {
                "event_id": uuid.uuid4(),
                "tenant_id": tenant.tenant_id,
                "conversation_id": run.conversation_id,
                "run_id": run.run_id,
                "seq": seq,
                "stream_type": None,
                "event_type": kind,
                "payload": json.dumps(data),
            },
        )
        await session.commit()


def url(run: RunSeed, seq: int = 10) -> str:
    return f"/api/v1/runs/{run.run_id}/events/{seq}"


async def read_detail(client: AsyncClient, run: RunSeed, seq: int = 10) -> dict:
    response = await client.get(url(run, seq))
    assert response.status_code == 200, response.text
    assert response.json()["code"] == "0"
    return response.json()["data"]


@pytest.mark.parametrize("kind", ["USER_MESSAGE", "ASSISTANT_MESSAGE", "ASSISTANT_TURN"])
async def test_message_text_is_event_specific_and_redacted(
    client: AsyncClient, tenant: TenantContext, run: RunSeed, kind: str
) -> None:
    question = (
        '问题：查询深圳天气 Bearer canary-bearer；示例 {"api_key": "canary-json"} password=canary-password'
    )
    await seed_event(
        tenant,
        run,
        kind,
        {"text": question, "reasoning_content": "canary-private-reasoning", "credential": "canary-root"},
    )
    detail = await read_detail(client, run)
    assert "查询深圳天气" in detail["text"]
    assert "canary" not in json.dumps(detail)
    assert "***" in detail["text"]
    assert "payload_json" not in detail and "input_text" not in detail
    assert detail["text_truncated"] is False
    overview = await client.get(f"/api/v1/runs/{run.run_id}")
    assert "查询深圳天气" not in overview.text
    assert set(overview.json()["data"]["timeline"][0]) == {
        "seq",
        "event_type",
        "stream_type",
        "has_artifact",
        "create_time",
    }


async def test_resume_questions_and_bounded_text(
    client: AsyncClient, tenant: TenantContext, run: RunSeed
) -> None:
    await seed_event(tenant, run, "USER_MESSAGE", {"text": "第一个问题"})
    await seed_event(
        tenant, run, "USER_MESSAGE", {"text": "第二个问题" + "长" * TEXT_LIMIT, "resumed": True}, 11
    )
    assert (await read_detail(client, run))["text"] == "第一个问题"
    second = await read_detail(client, run, 11)
    assert second["text"].startswith("第二个问题") and len(second["text"]) == TEXT_LIMIT
    assert second["text_truncated"] is True


def tool_audit(tenant: str, run: RunSeed, **overrides: object) -> ToolCallAudit:
    values = dict(
        tenant_id=tenant,
        run_id=run.run_id,
        conversation_id=run.conversation_id,
        tool_call_id="call-weather",
        tool_name="run_skill_script",
        tool_kind="SKILL",
        prepared_args_hash="sha256:test",
        args_preview_json={
            "skill_key": "weather-query",
            "script": "scripts/main.py",
            "authorization": "canary-token",
            "nested": {"password": "canary-nested"},
        },
        status="OK",
        start_time=datetime.now(UTC),
        latency_ms=1234,
    )
    values.update(overrides)
    return ToolCallAudit(**values)


async def test_tool_details_join_audit_without_copying_payload(
    client: AsyncClient, tenant: TenantContext, run: RunSeed
) -> None:
    await seed_event(
        tenant,
        run,
        "TOOL_CALL",
        {"tool_call_id": "call-weather", "tool_name": "run_skill_script", "preview": "深圳：晴"},
    )
    async with get_session_factory()() as session:
        session.add(tool_audit(tenant.tenant_id, run))
        session.add(
            tool_audit(tenant.tenant_id, run, run_id=uuid.uuid4(), tool_name="wrong-run", latency_ms=99999)
        )
        await session.commit()
    detail = await read_detail(client, run)
    fields = {field["key"]: field["value"] for field in detail["fields"]}
    assert fields["tool_name"] == "run_skill_script" and fields["duration_ms"] == 1234
    assert fields["skill_key"] == "weather-query" and fields["script"] == "scripts/main.py"
    assert detail["text"] == "深圳：晴"
    assert "canary" not in json.dumps(detail) and len(detail["arguments"]) == 4


@pytest.mark.parametrize("mode", ["other-tenant", "deleted"])
async def test_tool_join_ignores_foreign_and_deleted_audits(
    client: AsyncClient, tenant: TenantContext, run: RunSeed, mode: str
) -> None:
    await seed_event(
        tenant, run, "TOOL_CALL_STARTED", {"tool_call_id": "call-weather", "tool_name": "real-tool"}
    )
    async with get_session_factory()() as session:
        session.add(
            tool_audit(
                f"{tenant.tenant_id}-other" if mode == "other-tenant" else tenant.tenant_id,
                run,
                is_deleted=mode == "deleted",
                tool_name="canary-unavailable-tool",
            )
        )
        await session.commit()
    detail = await read_detail(client, run)
    assert "canary" not in json.dumps(detail) and detail["arguments"] == []


async def test_missing_tool_audit_keeps_available_event_facts(
    client: AsyncClient, tenant: TenantContext, run: RunSeed
) -> None:
    await seed_event(
        tenant, run, "TOOL_CALL_STARTED", {"tool_call_id": "missing", "tool_name": "list_schedules"}
    )
    detail = await read_detail(client, run)
    assert {field["key"]: field["value"] for field in detail["fields"]} == {
        "tool_name": "list_schedules",
        "tool_call_id": "missing",
    }
    assert detail["arguments"] == []


async def test_model_event_joins_exact_attempt_not_nearest_timestamp(
    client: AsyncClient, tenant: TenantContext, run: RunSeed
) -> None:
    audit_id = uuid.uuid4()
    async with get_session_factory()() as session:
        for index in range(3):
            session.add(
                ModelInvocationAudit(
                    id=audit_id if index == 1 else uuid.uuid4(),
                    tenant_id=tenant.tenant_id,
                    run_id=run.run_id,
                    provider="openai-compatible",
                    model=f"model-{index}",
                    attempt=index,
                    status="OK",
                    input_tokens=20 + index,
                    output_tokens=5,
                    latency_ms=400 + index,
                )
            )
        await session.commit()
    for seq, kind in [(10, "MODEL_CALL_STARTED"), (11, "MODEL_CALL_COMPLETED")]:
        await seed_event(tenant, run, kind, {"model_audit_id": str(audit_id)}, seq)
        detail = await read_detail(client, run, seq)
        fields = {field["key"]: field["value"] for field in detail["fields"]}
        assert fields["model"] == "model-1" and fields["attempt"] == 1 and fields["input_tokens"] == 21


@pytest.mark.parametrize("link", [None, "bad-uuid", "missing", "other-run", "other-tenant", "deleted"])
async def test_missing_or_foreign_model_reference_is_not_guessed(
    client: AsyncClient, tenant: TenantContext, run: RunSeed, link: str | None
) -> None:
    audit_id = uuid.uuid4()
    async with get_session_factory()() as session:
        session.add(
            ModelInvocationAudit(
                id=audit_id,
                tenant_id=f"{tenant.tenant_id}-other" if link == "other-tenant" else tenant.tenant_id,
                run_id=uuid.uuid4() if link == "other-run" else run.run_id,
                provider="p",
                model="private-model",
                attempt=0,
                status="OK",
                is_deleted=link == "deleted",
            )
        )
        await session.commit()
    reference = (
        str(audit_id)
        if link in {"other-run", "other-tenant", "deleted"}
        else str(uuid.uuid4())
        if link == "missing"
        else link
    )
    await seed_event(tenant, run, "MODEL_CALL_COMPLETED", {"model_audit_id": reference})
    assert (await read_detail(client, run))["fields"] == []


async def test_async_event_links_current_operation_and_task_status(
    client: AsyncClient, tenant: TenantContext, run: RunSeed
) -> None:
    async with get_session_factory()() as session:
        task_id = await _seed_task(session, tenant, run, status="COMPLETED")
        operation_id = await _seed_operation(
            session, tenant, run, task_id=task_id, status="RESULT_RECEIVED", mode="DETACH"
        )
        await session.commit()
    await seed_event(
        tenant,
        run,
        "TOOL_TASK_ACCEPTED",
        {"operation_id": str(operation_id), "task_id": str(task_id), "task_status": "QUEUED"},
    )
    fields = {field["key"]: field["value"] for field in (await read_detail(client, run))["fields"]}
    assert fields["operation_status"] == "RESULT_RECEIVED" and fields["task_status"] == "COMPLETED"
    assert fields["task_id"] == str(task_id) and fields["completion_mode"] == "DETACH"


async def test_artifact_metadata_and_preview_are_joined_without_storage_path(
    client: AsyncClient, tenant: TenantContext, run: RunSeed
) -> None:
    artifact_id = uuid.uuid4()
    async with get_session_factory()() as session:
        session.add(
            Artifact(
                id=artifact_id,
                tenant_id=tenant.tenant_id,
                run_id=run.run_id,
                artifact_type="TOOL_RESULT",
                storage_key="canary-storage-path",
                media_type="application/json",
                size=8192,
                checksum="test",
                preview_text="结果摘要",
                metadata_json={"filename": "result.json", "secret": "canary-metadata"},
            )
        )
        await session.commit()
    await seed_event(tenant, run, "TOOL_CALL", {"artifact_id": str(artifact_id), "tool_name": "query"})
    detail = await read_detail(client, run)
    assert detail["text"] == "结果摘要" and detail["attachments"][0]["size"] == 8192
    assert "canary" not in json.dumps(detail)


@pytest.mark.parametrize(
    "kind,data",
    [
        (
            "CONTEXT_COMPACTED",
            {
                "layers": {
                    "snip": {"fired": True, "groups": 3, "bytes_saved": 1000},
                    "micro": {"fired": False},
                    "bad": [],
                }
            },
        ),
        (
            "CONTEXT_SUMMARY",
            {
                "covers_up_to_seq": 8,
                "bytes_before": 100,
                "bytes_after": 30,
                "summary": {"text": "canary-summary"},
            },
        ),
        ("FUTURE_EVENT", {"text": "canary-unknown", "password": "canary-password"}),
        (
            "USER_MESSAGE",
            {"text": [], "attachments": [False, {"filename": "question.txt", "size": 10}, {"size": True}]},
        ),
        ("RUN_RESUMED", {"execution_epoch": 2, "wait_generation": True, "error_code": {"secret": "canary"}}),
        ("MODEL_CALL_COMPLETED", []),
    ],
)
async def test_event_whitelists_tolerate_missing_and_malformed_fields(
    client: AsyncClient, tenant: TenantContext, run: RunSeed, kind: str, data: object
) -> None:
    await seed_event(tenant, run, kind, data)
    detail = await read_detail(client, run)
    assert "canary" not in json.dumps(detail)
    if kind == "CONTEXT_COMPACTED":
        assert detail["compaction"] == [{"layer": "snip", "groups": 3, "bytes_saved": 1000}]
    elif kind == "CONTEXT_SUMMARY":
        assert len(detail["fields"]) == 3
    elif kind == "USER_MESSAGE":
        assert detail["text"] is None and len(detail["attachments"]) == 2
    elif kind == "RUN_RESUMED":
        assert detail["fields"] == [{"key": "execution_epoch", "value": 2}]
    else:
        assert detail["fields"] == [] and detail["text"] is None


@pytest.mark.parametrize(
    "mode", ["unknown-event", "wrong-run", "other-tenant", "deleted-event", "deleted-run", "delta"]
)
async def test_event_scope_and_soft_deletion_return_same_not_found(
    client: AsyncClient, tenant: TenantContext, run: RunSeed, mode: str
) -> None:
    await seed_event(
        tenant, run, "ASSISTANT_DELTA" if mode == "delta" else "USER_MESSAGE", {"text": "canary-private"}
    )
    async with get_session_factory()() as session:
        if mode == "other-tenant":
            await session.execute(
                text("UPDATE runtime.canonical_event SET tenant_id = :other WHERE run_id = :run_id"),
                {"other": f"{tenant.tenant_id}-other", "run_id": run.run_id},
            )
        elif mode in {"deleted-event", "deleted-run"}:
            table = "canonical_event" if mode == "deleted-event" else "run_record"
            column = "run_id" if mode == "deleted-event" else "id"
            await session.execute(
                text(f"UPDATE runtime.{table} SET is_deleted = true WHERE {column} = :id"), {"id": run.run_id}
            )
        await session.commit()
    target = (
        url(run, 99)
        if mode == "unknown-event"
        else f"/api/v1/runs/{uuid.uuid4()}/events/10"
        if mode == "wrong-run"
        else url(run)
    )
    response = await client.get(target, headers={"X-Tenant-Id": f"{tenant.tenant_id}-other"})
    assert response.status_code == 404 and response.json()["code"] == "COMMON_NOT_FOUND"
    assert "canary" not in response.text


@pytest.mark.parametrize("seq", [0, -1, "invalid"])
async def test_invalid_event_sequence_is_validation_error(
    client: AsyncClient, run: RunSeed, seq: int | str
) -> None:
    response = await client.get(f"/api/v1/runs/{run.run_id}/events/{seq}")
    assert response.status_code == 422 and response.json()["code"] == "COMMON_VALIDATION_ERROR"


async def test_event_details_require_a_session(
    client: AsyncClient, tenant: TenantContext, run: RunSeed
) -> None:
    await seed_event(tenant, run, "USER_MESSAGE", {"text": "canary-private"})
    client.cookies.clear()
    response = await client.get(url(run))
    assert response.status_code == 401 and "canary" not in response.text


@pytest.mark.parametrize(
    "content,expected",
    [
        ("后台工具返回天气：晴", "后台工具返回天气：晴"),
        (
            '{"artifact": {"preview": "结果预览", "storage_key": "canary-storage", '
            '"secret": "canary-secret"}}',
            "结果预览",
        ),
        ('{"artifact": {"storage_key": "canary-storage"}}', None),
    ],
)
async def test_background_result_uses_excerpt_without_internal_reference_fields(
    client: AsyncClient,
    tenant: TenantContext,
    run: RunSeed,
    content: str,
    expected: str | None,
) -> None:
    await seed_event(tenant, run, "BACKGROUND_RESULT", {"content": content, "terminal_status": "COMPLETED"})
    detail = await read_detail(client, run)
    assert detail["text"] == expected and "canary" not in json.dumps(detail)


@pytest.mark.parametrize("mode", ["other-run", "other-tenant", "deleted"])
async def test_artifact_join_cannot_expose_unrelated_preview(
    client: AsyncClient,
    tenant: TenantContext,
    run: RunSeed,
    mode: str,
) -> None:
    artifact_id = uuid.uuid4()
    async with get_session_factory()() as session:
        other = await _seed_run(session, tenant) if mode == "other-run" else run
        session.add(
            Artifact(
                id=artifact_id,
                tenant_id=f"{tenant.tenant_id}-other" if mode == "other-tenant" else tenant.tenant_id,
                run_id=other.run_id,
                artifact_type="TOOL_RESULT",
                storage_key="canary-storage",
                media_type="text/plain",
                size=1,
                checksum="x",
                preview_text="canary-private-preview",
                is_deleted=mode == "deleted",
            )
        )
        await session.commit()
    await seed_event(tenant, run, "TOOL_CALL", {"artifact_id": str(artifact_id)})
    detail = await read_detail(client, run)
    assert detail["attachments"] == [] and detail["text"] is None
