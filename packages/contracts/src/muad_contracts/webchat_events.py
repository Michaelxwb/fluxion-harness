"""Public event protocol; canonical projection and authorization belong to Runtime."""

import json
from typing import Annotated, Literal, Self, get_args
from uuid import UUID

from pydantic import AwareDatetime, ConfigDict, Field, StrictBool, StrictStr, TypeAdapter, model_validator

from .canonical import ensure_strict_json
from .enums import TerminalStatus
from .webchat import (
    ChatCount,
    ChatPositiveCount,
    WebchatAttachment,
    WebchatConfirmation,
    WebchatContract,
    WebchatExecutionStatus,
    WebchatPage,
)


class WebchatReplyDelta(WebchatContract):
    reply_generation: ChatCount
    text: StrictStr


class WebchatReplySnapshot(WebchatReplyDelta):
    """Zero-based parts of ONE canonical event; only the full group is applicable."""

    part_index: ChatCount
    part_count: ChatPositiveCount

    @model_validator(mode="after")
    def _part_coordinates(self) -> Self:
        if self.part_index >= self.part_count:
            raise ValueError("part_index must be less than part_count")
        return self


class WebchatSafeError(WebchatContract):
    """Stable catalog code only; no raw upstream message, details or exception."""

    code: Annotated[StrictStr, Field(pattern=r"^[A-Z][A-Z0-9_]+$")]
    retryable: StrictBool


class WebchatTerminal(WebchatContract):
    status: TerminalStatus


class WebchatEventIdentity(WebchatContract):
    event_id: UUID
    conversation_id: UUID
    run_id: UUID
    seq: ChatPositiveCount
    timestamp: AwareDatetime


class WebchatDeltaEvent(WebchatEventIdentity):
    type: Literal["message.delta"]
    data: WebchatReplyDelta


class WebchatSnapshotEvent(WebchatEventIdentity):
    type: Literal["message.snapshot"]
    data: WebchatReplySnapshot


class WebchatStageEvent(WebchatEventIdentity):
    type: Literal["run.stage"]
    data: WebchatExecutionStatus


class WebchatTerminalEvent(WebchatEventIdentity):
    type: Literal["run.terminal"]
    data: WebchatTerminal


class WebchatErrorEvent(WebchatEventIdentity):
    type: Literal["run.error"]
    data: WebchatSafeError


class WebchatConfirmationEvent(WebchatEventIdentity):
    type: Literal["confirmation.required"]
    data: WebchatConfirmation


class WebchatArtifactEvent(WebchatEventIdentity):
    type: Literal["artifact.created"]
    data: WebchatAttachment


class WebchatCheckpointData(WebchatContract):
    """A checkpoint changes no visible message content."""


class WebchatCheckpoint(WebchatContract):
    type: Literal["checkpoint"]
    conversation_id: UUID
    run_id: UUID
    seq: ChatCount
    timestamp: AwareDatetime
    data: WebchatCheckpointData


PublicWebchatEvent = Annotated[
    WebchatDeltaEvent | WebchatSnapshotEvent | WebchatStageEvent | WebchatTerminalEvent
    | WebchatErrorEvent | WebchatConfirmationEvent | WebchatArtifactEvent | WebchatCheckpoint,
    Field(discriminator="type"),
]
_event_adapter: TypeAdapter[PublicWebchatEvent] = TypeAdapter(
    PublicWebchatEvent, config=ConfigDict(hide_input_in_errors=True),
)
_event_variants = get_args(get_args(PublicWebchatEvent)[0])
_public_event_types = frozenset(
    name for variant in _event_variants for name in get_args(variant.model_fields["type"].annotation)
)


def parse_public_event(value: dict[str, object]) -> PublicWebchatEvent | None:
    """Parse already-projected data. Unknown types stay private; malformed known types fail.

    Never feed raw canonical payloads to this parser as a substitute for a safe projection.
    The whitelist is derived from the discriminated union, not a second event registry.
    """
    ensure_strict_json(value)
    event_type = value.get("type")
    if not isinstance(event_type, str) or event_type not in _public_event_types:
        return None
    return _event_adapter.validate_python(value)


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-JSON number: {value}")


def decode_public_event_json(value: str) -> PublicWebchatEvent | None:
    decoded: object = json.loads(value, parse_constant=_reject_json_constant)
    if not isinstance(decoded, dict):
        raise ValueError("public event must be a JSON object")
    return parse_public_event(decoded)


