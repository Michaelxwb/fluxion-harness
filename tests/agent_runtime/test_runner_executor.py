import dataclasses
import uuid
from typing import Any

import pytest
from conftest import FakeExecutor, TenantContext, parse_sse
from httpx import AsyncClient
from muad_agent_core.agent import AgentRunner
from muad_agent_core.hooks import HookPipeline
from muad_agent_core.model import (
    ModelProvider,
    ModelRequest,
    ModelResponse,
    ModelUnavailableError,
)
from muad_agent_core.tools import ToolRegistry
from muad_agent_runtime.api.deps import get_executor_factory
from muad_agent_runtime.application.executor import (
    AgentRunnerExecutor,
    ExecutorFactory,
    ExecutorRequest,
    RunExecutor,
    resolve_model_api_key,
)
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import RunRecord
from muad_agent_runtime.main import app
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts import ResolveDefinitionResponse, ResolvedModel
from muad_platform_sdk import SecretValue


class StaticProvider:
    def __init__(self, *, content: str = "", error: Exception | None = None) -> None:
        self._content = content
        self._error = error

    async def complete(self, request: ModelRequest) -> ModelResponse:
        if self._error is not None:
            raise self._error
        return ModelResponse(
            content=self._content,
            finish_reason="stop",
            input_tokens=3,
            output_tokens=5,
        )


def _factory(provider: ModelProvider, *, retries: int) -> ExecutorFactory:
    async def factory(request: ExecutorRequest) -> RunExecutor:
        agent = request.agent.model_copy(update={"runtime_config": {"max_model_retries": retries}})
        runner = AgentRunner(provider=provider, registry=ToolRegistry(), hooks=HookPipeline())
        return AgentRunnerExecutor(runner=runner, request=dataclasses.replace(request, agent=agent))

    return factory


@pytest.fixture
def executor_factory() -> ExecutorFactory:
    return _factory(StaticProvider(content="hello from model"), retries=1)


def _headers(tenant: TenantContext) -> dict[str, str]:
    return {"X-Tenant-Id": tenant.tenant_id}


def _payload(tenant: TenantContext) -> dict[str, Any]:
    return {
        "agent_id": str(tenant.agent_id),
        "platform_user_id": str(tenant.platform_user_id),
        "channel": {"type": "WECOM", "bot_id": "bot-1"},
        "message": {"id": f"msg-{uuid.uuid4()}", "type": "text", "text": "run it"},
    }


async def test_runner_executor_streams_final_text(
    client: AsyncClient,
    tenant: TenantContext,
) -> None:
    response = await client.post("/v1/runs", json=_payload(tenant), headers=_headers(tenant))
    assert response.status_code == 200

    events = parse_sse(response.text)
    deltas = [event for event in events if event["type"] == "message.delta"]
    assert "".join(str(event["data"]["delta"]) for event in deltas) == "hello from model"
    assert events[-1]["type"] == "run.completed"
    assert events[-1]["data"]["final_text"] == "hello from model"
    assert events[-1]["data"]["status"] == "COMPLETED"

    run_id = uuid.UUID(events[0]["run_id"])
    async with get_session_factory()() as session:
        run = await session.get(RunRecord, run_id)
        assert run is not None
        assert run.status == "COMPLETED"


async def test_executor_request_carries_resolved_skills(
    client: AsyncClient,
    tenant: TenantContext,
    resolved: ResolveDefinitionResponse,
) -> None:
    seen: list[ExecutorRequest] = []

    async def factory(request: ExecutorRequest) -> RunExecutor:
        seen.append(request)
        return FakeExecutor(request)

    app.dependency_overrides[get_executor_factory] = lambda: factory
    response = await client.post("/v1/runs", json=_payload(tenant), headers=_headers(tenant))

    assert response.status_code == 200
    assert [skill.key for skill in seen[0].skills] == [skill.key for skill in resolved.skills]


async def test_provider_failure_yields_run_failed_with_mapped_code(
    client: AsyncClient,
    tenant: TenantContext,
) -> None:
    app.dependency_overrides[get_executor_factory] = lambda: _factory(
        StaticProvider(error=ModelUnavailableError("model down")),
        retries=1,
    )
    response = await client.post("/v1/runs", json=_payload(tenant), headers=_headers(tenant))

    events = parse_sse(response.text)
    assert events[-1]["type"] == "run.failed"
    assert events[-1]["data"]["status"] == "FAILED"
    assert events[-1]["data"]["error_code"] == "COMMON_INTERNAL_ERROR"

    run_id = uuid.UUID(events[0]["run_id"])
    async with get_session_factory()() as session:
        run = await session.get(RunRecord, run_id)
        assert run is not None
        assert run.status == "FAILED"
        assert run.error_code == "COMMON_INTERNAL_ERROR"
        assert run.error_message is not None
        assert "retries exhausted" in run.error_message


async def test_executor_factory_failure_yields_run_failed_with_mapped_code(
    client: AsyncClient,
    tenant: TenantContext,
) -> None:
    async def failing_factory(request: ExecutorRequest) -> RunExecutor:
        raise AppError(ErrorCode.CREDENTIAL_MISSING)

    app.dependency_overrides[get_executor_factory] = lambda: failing_factory
    response = await client.post("/v1/runs", json=_payload(tenant), headers=_headers(tenant))

    events = parse_sse(response.text)
    assert events[-1]["type"] == "run.failed"
    assert events[-1]["data"]["error_code"] == "CREDENTIAL_MISSING"

    run_id = uuid.UUID(events[0]["run_id"])
    async with get_session_factory()() as session:
        run = await session.get(RunRecord, run_id)
        assert run is not None
        assert run.status == "FAILED"
        assert run.error_code == "CREDENTIAL_MISSING"


def _model(secret_ref: str | None) -> ResolvedModel:
    return ResolvedModel(
        id=uuid.uuid4(),
        revision=1,
        model_id="gpt-4o-mini",
        base_url="https://llm.test/v1",
        secret_ref=secret_ref,
    )


async def test_model_secret_resolves_through_env_secret_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MUAD_SECRET__MODEL__DEMO", "sk-from-env")

    secret = await resolve_model_api_key(_model("secret://model/demo"))

    assert isinstance(secret, SecretValue)
    assert secret.value == "sk-from-env"


async def test_missing_model_secret_maps_to_credential_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MUAD_SECRET__MODEL__DEMO", raising=False)

    with pytest.raises(AppError) as error:
        await resolve_model_api_key(_model("secret://model/demo"))

    assert error.value.code == "CREDENTIAL_MISSING"


async def test_dev_fallback_uses_model_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODEL_API_KEY", "sk-dev")

    secret = await resolve_model_api_key(_model(None))

    assert isinstance(secret, SecretValue)
    assert secret.value == "sk-dev"


async def test_missing_secret_and_no_fallback_runs_unauthenticated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MODEL_API_KEY", raising=False)

    assert await resolve_model_api_key(_model(None)) is None
