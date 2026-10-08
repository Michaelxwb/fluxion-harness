"""B-02: strict DTOs and first-response replay through real PG constraints."""

import math
from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from muad_agent_worker.application.submissions import TaskSubmissionService, submission_fingerprint
from muad_api import AppError, install_api_foundation
from muad_contracts import CreateTaskRequest, enums
from muad_contracts.canonical import NonCanonicalJsonError, canonical_json
from muad_contracts.enums import CompletionMode, RunStatus
from muad_contracts.tool_runtime import ToolResultRequest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("RunStatus", "CREATED RUNNING WAITING_TOOL WAITING_INPUT COMPLETED FAILED CANCELLED"),
        ("CompletionMode", "JOIN DETACH"),
        ("TerminalStatus", "COMPLETED FAILED CANCELLED"),
        (
            "OperationStatus",
            "SUBMIT_PENDING SUBMITTED TASK_ACCEPTED RUNNING RESULT_RECEIVED MATERIALIZED "
            "COMPLETED FAILED CANCELLED LATE",
        ),
        ("ControlOutboxStatus", "PENDING SENT FAILED"),
        ("InboxMaterializationState", "PENDING RECEIVED MATERIALIZED LATE FAILED"),
        ("WaitReason", "SUBMISSION TASK_RESULT RESUME_READY"),
    ],
)
def test_b02_business_state_enums_are_pinned(kind: str, expected: str) -> None:
    assert {item.value for item in getattr(enums, kind)} == set(expected.split())


def _submission() -> dict:
    run_id = uuid4()
    return {
        "tenant_id": f"b02-{uuid4()}",
        "agent_id": uuid4(),
        "actor_user_id": uuid4(),
        "source_run_id": run_id,
        "intent_key": "skill",
        "skill_id": uuid4(),
        "skill_artifact_id": uuid4(),
        "input": {"中文": "结果"},
        "execution_snapshot": {},
        "snapshot_hash": "sha256:" + "1" * 64,
        "idempotency_key": "b02-key",
        "delivery_mode": "NONE",
        "runtime_operation": {
            "operation_id": uuid4(),
            "source_run_id": run_id,
            "source_tool_call_id": "call-1",
            "completion_mode": "JOIN",
        },
    }


def _result() -> dict:
    return {
        "schema_version": 1,
        "event_id": uuid4(),
        "operation_id": uuid4(),
        "task_id": uuid4(),
        "task_event_seq": 1,
        "source_run_id": uuid4(),
        "actor_user_id": uuid4(),
        "task_snapshot_hash": "sha256:" + "1" * 64,
        "terminal_status": "COMPLETED",
        "completed_at": datetime.now(UTC),
        "result": {"value": "真实结果"},
    }


def test_b02_canonical_json_is_injective_for_supported_values() -> None:
    assert canonical_json({"b": 2, "a": "中文"}) == '{"a":"中文","b":2}'
    for value in ({1: "x"}, (1, 2), date(2026, 1, 1), {"x": math.nan}, {"x": math.inf}):
        with pytest.raises(NonCanonicalJsonError):
            canonical_json(value)
    cyclic = []
    cyclic.append(cyclic)
    with pytest.raises(NonCanonicalJsonError):
        canonical_json(cyclic)


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf, date(2026, 1, 1), object()])
def test_b02_free_json_dtos_reject_non_json_before_serialization(value: object) -> None:
    for field in ("input", "execution_snapshot"):
        payload = _submission()
        payload[field] = {"nested": [value]}
        with pytest.raises(ValidationError):
            CreateTaskRequest.model_validate(payload)
    payload = _result()
    payload["result"] = {"nested": [value]}
    with pytest.raises(ValidationError):
        ToolResultRequest.model_validate(payload)


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
async def test_b02_invalid_wire_json_returns_422(literal: str) -> None:
    app = FastAPI()
    install_api_foundation(app)

    @app.post("/results")
    async def receive(payload: ToolResultRequest) -> dict:
        return payload.model_dump(mode="json")

    request = ToolResultRequest.model_validate(_result()).model_dump_json()
    request = request.replace('"真实结果"', literal)
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        response = await client.post(
            "/results", content=request, headers={"Content-Type": "application/json"}
        )
    assert response.status_code == 422
    assert response.json()["code"] == "COMMON_VALIDATION_ERROR"


