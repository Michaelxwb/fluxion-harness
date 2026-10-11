"""Bounded presentation DTOs. Raw events, reasoning and storage paths never leave this boundary."""

import json
import re
from typing import Literal

from muad_logging.redaction import redact_text, redact_value
from pydantic import BaseModel, Field, JsonValue

EventFieldKey = Literal[
    "tool_name",
    "tool_call_id",
    "skill_key",
    "script",
    "status",
    "duration_ms",
    "model",
    "provider",
    "attempt",
    "retry_reason",
    "input_tokens",
    "output_tokens",
    "operation_id",
    "task_id",
    "task_status",
    "completion_mode",
    "wait_generation",
    "execution_epoch",
    "error_code",
    "covers_up_to_seq",
    "bytes_before",
    "bytes_after",
    "operation_status",
    "protocol",
    "model_revision",
]
TEXT_LIMIT = 12000
ITEM_LIMIT = 20
_QUOTED_PAIR = re.compile(r'"(?P<key>[^"\\\r\n]{1,128})"\s*:\s*"(?P<value>(?:\\.|[^"\\])*)"')


class EventField(BaseModel):
    key: EventFieldKey
    value: str | int


class EventArgument(BaseModel):
    name: str
    value: str


class EventAttachment(BaseModel):
    id: str | None = None
    filename: str | None = None
    media_type: str | None = None
    size: int | None = None


class EventCompaction(BaseModel):
    layer: str
    groups: int | None = None
    bytes_saved: int | None = None


class ModelRunSummary(BaseModel):
    call_count: int
    success_count: int
    retry_count: int
    error_count: int
    input_tokens: int | None
    output_tokens: int | None
    duration_ms: int | None


class RunEventDetail(BaseModel):
    seq: int
    event_type: str
    text: str | None = None
    text_truncated: bool = False
    fields: list[EventField] = Field(default_factory=list)
    arguments: list[EventArgument] = Field(default_factory=list)
    attachments: list[EventAttachment] = Field(default_factory=list)
    compaction: list[EventCompaction] = Field(default_factory=list)
    model_state: Literal["LINKED", "UNLINKED", "PENDING"] | None = None
    model_summary: ModelRunSummary | None = None


def _redact_quoted_pair(match: re.Match[str]) -> str:
    key, value = match.group("key", "value")
    cleaned = redact_value({key: value})[key]
    return match.group(0) if cleaned == value else json.dumps(key) + ": " + json.dumps(cleaned)


def safe_text(value: JsonValue, limit: int = 512) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    # Also redact quoted JSON pairs embedded in prose/code without changing the message format.
    cleaned = redact_text(_QUOTED_PAIR.sub(_redact_quoted_pair, value))
    return cleaned[:limit]


def integer(value: JsonValue) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def add_fields(detail: RunEventDetail, data: dict[str, JsonValue], keys: tuple[EventFieldKey, ...]) -> None:
    for key in keys:
        value = data.get(key)
        normalized = integer(value) if isinstance(value, int) else safe_text(value)
        if normalized is not None:
            detail.fields.append(EventField(key=key, value=normalized))


def argument_views(value: JsonValue) -> list[EventArgument]:
    if not isinstance(value, dict):
        return []
    cleaned = redact_value(value)
    return [
        EventArgument(name=redact_text(key)[:128], value=json.dumps(item, ensure_ascii=False)[:1024])
        for key, item in list(cleaned.items())[:ITEM_LIMIT]
    ]


def attachment_view(data: dict[str, JsonValue]) -> EventAttachment:
    return EventAttachment(
        id=safe_text(data.get("artifact_id") or data.get("id")),
        filename=safe_text(data.get("filename")),
        media_type=safe_text(data.get("media_type")),
        size=integer(data.get("size")),
    )


def compaction_views(value: JsonValue) -> list[EventCompaction]:
    if not isinstance(value, dict):
        return []
    return [
        EventCompaction(
            layer=redact_text(key)[:128],
            groups=integer(item.get("groups")),
            bytes_saved=integer(item.get("bytes_saved")),
        )
        for key, item in list(value.items())[:ITEM_LIMIT]
        if isinstance(item, dict) and item.get("fired") is True
    ]
