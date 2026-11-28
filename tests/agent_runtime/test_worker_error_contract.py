"""Worker error envelope → tool message → outcome, through the real client/toolset."""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Mapping
from typing import Any

import httpx
import pytest
from muad_agent_core.tools import ToolExecutionPipeline, ToolRegistry
from muad_agent_runtime.application.task_client import TaskSubmissionContext, WorkerTaskClient
from muad_agent_runtime.application.task_tools import (
    DRAFT_SCHEDULE_TOOL,
    BackgroundTaskToolSet,
)
from muad_agent_runtime.application.worker_errors import WorkerResponseError
from muad_contracts import (
    ResolvedAgent,
    ResolveDefinitionResponse,
    ResolvedModel,
    ResolvedSkill,
    ScheduleDraftRequest,
    SkillExecutionMode,
)

pytestmark = pytest.mark.unit

TENANT = "tenant-tool-error"
CONTRACT = "SCHEDULE_INVOCATION_CONTRACT_MISSING"
ZH_CONTRACT = "技能的定时执行配置未通过预检"
EN_CONTRACT = "The skill scheduling configuration did not pass preflight"


def _context(locale: str = "zh-CN") -> TaskSubmissionContext:
    return TaskSubmissionContext(
        tenant_id=TENANT,
        actor_user_id=uuid.uuid4(),
        agent=ResolvedAgent(
            id=uuid.uuid4(), key="agent", revision=1, instructions="hi", runtime_config={}
        ),
        model=ResolvedModel(
            id=uuid.uuid4(),
            revision=1,
            model_id="gpt-4o-mini",
            base_url="http://model",
            params={},
        ),
        skills=(),
        source_run_id=uuid.uuid4(),
        entry_channel="WEBCHAT",
        locale=locale,
    )


def _draft_args(*, timezone: str | None = "Asia/Shanghai") -> dict[str, Any]:
    return {
        "instruction": "每天 9 点提醒我开周会",
        "timezone": timezone,
        "recurrence": {"kind": "CRON", "cron": "0 9 * * *"},
        "payload": {"kind": "REMINDER", "text": "开周会"},
        "name": "周会提醒",
    }


def _contract_detail(reason: str) -> dict[str, str]:
    return {"field": "payload.skill_id", "reason": reason, "repair": "REIMPORT_SKILL"}