def test_b02_result_identity_shape_and_size_are_strict() -> None:
    assert RunStatus.WAITING_TOOL.value == "WAITING_TOOL"
    assert set(CompletionMode) == {CompletionMode.JOIN, CompletionMode.DETACH}
    for changes in (
        {"task_event_seq": 0},
        {"task_event_seq": True},
        {"schema_version": 2},
        {"completed_at": datetime(2026, 1, 1)},
        {"error_code": "unexpected"},
        {"result": {"value": "x" * (256 * 1024)}},
    ):
        with pytest.raises(ValidationError):
            ToolResultRequest.model_validate(_result() | changes)
    with pytest.raises(ValidationError):
        ToolResultRequest.model_validate(_result() | {"terminal_status": "FAILED"})
    payload = _submission()
    payload["runtime_operation"]["source_run_id"] = uuid4()
    with pytest.raises(ValidationError):
        CreateTaskRequest.model_validate(payload)


async def test_b02_pg_replay_conflicts_and_unique_scope(async_tool_database) -> None:
    request = CreateTaskRequest.model_validate(_submission())
    fingerprint = submission_fingerprint("create-task", request)
    factory = async_sessionmaker(async_tool_database, expire_on_commit=False)
    async with factory() as session:
        service = TaskSubmissionService(session)
        first = await service.record_in(
            tenant_id=request.tenant_id,
            idempotency_key=request.idempotency_key,
            endpoint="create-task",
            actor_user_id=request.actor_user_id,
            request_fingerprint=fingerprint,
            response={"task_id": str(uuid4()), "status": "QUEUED"},
        )
        await session.commit()
        replay = await service.find_replay(
            tenant_id=request.tenant_id,
            idempotency_key=request.idempotency_key,
            endpoint="create-task",
            fingerprint=fingerprint,
        )
        assert replay.response_json == first.response_json
        for field, value in (
            ("actor_user_id", uuid4()),
            ("input", {"different": True}),
            ("snapshot_hash", "sha256:" + "2" * 64),
        ):
            changed = request.model_copy(update={field: value})
            with pytest.raises(AppError, match="IDEMPOTENCY_MISMATCH"):
                await service.find_replay(
                    tenant_id=request.tenant_id,
                    idempotency_key=request.idempotency_key,
                    endpoint="create-task",
                    fingerprint=submission_fingerprint("create-task", changed),
                )
        changed = request.model_copy(deep=True)
        changed.runtime_operation.completion_mode = CompletionMode.DETACH
        with pytest.raises(AppError, match="IDEMPOTENCY_MISMATCH"):
            await service.find_replay(
                tenant_id=request.tenant_id,
                idempotency_key=request.idempotency_key,
                endpoint="create-task",
                fingerprint=submission_fingerprint("create-task", changed),
            )
        for tenant, endpoint in (("other", "create-task"), (request.tenant_id, "cancel-operation")):
            assert (
                await service.find_replay(
                    tenant_id=tenant,
                    idempotency_key=request.idempotency_key,
                    endpoint=endpoint,
                    fingerprint=fingerprint,
                )
                is None
            )
            assert (
                submission_fingerprint(endpoint, request.model_copy(update={"tenant_id": tenant}))
                != fingerprint
            )
        with pytest.raises(IntegrityError):
            await service.record_in(
                tenant_id=request.tenant_id,
                idempotency_key=request.idempotency_key,
                endpoint="create-task",
                actor_user_id=request.actor_user_id,
                request_fingerprint=fingerprint,
                response={"wrong": True},
            )
        await session.rollback()
