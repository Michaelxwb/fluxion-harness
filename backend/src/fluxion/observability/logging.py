from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from datetime import UTC, datetime

import structlog

from fluxion.observability.context import RequestContext
from fluxion.observability.redaction import redact_mapping

ACCESS_LOGGER_NAME = "fluxion.console.access"
ERROR_LOGGER_NAME = "fluxion.console.error"
RUNTIME_ERROR_LOGGER_NAME = "fluxion.runtime.error"
_JSON_RENDERER = structlog.processors.JSONRenderer(ensure_ascii=False, sort_keys=True)


def emit_access_log(
    context: RequestContext,
    *,
    status_code: int,
    biz_code: int,
    latency_ms: float,
    headers: Mapping[str, object],
    query: Mapping[str, object],
    publish_id: str | None = None,
) -> None:
    level = "error" if status_code >= 500 else "info"
    event: dict[str, object] = {
        "timestamp": datetime.now(UTC).isoformat(),
        "level": level,
        "service": "fluxion-console",
        "environment": _environment(),
        "event": "http.request.completed",
        "request_id": context.request_id,
        "trace_id": context.trace_id,
        "tenant_id": context.tenant_id,
        "actor_id": context.actor_id,
        "method": context.method,
        "route": context.route,
        "status_code": status_code,
        "biz_code": biz_code,
        "latency_ms": round(latency_ms, 3),
        "headers": redact_mapping(headers),
        "query": redact_mapping(query),
    }
    if publish_id is not None:
        event["publish_id"] = publish_id
    if status_code >= 500:
        event["error_type"] = "internal_error"
        event["error_code"] = biz_code
    logger = logging.getLogger(ACCESS_LOGGER_NAME)
    if status_code >= 500:
        logger.error(_JSON_RENDERER(None, "", event))
    else:
        logger.info(_JSON_RENDERER(None, "", event))


def emit_error_log(
    *,
    request_id: str,
    trace_id: str,
    tenant_id: str,
    actor_id: str,
    method: str,
    route: str,
    error_type: str,
    error_code: int,
    stack: str,
) -> None:
    event = {
        "timestamp": datetime.now(UTC).isoformat(),
        "level": "error",
        "service": "fluxion-console",
        "environment": _environment(),
        "event": "http.request.failed",
        "request_id": request_id,
        "trace_id": trace_id,
        "tenant_id": tenant_id,
        "actor_id": actor_id,
        "method": method,
        "route": route,
        "error_type": error_type,
        "error_code": error_code,
        "stack": stack,
    }
    logging.getLogger(ERROR_LOGGER_NAME).error(_JSON_RENDERER(None, "", event))


def emit_runtime_error_log(
    *,
    request_id: str,
    trace_id: str,
    tenant_id: str,
    execution_id: str,
    runtime_profile_id: str,
    error_type: str,
    error_code: str,
    message: str,
    stack: str,
) -> None:
    event = {
        "timestamp": datetime.now(UTC).isoformat(),
        "level": "error",
        "service": "fluxion-runtime",
        "environment": _environment(),
        "event": "execution.failed",
        "request_id": request_id,
        "trace_id": trace_id,
        "tenant_id": tenant_id,
        "execution_id": execution_id,
        "runtime_profile_id": runtime_profile_id,
        "error_type": error_type,
        "error_code": error_code,
        "message": message,
        "stack": stack,
    }
    logging.getLogger(RUNTIME_ERROR_LOGGER_NAME).error(_JSON_RENDERER(None, "", event))


def _environment() -> str:
    return os.environ.get("FLUXION_ENV", "development")
