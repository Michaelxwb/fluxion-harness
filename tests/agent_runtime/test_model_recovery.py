"""[S-06][E-06] Model Recovery 与逐 attempt 审计（真实 PostgreSQL model_invocation_audit）。"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from muad_agent_core.model import (
    ModelRateLimitedError,
    ModelRequest,
    ModelResponse,
)
from muad_agent_runtime.application.model_gateway import ModelGateway
from muad_agent_runtime.infrastructure.audit_writer import RuntimeAuditWriter
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import (
    Conversation,
    ModelInvocationAudit,
)
from muad_api import AppError

TENANT = f"rec-{uuid.uuid4()}"
RUN_ID = uuid.uuid4()
CONV_ID = uuid.uuid4()


@pytest.fixture(autouse=True)
async def _seed_and_cleanup():
    async with get_session_factory()() as session:
        session.add(
            Conversation(
                id=CONV_ID,
                tenant_id=TENANT,
                user_id=uuid.uuid4(),
                agent_id=uuid.uuid4(),
                status="ACTIVE",
                last_seq=0,
            )
        )
    yield
    async with get_session_factory()() as session:
        await session.execute(
            ModelInvocationAudit.__table__.delete().where(
                ModelInvocationAudit.tenant_id == TENANT
            )
        )
        await session.commit()


class RetryThenSuccessProvider:
    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.calls = 0

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        if self.calls <= self.failures:
            # 429 语义由 gateway 通过 provider 异常类型判定；这里用属性标记
            raise ModelRateLimitedError("rate limited", retry_after=0.01)
        return ModelResponse(content="ok", finish_reason="stop", input_tokens=1, output_tokens=1)





def _request() -> ModelRequest:
    return ModelRequest(model_id="gpt-4o-mini", messages=(), tools=())


def _writer() -> RuntimeAuditWriter:
    return RuntimeAuditWriter(
        tenant_id=TENANT,
        run_id=RUN_ID,
        task_id=None,
        conversation_id=CONV_ID,
        user_id=uuid.uuid4(),
        session_factory=get_session_factory,
    )


def _gateway(policy_retries: int = 3, deadline_ms: int = 10_000) -> ModelGateway:
    return ModelGateway(
        max_retries=policy_retries, deadline_ms=deadline_ms, audit_writer=_writer()
    )


async def test_s06_rate_limited_then_success_records_attempts() -> None:
    """[S-06] 429 Retry-After 后成功；attempt/retry_reason 记录；Run 不失败。"""
    provider = RetryThenSuccessProvider(failures=1)
    gateway = _gateway()
    response = await gateway.complete(
        provider, _request(), provider_name="openai", model="gpt-4o-mini"
    )
    assert response.content == "ok"
    assert provider.calls == 2

    async with get_session_factory()() as session:
        rows = (
            await session.execute(
                sa.select(ModelInvocationAudit)
                .where(ModelInvocationAudit.tenant_id == TENANT)
                .order_by(ModelInvocationAudit.attempt)
            )
        ).scalars().all()
        assert len(rows) == 2
        assert rows[0].attempt == 1
        assert rows[0].status == "FAILED"
        assert rows[0].retry_reason == "rate_limited"
        assert rows[1].attempt == 2
        assert rows[1].status == "SUCCEEDED"


async def test_e06_exhausted_retries_fail_with_unavailable() -> None:
    """[E-06] 重试耗尽 → MODEL_UNAVAILABLE；每次 attempt 均审计。"""
    provider = RetryThenSuccessProvider(failures=10)
    gateway = _gateway(policy_retries=2, deadline_ms=60_000)
    from muad_api.error_codes import ErrorCode

    with pytest.raises(AppError) as exc:
        await gateway.complete(provider, _request(), provider_name="openai", model="gpt-4o-mini")
    assert exc.value.code == ErrorCode.MODEL_UNAVAILABLE
    assert provider.calls == 3  # 首次 + 2 重试

    async with get_session_factory()() as session:
        rows = (
            await session.execute(
                sa.select(ModelInvocationAudit)
                .where(ModelInvocationAudit.tenant_id == TENANT)
                .order_by(ModelInvocationAudit.attempt)
            )
        ).scalars().all()
        assert len(rows) == 3
        assert all(row.status == "FAILED" for row in rows)
        assert [row.attempt for row in rows] == [1, 2, 3]


async def test_e06_cancel_stops_backoff() -> None:
    """[E-06] 取消终止退避：cancel_requested 置位后不再重试。"""
    provider = RetryThenSuccessProvider(failures=10)
    gateway = _gateway(policy_retries=5, deadline_ms=60_000)
    from muad_api import AppError

    with pytest.raises(AppError):
        await gateway.complete(
            provider,
            _request(),
            provider_name="openai",
            model="gpt-4o-mini",
            is_cancelled=lambda: True,
        )
    assert provider.calls == 0  # 取消立即终止：不发起模型调用
