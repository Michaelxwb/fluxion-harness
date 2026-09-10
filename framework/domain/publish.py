import hashlib
import json
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from framework.web.errors import AppError


class ServiceDraft(BaseModel):
    """Typed shape of a Service draft payload (01 FEAT-02).

    The draft is validated **before** anything is frozen. An untyped dict would
    let a malformed draft reach the release payload, where it would only fail
    when an Execution tried to use it. ``extra="allow"`` keeps the payload's
    open-ended sections (execution_spec / confirmation_rules / ...) intact.
    """

    model_config = ConfigDict(extra="allow")

    name: str = Field(min_length=1, max_length=256)
    goal: str = Field(min_length=1)
    resource_scope_type: str | None = None


class PublishedService(BaseModel):
    """Result of LIB-01 publish_service (01 FEAT-02)."""

    service_id: UUID
    release_id: str
    content_hash: str
    frozen_payload: dict[str, object] = Field(default_factory=dict)


def _canonical(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def build_service_release(
    *,
    service_id: UUID,
    service_key: str,
    draft: dict[str, object],
    agent_snapshot: dict[str, object] | None = None,
    resource_scope_schema_hash: str | None = None,
) -> PublishedService:
    """Validate a draft and build the immutable release payload (01 LIB-01).

    Returns a domain object, never a bare dict. Frozen payload carries the
    resolved agent config and scope schema hash so running executions never
    drift when live config changes (V1.7 D01/D04).

    Per FEAT-04 the declared ``resource_scope_type`` travels **inside** the
    draft payload; ``resource_scope_schema_hash`` is derived by the caller from
    the Integration manifest registry (RULE-05) and never trusted from input.
    """
    try:
        validated = ServiceDraft.model_validate(draft)
    except ValidationError as exc:
        first = exc.errors()[0]
        location = ".".join(str(part) for part in first["loc"]) or "<root>"
        raise AppError(
            code="SERVICE_DRAFT_INVALID",
            message=f"service draft invalid at {location}: {first['msg']}",
            status_code=422,
        ) from exc
    declared_scope = validated.resource_scope_type
    frozen: dict[str, object] = {
        "service_key": service_key,
        "draft": draft,
        "agent_snapshot": agent_snapshot or {},
        "resource_scope_type": declared_scope if isinstance(declared_scope, str) else None,
        "resource_scope_schema_hash": resource_scope_schema_hash,
    }
    content_hash = hashlib.sha256(_canonical(frozen).encode("utf-8")).hexdigest()
    return PublishedService(
        service_id=service_id,
        release_id=f"r-{content_hash[:12]}",
        content_hash=content_hash,
        frozen_payload=frozen,
    )