class WebchatRunCursor(WebchatContract):
    run_id: UUID
    after_seq: ChatCount = 0


class WebchatCursorWindow(WebchatContract):
    run_id: UUID
    minimum_after_seq: ChatCount
    last_seq: ChatCount

    @model_validator(mode="after")
    def _retention_watermark(self) -> Self:
        if self.minimum_after_seq > self.last_seq:
            raise ValueError("minimum_after_seq cannot exceed last_seq")
        return self


class WebchatCursorError(WebchatCursorWindow):
    code: Literal["CURSOR_EXPIRED", "CURSOR_AHEAD", "CURSOR_RUN_MISMATCH"]
    requested_seq: ChatCount


def validate_run_cursor(
    request: WebchatRunCursor, window: WebchatCursorWindow,
) -> WebchatCursorError | None:
    """Check a trusted retained window; legal filtering gaps never cause an error."""
    code: Literal["CURSOR_EXPIRED", "CURSOR_AHEAD", "CURSOR_RUN_MISMATCH"]
    if request.run_id != window.run_id:
        code = "CURSOR_RUN_MISMATCH"
    elif request.after_seq < window.minimum_after_seq:
        code = "CURSOR_EXPIRED"
    elif request.after_seq > window.last_seq:
        code = "CURSOR_AHEAD"
    else:
        return None
    return WebchatCursorError(
        code=code, requested_seq=request.after_seq, run_id=window.run_id,
        minimum_after_seq=window.minimum_after_seq, last_seq=window.last_seq,
    )


class WebchatNotification(WebchatContract):
    id: UUID
    seq: ChatPositiveCount
    entry_id: UUID
    agent_id: UUID
    conversation_id: UUID
    run_id: UUID
    task_id: UUID | None = None
    event_id: UUID | None = None
    kind: Literal["RUN_COMPLETED", "RUN_WAITING_INPUT", "TASK_RESULT"]
    summary: StrictStr
    attachments: list[WebchatAttachment] = Field(default_factory=list)
    created_at: AwareDatetime
    read_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def _task_source(self) -> Self:
        if self.kind == "TASK_RESULT" and self.task_id is None:
            raise ValueError("TASK_RESULT requires task_id")
        return self


class WebchatNotificationPage(WebchatPage[WebchatNotification]):
    """next_seq is a scanned feed watermark, separate from read and message cursors."""

    through_seq: ChatCount
    next_seq: ChatCount
    unread_count: ChatCount

    @model_validator(mode="after")
    def _feed_watermarks(self) -> Self:
        if self.next_seq > self.through_seq:
            raise ValueError("next_seq cannot exceed through_seq")
        previous = 0
        for item in self.items:
            if item.seq <= previous or item.seq > self.next_seq:
                raise ValueError("notification seq must increase within next_seq")
            previous = item.seq
        return self


class WebchatTaskResultRegistration(WebchatContract):
    """受信 Task 结果入箱请求（Runtime Owner 端口；TASK-064/TASK-068）。

    只携带 Worker 真实持有的身份事实（task/run/actor user 与产出的 artifact）：会话、入口与
    Agent 由 Runtime 从真实行导出，调用方自报入口/Agent 没有位置；每个 artifact 必须属于该
    Task（`artifact.task_id`），否则拒绝——安全投影里的附件引用不认调用方声明。
    """

    task_id: UUID
    run_id: UUID
    user_id: UUID
    artifact_ids: list[UUID] = Field(default_factory=list)

    @model_validator(mode="after")
    def _distinct_artifacts(self) -> Self:
        if len(set(self.artifact_ids)) != len(self.artifact_ids):
            raise ValueError("artifact_ids must be distinct")
        return self


class WebchatTaskResultReceipt(WebchatContract):
    """持久入箱回执：`persisted=True` 只在收件箱行已提交后返回；重投 `duplicate=True` 命中
    原回执（不新占 seq、不新增未读）。"""

    task_id: UUID
    notification_id: UUID
    seq: ChatPositiveCount
    persisted: StrictBool
    duplicate: StrictBool


class WebchatNotificationReadReceipt(WebchatContract):
    """API-11 显式已读回执（TASK-064）：只回明确读过的通知身份与持久 `read_at`。

    已读以持久回执为准（不按重放帧累加、不夹带客户端自报内容）；重复标记返回原 `read_at`。
    """

    id: UUID
    read_at: AwareDatetime
