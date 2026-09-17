from __future__ import annotations

import asyncio
import contextlib
import importlib
import os
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any, Protocol, cast

from muad_agent_core.agent import (
    AgentPolicy,
    AgentRunner,
    AgentRunRequest,
    RunnerCancelled,
)
from muad_agent_core.hooks import HookPipeline
from muad_agent_core.model import ModelMessage, ModelRole, OpenAICompatibleProvider
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_artifact_store import SkillArtifactCache
from muad_contracts import ResolvedAgent, ResolvedModel, ResolvedSkill
from muad_platform_sdk.credential import SecretProvider
from muad_platform_sdk.types import SecretValue

from .skill_tools import build_default_skill_cache, build_skill_registry

CancelCheck = Callable[[], Awaitable[bool]]
MESSAGE_DELTA_EVENT = "message.delta"
DELTA_CHUNK_SIZE = 256
CANCEL_POLL_INTERVAL_SEC = 0.25
MODEL_TIMEOUT_SEC = 120.0
MODEL_API_KEY_ENV = "MODEL_API_KEY"


@dataclass(frozen=True)
class ExecutorEvent:
    type: str
    data: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ExecutorRequest:
    agent: ResolvedAgent
    model: ResolvedModel
    input_text: str
    is_cancel_requested: CancelCheck
    skills: tuple[ResolvedSkill, ...] = ()


class RunExecutor(Protocol):
    def run(self) -> AsyncIterator[ExecutorEvent]: ...


ExecutorFactory = Callable[[ExecutorRequest], Awaitable[RunExecutor]]


class AgentRunnerExecutor:
    def __init__(
        self,
        *,
        runner: AgentRunner,
        request: ExecutorRequest,
        close: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._runner = runner
        self._request = request
        self._close = close

    async def run(self) -> AsyncIterator[ExecutorEvent]:
        cancelled = asyncio.Event()
        poller = asyncio.create_task(self._poll_cancel(cancelled))
        try:
            result = await self._runner.run(
                self._build_run_request(),
                is_cancelled=cancelled.is_set,
            )
        except RunnerCancelled:
            return
        finally:
            poller.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await poller
            if self._close is not None:
                await self._close()
        for chunk in _chunks(result.final_text):
            yield ExecutorEvent(type=MESSAGE_DELTA_EVENT, data={"delta": chunk})

    def _build_run_request(self) -> AgentRunRequest:
        params = dict(self._request.model.params)
        return AgentRunRequest(
            model_id=self._request.model.model_id,
            instructions=self._request.agent.instructions,
            messages=(ModelMessage(role=ModelRole.USER, content=self._request.input_text),),
            policy=AgentPolicy.from_runtime_config(self._request.agent.runtime_config),
            temperature=_float_param(params, "temperature"),
            max_tokens=_int_param(params, "max_tokens"),
            params=params,
        )

    async def _poll_cancel(self, cancelled: asyncio.Event) -> None:
        while not cancelled.is_set():
            if await self._request.is_cancel_requested():
                cancelled.set()
                return
            await asyncio.sleep(CANCEL_POLL_INTERVAL_SEC)


async def default_executor_factory(
    request: ExecutorRequest,
    *,
    skill_cache: SkillArtifactCache | None = None,
) -> RunExecutor:
    provider = OpenAICompatibleProvider(
        base_url=request.model.base_url,
        model=request.model.model_id,
        api_key=await resolve_model_api_key(request.model),
        timeout_sec=MODEL_TIMEOUT_SEC,
    )
    runner = AgentRunner(
        provider=provider,
        registry=build_skill_registry(
            cache=skill_cache or build_default_skill_cache(),
            skills=request.skills,
            policy=AgentPolicy.from_runtime_config(request.agent.runtime_config),
        ),
        hooks=HookPipeline(),
    )
    return AgentRunnerExecutor(runner=runner, request=request, close=provider.aclose)


async def resolve_model_api_key(model: ResolvedModel) -> SecretValue | str | None:
    if model.secret_ref:
        provider = _load_secret_provider()
        if provider is None:
            raise AppError(ErrorCode.CREDENTIAL_MISSING)
        try:
            secret = await provider.get(model.secret_ref)
        except LookupError as exc:
            raise AppError(ErrorCode.CREDENTIAL_MISSING) from exc
        if isinstance(secret, SecretValue):
            return secret
        raise AppError(ErrorCode.CREDENTIAL_MISSING)
    value = os.environ.get(MODEL_API_KEY_ENV)
    if value:
        return SecretValue(value=value, version="env")
    return None


def _load_secret_provider() -> SecretProvider | None:
    module = importlib.import_module("muad_platform_sdk.credential")
    factory = getattr(module, "EnvSecretProvider", None)
    if factory is None:
        return None
    return cast(SecretProvider, factory())


def _chunks(text: str) -> Iterator[str]:
    for index in range(0, len(text), DELTA_CHUNK_SIZE):
        yield text[index : index + DELTA_CHUNK_SIZE]


def _float_param(params: Mapping[str, Any], key: str) -> float | None:
    value = params.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _int_param(params: Mapping[str, Any], key: str) -> int | None:
    value = params.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else None
