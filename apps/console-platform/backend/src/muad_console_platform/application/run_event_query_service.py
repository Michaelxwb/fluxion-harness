"""Lazy event details, joined to existing facts and projected through a typed whitelist."""

import uuid

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from pydantic import JsonValue, TypeAdapter, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.repositories.run_event_query_repository import (
    RunEventDetailRow,
    RunEventQueryRepository,
)
from .run_event_view import (
    ITEM_LIMIT,
    TEXT_LIMIT,
    ModelRunSummary,
    RunEventDetail,
    add_fields,
    argument_views,
    attachment_view,
    compaction_views,
    safe_text,
)

MESSAGE_EVENTS = {"USER_MESSAGE", "ASSISTANT_MESSAGE", "ASSISTANT_TURN", "BACKGROUND_RESULT"}
TOOL_EVENTS = {"TOOL_CALL_STARTED", "TOOL_CALL", "SKILL_LOADED"}
MODEL_EVENTS = {"MODEL_CALL_STARTED", "MODEL_CALL_COMPLETED"}
OPERATION_EVENTS = {
    "TOOL_SUBMISSION_PENDING",
    "TOOL_SUBMISSION_FAILED",
    "TOOL_TASK_ACCEPTED",
    "TOOL_RESULT_RECEIVED",
    "BACKGROUND_RESULT",
    "BACKGROUND_RESULT_LATE",
    "TASK_ACCEPTED",
}


def _result_excerpt(row: RunEventDetailRow) -> JsonValue:
    if row.artifact is not None:
        return row.artifact.get("preview")
    content = row.event_data.get("content")
    if isinstance(content, str):
        try:
            parsed: JsonValue = TypeAdapter[JsonValue](JsonValue).validate_json(content)
        except ValidationError:
            return content
        reference = parsed.get("artifact") if isinstance(parsed, dict) else None
        if isinstance(reference, dict):
            # Only the preview from a persisted result reference, never its internal fields.
            return reference.get("preview")
    return content


def _content(detail: RunEventDetail, row: RunEventDetailRow) -> None:
    data = row.event_data
    content = None
    if row.event_type in MESSAGE_EVENTS:
        content = data.get("text") if row.event_type != "BACKGROUND_RESULT" else _result_excerpt(row)
    elif row.event_type == "TOOL_CALL":
        content = data.get("preview") or (row.artifact or {}).get("preview")
    cleaned = safe_text(content, TEXT_LIMIT + 1)
    detail.text_truncated = cleaned is not None and len(cleaned) > TEXT_LIMIT
    detail.text = cleaned[:TEXT_LIMIT] if cleaned is not None else None
    attachments = data.get("attachments") if row.event_type == "USER_MESSAGE" else None
    if isinstance(attachments, list):
        detail.attachments = [
            attachment_view(item) for item in attachments[:ITEM_LIMIT] if isinstance(item, dict)
        ]
    if row.artifact is not None:
        attachment = attachment_view(row.artifact)
        if (
            attachment.id not in {item.id for item in detail.attachments}
            and len(detail.attachments) < ITEM_LIMIT
        ):
            detail.attachments.append(attachment)


def _model_pending(row: RunEventDetailRow) -> bool:
    if row.event_type != "MODEL_CALL_STARTED" or row.run_status not in {
        "CREATED",
        "RUNNING",
        "WAITING_TOOL",
        "WAITING_INPUT",
    }:
        return False
    reference = row.event_data.get("model_audit_id")
    if not isinstance(reference, str):
        return False
    try:
        uuid.UUID(reference)
    except ValueError:
        return False
    return True


def _model_details(detail: RunEventDetail, row: RunEventDetailRow) -> None:
    if row.event_type not in MODEL_EVENTS:
        return
    snapshot = row.model_snapshot or {}
    add_fields(detail, snapshot, ("protocol", "model_revision"))
    if row.model is not None:
        detail.model_state = "LINKED"
        add_fields(
            detail,
            row.model,
            (
                "model",
                "provider",
                "status",
                "duration_ms",
                "attempt",
                "retry_reason",
                "input_tokens",
                "output_tokens",
                "error_code",
            ),
        )
        return
    add_fields(detail, snapshot, ("model",))
    detail.model_state = "PENDING" if _model_pending(row) else "UNLINKED"
    if row.model_summary is not None:
        detail.model_summary = ModelRunSummary.model_validate(row.model_summary)


def _call_details(detail: RunEventDetail, row: RunEventDetailRow) -> None:
    if row.event_type in TOOL_EVENTS:
        data = row.tool or row.event_data
        add_fields(detail, data, ("tool_name", "tool_call_id", "status", "duration_ms", "error_code"))
        arguments = data.get("arguments")
        detail.arguments = argument_views(arguments)
        if isinstance(arguments, dict):
            add_fields(detail, arguments, ("skill_key", "script"))
        if row.event_type == "SKILL_LOADED":
            add_fields(detail, row.event_data, ("skill_key",))
    elif row.event_type == "ASSISTANT_TURN":
        calls = row.event_data.get("tool_calls")
        if isinstance(calls, list):
            for call in calls[:ITEM_LIMIT]:
                if isinstance(call, dict):
                    add_fields(detail, {"tool_name": call.get("name")}, ("tool_name",))


def event_view(row: RunEventDetailRow) -> RunEventDetail:
    detail = RunEventDetail(seq=row.seq, event_type=row.event_type)
    _content(detail, row)
    _model_details(detail, row)
    _call_details(detail, row)
    if row.event_type in OPERATION_EVENTS:
        add_fields(detail, row.event_data, ("status", "error_code"))
        add_fields(detail, {"status": row.event_data.get("terminal_status")}, ("status",))
        data = row.operation or row.event_data
        add_fields(detail, data, ("operation_id", "task_id", "task_status", "completion_mode"))
        if row.operation:
            add_fields(detail, {"operation_status": row.operation.get("status")}, ("operation_status",))
    if row.event_type in {"CONTEXT_SUMMARY", "CONTEXT_COMPACTED"}:
        add_fields(detail, row.event_data, ("covers_up_to_seq", "bytes_before", "bytes_after"))
        detail.compaction = compaction_views(row.event_data.get("layers"))
    if row.event_type in {
        "RUN_CREATED",
        "RUN_COMPLETED",
        "RUN_FAILED",
        "CANCEL",
        "RUN_WAITING_TOOL",
        "RUN_RESUMED",
    }:
        add_fields(detail, row.event_data, ("status", "wait_generation", "execution_epoch", "error_code"))
    return detail


class RunEventQueryService:
    def __init__(self, session: AsyncSession) -> None:
        self._repository = RunEventQueryRepository(session)

    async def get_event(self, tenant_id: str, run_id: uuid.UUID, seq: int) -> RunEventDetail:
        row = await self._repository.detail(tenant_id, run_id, seq)
        if row is None:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
        return event_view(row)
