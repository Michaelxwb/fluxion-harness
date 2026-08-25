from __future__ import annotations

from fastapi.responses import JSONResponse

from fluxion.api.responses import success
from fluxion.errors.console import VALIDATION_FAILED, ConsoleError
from fluxion.observability.context import current_context
from fluxion.resources import ResourceKind
from fluxion.services.console_contracts import ConsoleActor, PublishResourceResult
from fluxion.services.console_payloads import publish_payload


def _page(items: list[dict[str, object]], page: int, page_size: int, total: int) -> dict[str, object]:
    return {"items": items, "page": page, "page_size": page_size, "total": total}


def _publication_response(result: PublishResourceResult) -> JSONResponse:
    response = success(publish_payload(result))
    response.headers["X-Publish-ID"] = result.publish_id
    return response


def _actor(actor_id: str | None) -> ConsoleActor:
    context = current_context()
    if context is None:
        return ConsoleActor(
            tenant_id="unknown",
            actor_id=actor_id or "unknown",
            request_id="req_unknown",
            trace_id="trace_unknown",
        )
    return ConsoleActor(
        tenant_id=context.tenant_id,
        actor_id=context.actor_id,
        request_id=context.request_id,
        trace_id=context.trace_id,
    )


def _kind(value: str) -> ResourceKind:
    try:
        return ResourceKind(value)
    except ValueError as exc:
        raise ConsoleError(VALIDATION_FAILED, "invalid resource type", 400) from exc
