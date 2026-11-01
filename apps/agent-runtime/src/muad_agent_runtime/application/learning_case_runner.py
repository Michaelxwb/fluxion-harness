"""Production CaseRunner: rebuild a replay request from the frozen Snapshot.

从 eval_case 的来源 Run 读取输入，从 RuntimeSnapshot 读取 agent 指令与模型定义；凭据由
注入的解析器按 API-09 实时提供，不落日志/报告。候选臂把经验正文作为参考经验注入，
基线臂不注入。判定优先看参考答案的 JSON 结构，否则看词法覆盖。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import UUID

import httpx
import sqlalchemy as sa
from muad_agent_core.memory.tokens import tokenize
from muad_agent_core.model import (
    ModelMessage,
    ModelRequest,
    ModelRole,
    OpenAICompatibleProvider,
    text_of,
)

from ..infrastructure.db import SessionFactory
from ..infrastructure.models.runtime import CanonicalEvent, EvalCase, RunRecord, RuntimeSnapshot, UserMemory
from .learning_replay import CaseOutcome

logger = logging.getLogger(__name__)

#: (tenant_id, model) -> api_key；生产装配用 API-09 受信解析，测试注入固定值。
CredentialsResolver = Callable[[str, "object"], Awaitable[str]]
REFERENCE_COVERAGE = 0.8


@dataclass(frozen=True, slots=True)
class RunnerLimits:
    max_tokens: int = 512
    timeout_sec: float = 60.0


def _reference_answer(events: list[CanonicalEvent]) -> str:
    for row in reversed(events):
        if row.event_type == "ASSISTANT_MESSAGE":
            text = row.payload_json.get("text") if isinstance(row.payload_json, dict) else None
            if isinstance(text, str) and text.strip():
                return text
    return ""


def judge_answer(reference: str, candidate: str) -> tuple[bool, str]:
    """参考答案优先按 JSON 结构比对，否则按内容 token 覆盖率。"""
    if not reference.strip():
        return False, "reference answer missing"
    try:
        expected = json.loads(reference)
    except (ValueError, TypeError):
        expected = None
    if isinstance(expected, dict):
        try:
            actual = json.loads(candidate)
        except (ValueError, TypeError):
            return False, "candidate is not JSON"
        if not isinstance(actual, dict):
            return False, "candidate JSON is not an object"
        missing = sorted(set(expected) - set(actual))
        if missing:
            return False, f"missing keys: {', '.join(missing)}"
        return True, ""
    wanted = tokenize(reference)
    if not wanted:
        return False, "reference answer has no tokens"
    coverage = len(wanted & tokenize(candidate)) / len(wanted)
    if coverage < REFERENCE_COVERAGE:
        return False, f"reference coverage {coverage:.2f} < {REFERENCE_COVERAGE}"
    return True, ""


class ProductionCaseRunner:
    def __init__(
        self,
        session_factory: SessionFactory,
        *,
        resolve_credentials: CredentialsResolver,
        limits: RunnerLimits | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._factory = session_factory
        self._credentials = resolve_credentials
        self._limits = limits or RunnerLimits()
        self._transport = transport

    async def __call__(self, case: EvalCase, experience_key: str | None) -> CaseOutcome:
        async with self._factory() as session:
            run = await session.get(RunRecord, case.source_run_id)
            snapshot = (
                await session.get(RuntimeSnapshot, run.snapshot_id)
                if run is not None and run.snapshot_id is not None
                else None
            )
            if run is None or snapshot is None:
                return CaseOutcome(False, "source run or snapshot missing", 0, 0)
            events = list(
                (
                    await session.scalars(
                        sa.select(CanonicalEvent)
                        .where(
                            CanonicalEvent.tenant_id == case.tenant_id,
                            CanonicalEvent.run_id == run.id,
                            CanonicalEvent.is_deleted.is_(False),
                        )
                        .order_by(CanonicalEvent.seq)
                    )
                ).all()
            )
            experience = None
            if experience_key is not None:
                row = await session.scalar(
                    sa.select(UserMemory).where(
                        UserMemory.tenant_id == case.tenant_id,
                        UserMemory.user_id == run.user_id,
                        UserMemory.memory_key == experience_key,
                        UserMemory.is_deleted.is_(False),
                    )
                )
                if row is not None and isinstance(row.content_json, dict):
                    value = row.content_json.get("value")
                    experience = value if isinstance(value, str) else None
            model = snapshot.model_json
            agent = snapshot.agent_json
        reference = _reference_answer(events)
        instructions = agent.get("instructions", "") if isinstance(agent, dict) else ""
        model_id = model.get("model_id") if isinstance(model, dict) else None
        base_url = model.get("base_url") if isinstance(model, dict) else None
        if not isinstance(model_id, str) or not isinstance(base_url, str):
            return CaseOutcome(False, "snapshot model incomplete", case.item_count, 0)
        api_key = await self._credentials(case.tenant_id, model)
        system = instructions
        if experience:
            system = f"{system}\n\n参考经验（已验证）：{experience}"
        provider = OpenAICompatibleProvider(
            base_url=base_url,
            model=model_id,
            api_key=api_key,
            timeout_sec=self._limits.timeout_sec,
            transport=self._transport,
        )
        try:
            response = await provider.complete(
                ModelRequest(
                    model_id=model_id,
                    messages=(
                        ModelMessage(role=ModelRole.SYSTEM, content=system),
                        ModelMessage(role=ModelRole.USER, content=run.input_text),
                    ),
                    max_tokens=self._limits.max_tokens,
                    timeout_sec=self._limits.timeout_sec,
                )
            )
        finally:
            await provider.aclose()
        passed, reason = judge_answer(reference, text_of(response.content))
        return CaseOutcome(passed, reason, case.item_count, case.item_count if passed else 0)
