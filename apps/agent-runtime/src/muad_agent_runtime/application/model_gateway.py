"""Model Gateway：有界恢复策略（429/5xx/reset/timeout），逐 attempt 审计。"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Callable
from typing import Any, Protocol

from muad_agent_core.model import (
    ModelRateLimitedError,
    ModelRequestError,
    ModelUnavailableError,
)
from muad_api import AppError
from muad_api.error_codes import ErrorCode

from ..infrastructure.audit_writer import RuntimeAuditWriter
from ..metrics import MODEL_INVOCATIONS_METRIC, record_outcome

MAX_RETRIES_DEFAULT = 3
DEADLINE_DEFAULT_MS = 60_000
RETRY_BASE_SEC = 0.05
SUCCEEDED_STATUS = "SUCCEEDED"
FAILED_STATUS = "FAILED"


class _Provider(Protocol):
    async def complete(self, request: Any) -> Any: ...


class _RateLimited(Exception):
    def __init__(self, retry_after: float | None = None) -> None:
        super().__init__("rate limited")
        self.retry_after = retry_after


class _Unavailable(Exception):
    pass


class _RequestRejected(Exception):
    pass


class ModelGateway:
    """有界恢复：429 按 Retry-After，5xx/reset/timeout 指数退避；取消/截止即停。"""

    def __init__(
        self,
        *,
        max_retries: int = MAX_RETRIES_DEFAULT,
        deadline_ms: int = DEADLINE_DEFAULT_MS,
        audit_writer: RuntimeAuditWriter | None = None,
        base_delay_sec: float = RETRY_BASE_SEC,
    ) -> None:
        self._max_retries = max_retries
        self._deadline_ms = deadline_ms
        self._audit_writer = audit_writer
        self._base_delay_sec = base_delay_sec

    async def complete(
        self,
        provider: _Provider,
        request: Any,
        *,
        provider_name: str = "openai",
        model: str = "",
        is_cancelled: Callable[[], bool] | None = None,
        run_id: uuid.UUID | None = None,
        task_id: uuid.UUID | None = None,
        tenant_id: str = "",
        conversation_id: uuid.UUID | None = None,
        user_id: uuid.UUID | None = None,
    ) -> Any:
        writer = self._audit_writer
        started = time.monotonic()
        attempt = 0
        while True:
            if is_cancelled and is_cancelled():
                raise AppError(ErrorCode.COMMON_VALIDATION_ERROR)
            attempt += 1
            retry_reason: str | None = None
            start = time.monotonic()
            try:
                response = await provider.complete(request)
                if writer is not None:
                    await writer.record_model_invocation(
                        provider=provider_name,
                        model=model,
                        attempt=attempt,
                        retry_reason=None,
                        input_tokens=getattr(response, "input_tokens", None),
                        output_tokens=getattr(response, "output_tokens", None),
                        latency_ms=int((time.monotonic() - start) * 1000),
                        status="SUCCEEDED",
                    )
                record_outcome(
                    MODEL_INVOCATIONS_METRIC,
                    SUCCEEDED_STATUS,
                    {"provider": provider_name, "model": model},
                )
                return response
            except ModelRateLimitedError as exc:
                retry_reason = "rate_limited"
                delay = getattr(exc, "retry_after", None) or self._base_delay_sec * (2**attempt)
            except ModelUnavailableError as exc:
                retry_reason = "unavailable"
                delay = self._base_delay_sec * (2**attempt)
                if attempt > self._max_retries:
                    await self._audit_failed(writer, provider_name, model, attempt, retry_reason)
                    raise AppError(ErrorCode.MODEL_UNAVAILABLE) from exc
            except ModelRequestError as exc:
                await self._audit_failed(writer, provider_name, model, attempt, "request_rejected")
                raise AppError(ErrorCode.MODEL_UNAVAILABLE) from exc

            if attempt > self._max_retries:
                await self._audit_failed(writer, provider_name, model, attempt, retry_reason)
                raise AppError(ErrorCode.MODEL_UNAVAILABLE)
            elapsed_ms = (time.monotonic() - started) * 1000
            if elapsed_ms + delay * 1000 >= self._deadline_ms:
                await self._audit_failed(writer, provider_name, model, attempt, "deadline")
                raise AppError(ErrorCode.MODEL_UNAVAILABLE)
            await asyncio.sleep(delay)
            if writer is not None:
                await writer.record_model_invocation(
                    provider=provider_name,
                    model=model,
                    attempt=attempt,
                    retry_reason=retry_reason,
                    input_tokens=None,
                    output_tokens=None,
                    latency_ms=int((time.monotonic() - start) * 1000),
                    status="FAILED",
                    error_code=retry_reason,
                )

    async def _audit_failed(
        self,
        writer: RuntimeAuditWriter | None,
        provider_name: str,
        model: str,
        attempt: int,
        retry_reason: str,
    ) -> None:
        record_outcome(
            MODEL_INVOCATIONS_METRIC,
            FAILED_STATUS,
            {"provider": provider_name, "model": model},
        )
        if writer is None:
            return
        await writer.record_model_invocation(
            provider=provider_name,
            model=model,
            attempt=attempt,
            retry_reason=retry_reason,
            input_tokens=None,
            output_tokens=None,
            latency_ms=None,
            status="FAILED",
            error_code=retry_reason,
        )
