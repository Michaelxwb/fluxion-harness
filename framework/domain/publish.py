import hashlib
import json
from uuid import UUID

from pydantic import BaseModel, Field

from framework.web.errors import AppError


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
    resource_scope_type: str | None = None,
    resource_scope_schema_hash: str | None = None,
) -> PublishedService:
    """Validate a draft and build the immutable release payload (01 LIB-01).

    Returns a domain object, never a bare dict. Frozen payload carries the
    resolved agent config and scope schema hash so running executions never
    drift when live config changes (V1.7 D01/D04).
    """
    for field in ("name", "goal"):
        if not draft.get(field):
            raise AppError(
                code="SERVICE_DRAFT_INVALID",
                message=f"service draft missing required field: {field}",
                status_code=422,
            )
    frozen: dict[str, object] = {
        "service_key": service_key,
        "draft": draft,
        "agent_snapshot": agent_snapshot or {},
        "resource_scope_type": resource_scope_type,
        "resource_scope_schema_hash": resource_scope_schema_hash,
    }
    content_hash = hashlib.sha256(_canonical(frozen).encode("utf-8")).hexdigest()
    return PublishedService(
        service_id=service_id,
        release_id=f"r-{content_hash[:12]}",
        content_hash=content_hash,
        frozen_payload=frozen,
    )