async def _run(
    payload: object,
    *,
    status: int = 422,
    locale: str = "zh-CN",
    arguments: Mapping[str, Any] | None = None,
    malformed_success: bool = False,
    raised: httpx.HTTPError | None = None,
) -> tuple[Any, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET" and request.url.path.endswith("/delivery-targets"):
            return httpx.Response(200, json={"code": "0", "msg": "ok", "data": {"items": []}})
        if raised is not None:
            raise raised
        if malformed_success:
            return httpx.Response(200, text="not-json")
        return httpx.Response(status, json=payload)

    client = WorkerTaskClient("http://worker", transport=httpx.MockTransport(handler))
    registry = ToolRegistry()
    BackgroundTaskToolSet(client=client, context=_context(locale), skills=[]).register(registry)
    pipeline = ToolExecutionPipeline(registry=registry)
    try:
        execution = await pipeline.execute(
            pipeline.prepare(
                call_id="call-1",
                tool_name=DRAFT_SCHEDULE_TOOL,
                arguments=dict(arguments or _draft_args()),
            )
        )
    finally:
        await client.aclose()
    return execution, requests


def _error(execution: Any) -> dict[str, Any]:
    return json.loads(execution.result.content)["error"]


@pytest.mark.parametrize("reason", ["CONTRACT_MISSING", "CONTRACT_INVALID"])
async def test_worker_contract_error_reaches_the_model_with_reason_and_repair(
    reason: str,
) -> None:
    execution, _ = await _run(
        {
            "code": CONTRACT,
            "msg": ZH_CONTRACT,
            "data": _contract_detail(reason),
        }
    )

    error = _error(execution)
    assert error["code"] == CONTRACT
    assert error["message"] == ZH_CONTRACT
    assert error["detail"] == _contract_detail(reason)
    assert error["action_owner"] == "ADMIN"
    assert error["retryable"] is False
    assert error["cause"]
    assert error["next_step"]
    assert error["cause"] != error["message"]

    outcome = execution.result.outcome
    assert outcome.error_code == CONTRACT
    assert outcome.retryable is False
    assert outcome.evidence.diagnostic["detail"] == _contract_detail(reason)
    assert outcome.evidence.diagnostic["action_owner"] == "ADMIN"
    assert outcome.evidence.diagnostic["message_source"] == "PLATFORM"


async def test_worker_contract_error_without_detail_does_not_invent_a_repair() -> None:
    execution, _ = await _run({"code": CONTRACT, "msg": ZH_CONTRACT})

    error = _error(execution)
    assert error["code"] == CONTRACT
    assert error["message"] == ZH_CONTRACT
    assert "detail" not in error
    assert "action_owner" not in error
    assert error["retryable"] is False
    assert "REIMPORT" not in execution.result.content

    diagnostic = execution.result.outcome.evidence.diagnostic
    assert diagnostic["detail"] == {}
    assert diagnostic["action_owner"] is None
    assert diagnostic["message_source"] == "PLATFORM"


async def test_local_task_tool_error_keeps_its_detail_and_grounded_owner() -> None:
    execution, requests = await _run(None, arguments=_draft_args(timezone="Mars/Olympus"))

    error = _error(execution)
    assert error["code"] == "SCHEDULE_DRAFT_INVALID"
    assert "INVALID_TIMEZONE" in error["message"]
    assert error["detail"]["reason"] == "INVALID_TIMEZONE"
    assert error["action_owner"] == "USER"
    assert error["retryable"] is True
    assert not [request for request in requests if request.method == "POST"]

    diagnostic = execution.result.outcome.evidence.diagnostic
    assert diagnostic["action_owner"] == "USER"
    assert diagnostic["message_source"] == "LOCAL"


async def test_unknown_but_safe_code_is_kept_without_trusting_its_data() -> None:
    execution, _ = await _run(
        {
            "code": "SCHEDULE_FUTURE_FACT",
            "msg": "future fact",
            "data": {"reason": "CONTRACT_MISSING", "repair": "REIMPORT_SKILL"},
        }
    )

    error = _error(execution)
    assert error["code"] == "SCHEDULE_FUTURE_FACT"
    assert error["message"] == "系统内部错误"
    assert "detail" not in error
    assert "retryable" not in error
    assert error["outcome_known"] is False

    outcome = execution.result.outcome
    assert outcome.retryable is False
    assert outcome.control.outcome_known is False
    assert outcome.evidence.diagnostic["message_source"] == "CATALOG"


async def test_unsafe_code_falls_back_without_leaking_the_marker() -> None:
    marker = "secret-marker-0f3"
    execution, _ = await _run({"code": marker, "msg": marker, "data": {"reason": marker}})

    error = _error(execution)
    assert error["code"] == "COMMON_INTERNAL_ERROR"
    assert marker not in execution.result.content
    assert "detail" not in error
    assert error["outcome_known"] is False
    assert marker not in repr(execution.result.outcome.evidence.diagnostic)


async def test_free_text_and_unlisted_detail_keys_never_reach_the_model_or_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    marker = "UNIQUE-MARKER-9f3"
    payload = {
        "code": CONTRACT,
        "msg": f"worker said {marker}",
        "data": {
            **_contract_detail("CONTRACT_MISSING"),
            "secret": marker,
            "nested": {"value": marker},
            "fields": [marker],
        },
    }

    with caplog.at_level(logging.WARNING):
        execution, _ = await _run(payload)

    error = _error(execution)
    assert error["detail"] == _contract_detail("CONTRACT_MISSING")
    assert marker not in execution.result.content
    assert marker not in repr(execution.result.outcome.evidence.diagnostic)
    assert marker not in "\n".join(record.getMessage() for record in caplog.records)
    assert execution.result.outcome.evidence.diagnostic["message_source"] == "CATALOG"


async def test_detail_lists_are_bounded_and_only_allowlisted_values_survive() -> None:
    valid_fields = sorted(ScheduleDraftRequest.model_fields)[:8]
    marker = "SECRET-VALUE"
    fields = [*valid_fields * 3, marker, "x" * 500]

    execution, _ = await _run(
        {
            "code": "SCHEDULE_PARAMETER_INCOMPLETE",
            "msg": "缺少必填参数",
            "data": {"fields": fields, "reason": marker},
        }
    )

    error = _error(execution)
    assert error["detail"]["fields"] == valid_fields * 2
    assert len(error["detail"]["fields"]) == 16
    assert marker not in execution.result.content


async def test_malformed_success_body_is_an_unknown_outcome() -> None:
    execution, _ = await _run(None, malformed_success=True)

    error = _error(execution)
    assert error["code"] == "COMMON_INTERNAL_ERROR"
    assert error["outcome_known"] is False
    assert execution.result.outcome.control.sent is True
    assert execution.result.outcome.control.outcome_known is False


@pytest.mark.parametrize(
    ("raised", "sent"),
    [
        (httpx.ReadTimeout("read timed out"), True),
        (httpx.ConnectError("connection refused"), False),
    ],
)
async def test_transport_failure_keeps_the_real_send_fact(
    raised: httpx.HTTPError, sent: bool
) -> None:
    execution, _ = await _run(None, raised=raised)

    error = _error(execution)
    assert error["code"] == "COMMON_INTERNAL_ERROR"
    assert error["outcome_known"] is False
    assert "retryable" not in error
    outcome = execution.result.outcome
    assert outcome.control.sent is sent
    assert outcome.control.outcome_known is False
    assert outcome.retryable is False


async def test_worker_message_in_another_locale_is_not_labelled_as_platform_text() -> None:
    execution, _ = await _run(
        {"code": CONTRACT, "msg": ZH_CONTRACT, "data": _contract_detail("CONTRACT_MISSING")},
        locale="en-US",
    )

    error = _error(execution)
    assert error["message"] == EN_CONTRACT
    assert execution.result.outcome.evidence.diagnostic["message_source"] == "CATALOG"


class _Resolver:
    def __init__(self, skill: ResolvedSkill) -> None:
        self.skill = skill
        self.calls = 0

    async def resolve(self, _request: object, *, tenant_id: str, trace_id: str = ""):
        self.calls += 1
        return ResolveDefinitionResponse(
            agent=_context().agent,
            model=_context().model,
            skills=[self.skill],
        )


async def test_allowed_values_are_checked_against_the_latest_skill_contract() -> None:
    current = ResolvedSkill(
        skill_id=uuid.UUID("11111111-1111-4111-8111-111111111111"),
        artifact_id=uuid.uuid4(),
        key="demo",
        name="Demo",
        description="demo",
        version="2.0.0",
        checksum="sha256:" + "c" * 64,
        storage_key="skills/demo.tar.gz",
        execution_mode=SkillExecutionMode.SYNC,
        frontmatter={
            "invocation_contract": {
                "version": 1,
                "invocation": "stdin",
                "schema": {
                    "type": "object",
                    "properties": {"body": {"type": "string"}},
                    "required": ["body"],
                    "additionalProperties": False,
                },
            }
        },
        source="ADMIN",
    )
    resolver = _Resolver(current)
    context = _context()
    toolset = BackgroundTaskToolSet(
        client=WorkerTaskClient("http://worker"),
        context=context,
        skills=[],
        resolve_client=resolver,
    )
    error = WorkerResponseError(
        {
            "code": "SCHEDULE_INPUT_INVALID",
            "msg": "Skill 输入参数不合法",
            "data": {
                "field": "payload.input",
                "reason": "UNKNOWN_ARGUMENT",
                "repair": "PROVIDE_SKILL_INPUT",
                "allowed_values": ["body", "UNIQUE-MARKER-9f3"],
            },
        },
        "zh-CN",
        outcome_known=True,
        sent=True,
    )

    detail = await toolset._validated_worker_detail(
        error, {"payload": {"skill_key": "demo", "input": {"body": "x"}}}
    )

    assert resolver.calls == 1
    assert detail["allowed_values"] == ["body"]
    assert "UNIQUE-MARKER-9f3" not in json.dumps(detail)
