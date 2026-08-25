from __future__ import annotations

import traceback
from datetime import datetime

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from fluxion.api.middleware import RequestContextMiddleware
from fluxion.api.responses import failure, success
from fluxion.config import DevModeSettings
from fluxion.errors.console import (
    EVAL_EXECUTION_ERROR,
    EVAL_INTERNAL_ERROR,
    EVAL_TRACEABILITY_ERROR,
    EVAL_VALIDATION_FAILED,
)
from fluxion.observability.context import current_context
from fluxion.observability.logging import emit_error_log
from fluxion.services.eval_app import (
    EvalExecutionError,
    EvalRunRecord,
    EvalRunRequest,
    EvalTraceabilityError,
    EvaluationApplicationService,
)


class EvalRunCreatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    eval_set_id: str
    eval_set_version: str
    trace_id: str


class EvalComparePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    baseline_run_id: str


def create_app(
    service: EvaluationApplicationService,
    *,
    dev_mode: DevModeSettings | None = None,
) -> FastAPI:
    app = FastAPI(title="Fluxion Eval API")
    app.add_middleware(RequestContextMiddleware, dev_mode=dev_mode)
    _register_errors(app)
    _register_runs(app, service)
    _register_compare(app, service)
    return app


def _register_errors(app: FastAPI) -> None:
    @app.exception_handler(EvalTraceabilityError)
    async def traceability_error(request: Request, exc: EvalTraceabilityError) -> JSONResponse:
        return failure(
            EVAL_TRACEABILITY_ERROR,
            str(exc),
            status_code=404,
            request=request,
        )

    @app.exception_handler(EvalExecutionError)
    async def execution_error(request: Request, exc: EvalExecutionError) -> JSONResponse:
        del exc
        return failure(
            EVAL_EXECUTION_ERROR,
            "Eval 执行失败",
            status_code=500,
            request=request,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        del exc
        return failure(
            EVAL_VALIDATION_FAILED,
            "请求参数无效",
            status_code=400,
            request=request,
        )

    @app.exception_handler(Exception)
    async def internal_error(request: Request, exc: Exception) -> JSONResponse:
        # 与 Console/Channel API 对齐：未捕获异常必须回到统一 envelope，而不是裸 500 文本。
        emit_error_log(
            request_id=_state_or_header(request, "request_id", "X-Request-ID"),
            trace_id=_state_or_header(request, "trace_id", "X-Trace-ID"),
            tenant_id=_state_or_unknown(request, "tenant_id"),
            actor_id=_state_or_unknown(request, "actor_id"),
            method=request.method,
            route=request.url.path,
            error_type=type(exc).__name__,
            error_code=EVAL_INTERNAL_ERROR,
            stack=traceback.format_exc(),
        )
        return failure(EVAL_INTERNAL_ERROR, "internal error", status_code=500, request=request)


def _register_runs(app: FastAPI, service: EvaluationApplicationService) -> None:
    @app.post("/api/v1/eval/runs")
    async def create_run(payload: EvalRunCreatePayload) -> JSONResponse:
        record = await service.start_run(
            EvalRunRequest(
                run_id=payload.run_id,
                tenant_id=_tenant_id(),
                eval_set_id=payload.eval_set_id,
                eval_set_version=payload.eval_set_version,
                trace_id=payload.trace_id,
            )
        )
        return success(_run_payload(record))

    @app.get("/api/v1/eval/runs")
    async def list_runs() -> JSONResponse:
        records = await service.list_runs(tenant_id=_tenant_id())
        return success(
            {
                "items": [_run_payload(record) for record in records],
                "total": len(records),
            }
        )

    @app.get("/api/v1/eval/runs/{run_id}")
    async def get_run(run_id: str) -> JSONResponse:
        record = await service.get_run(run_id, tenant_id=_tenant_id())
        if record is None:
            raise EvalTraceabilityError(f"EvalRun 不存在: {run_id}")
        return success(_run_payload(record))


def _register_compare(app: FastAPI, service: EvaluationApplicationService) -> None:
    @app.post("/api/v1/eval/runs:compare")
    async def compare_runs(payload: EvalComparePayload) -> JSONResponse:
        regression = await service.compare(
            tenant_id=_tenant_id(),
            run_id=payload.run_id,
            baseline_run_id=payload.baseline_run_id,
        )
        return success(
            {
                "run_id": regression.run_id,
                "baseline_run_id": regression.baseline_run_id,
                "score_delta": regression.score_delta,
            }
        )


def _run_payload(record: EvalRunRecord) -> dict[str, object]:
    return {
        "run_id": record.run_id,
        "tenant_id": record.tenant_id,
        "eval_set_id": record.eval_set_id,
        "eval_set_version": record.eval_set_version,
        "runtime_profile_id": record.runtime_profile_id,
        "runtime_profile_version": record.runtime_profile_version,
        "trace_id": record.trace_id,
        "score": record.score,
        "passed": record.passed,
        "created_at": record.created_at.isoformat(),
        "execution_snapshot": _json_safe(record.execution_snapshot),
    }


def _json_safe(value: object) -> object:
    # ExecutionSnapshot 的 model_dump(mode="python") 保留 datetime；JSONResponse 只接受可 JSON
    # 序列化对象，这里递归转成 isoformat 字符串。
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _tenant_id() -> str:
    context = current_context()
    return context.tenant_id if context is not None else "unknown"


def _state_or_header(request: Request, state_key: str, header_name: str) -> str:
    value = getattr(request.state, state_key, None)
    if isinstance(value, str) and value:
        return value
    return request.headers.get(header_name, "unknown")


def _state_or_unknown(request: Request, state_key: str) -> str:
    value = getattr(request.state, state_key, None)
    return value if isinstance(value, str) and value else "unknown"
