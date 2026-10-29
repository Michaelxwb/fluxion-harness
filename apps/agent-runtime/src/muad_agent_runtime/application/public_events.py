"""Webchat 安全公开投影：历史查询与 SSE 流共用唯一实现（design §3.6；TASK-051）。

- **同一事实、同一词表、同一过滤**：canonical 行 → 公开事实只经 `project_public_fact` 一个入口；
  历史分页（`WebchatHistoryService`）与 SSE 接缝（`project_public_stream_event`，TASK-061 的流
  端点消费）都从这里取事实，不各自维护第二份白名单与附件规则。
- **白名单**：`USER_MESSAGE`/`ASSISTANT_MESSAGE` → 公开消息（角色 USER/ASSISTANT，仅此两种）；
  公开流事件词表见 `PUBLIC_STREAM_EVENT_TYPES`（正文 delta、正文 snapshot 替换边界、业务阶段、
  终态、安全错误、附件引用、真实用户确认）。工具参数/结果、模型请求、SYSTEM、原始推理、
  记忆/未知内部事件默认不公开——过滤后 canonical seq 可以不连续，不补号、不重排。
- **附件安全**：只投影权威 `artifact` 行的元信息；失效（软删）附件显式 `available=false`；
  无权威行/尺寸不可投影/内部留档（`TRANSCRIPT`）的引用不伪造、不出现在公开投影里；
  `storage_key` 与内部载荷字段永不进入公开事实。
- **错误显式**：已知公开事件类型的坏载荷显式失败（`COMMON_INTERNAL_ERROR`），不静默当过滤；
  记忆确认的载荷只有本身就是完整安全 DTO 时才公开（内部 proposal 载荷不公开、不合成选项——
  完整候选投影属 TASK-056）。
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Literal, Protocol

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts.webchat import (
    WebchatAttachment,
    WebchatConfirmation,
    WebchatExecutionStatus,
    WebchatHistoryMessage,
)
from muad_contracts.webchat_events import (
    PublicWebchatEvent,
    WebchatArtifactEvent,
    WebchatConfirmationEvent,
    WebchatDeltaEvent,
    WebchatErrorEvent,
    WebchatReplyDelta,
    WebchatReplySnapshot,
    WebchatSafeError,
    WebchatSnapshotEvent,
    WebchatStageEvent,
    WebchatTerminal,
    WebchatTerminalEvent,
)
from pydantic import ValidationError

from ..infrastructure.models.runtime import Artifact
from .attachments.reference import attachment_ref
from .attachments.transcripts import TRANSCRIPT_ARTIFACT_TYPE

#: 可见消息事件白名单（canonical 协议标识，不是配置；写入方见 `channel_conversations` 与
#: `run_service._finalize_run`）。
PUBLIC_MESSAGE_EVENT_TYPES: tuple[str, ...] = ("USER_MESSAGE", "ASSISTANT_MESSAGE")
#: 事件类型 → 公开角色（只此两种可见角色；其余事件默认不公开）。
PUBLIC_ROLE_BY_EVENT_TYPE: dict[str, Literal["USER", "ASSISTANT"]] = {
    "USER_MESSAGE": "USER",
    "ASSISTANT_MESSAGE": "ASSISTANT",
}
#: canonical `stream_type` → 公开事件类型（TASK-006 判别联合的公开词表）；未知/内部默认不公开。
PUBLIC_STREAM_EVENT_TYPES: dict[str, str] = {
    "message.delta": "message.delta",
    "message.snapshot": "message.snapshot",
    "run.stage": "run.stage",
    "run.completed": "run.terminal",
    "run.failed": "run.error",
    "artifact.created": "artifact.created",
    "interrupt.required": "confirmation.required",
    "memory.confirmation_required": "confirmation.required",
}


class CanonicalRow(Protocol):
    """投影输入的最小行形状：canonical 行的只读子集。

    历史分页（列选择 `Row`）与 SSE 追尾（ORM 实体）都满足本形状；两路径因此共用同一投影，
    而不是各写一份「哪些行能公开」的判断。
    """

    id: uuid.UUID
    conversation_id: uuid.UUID
    run_id: uuid.UUID | None
    seq: int
    event_type: str
    stream_type: str | None
    payload_json: dict[str, Any]
    artifact_id: uuid.UUID | None
    create_time: datetime


def project_public_fact(
    row: CanonicalRow, artifacts: Mapping[uuid.UUID, Artifact]
) -> WebchatHistoryMessage | PublicWebchatEvent | None:
    """canonical 行 → 公开事实（**唯一投影入口**）；非公开事实返回 None（默认不公开）。

    可见消息行归历史路径；其余行按 `PUBLIC_STREAM_EVENT_TYPES` 白名单投影为公开流事件。
    """
    if row.event_type in PUBLIC_ROLE_BY_EVENT_TYPE:
        return _project_message(row, artifacts)
    if row.stream_type is None:
        return None
    return _project_stream_event(row, artifacts)


def project_public_stream_event(
    row: CanonicalRow, artifacts: Mapping[uuid.UUID, Artifact]
) -> PublicWebchatEvent | None:
    """SSE 侧接缝（API-07）：只出公开流事件；可见消息行归历史，不在流里重复。"""
    fact = project_public_fact(row, artifacts)
    if fact is None or isinstance(fact, WebchatHistoryMessage):
        return None
    return fact


def visible_attachment_ids(rows: Sequence[CanonicalRow]) -> list[uuid.UUID]:
    """可见用户消息载荷里的附件 id（去重保序）——附件元信息按页批量取，不逐条回查。"""
    ids: list[uuid.UUID] = []
    for row in rows:
        if PUBLIC_ROLE_BY_EVENT_TYPE.get(row.event_type) != "USER":
            continue
        payload = _payload(row)
        raw = payload.get("attachments")
        if not isinstance(raw, list):
            continue
        for entry in raw:
            artifact_id = _artifact_id_of(entry)
            if artifact_id is not None and artifact_id not in ids:
                ids.append(artifact_id)
    return ids


def _payload(row: CanonicalRow) -> dict[str, Any]:
    return row.payload_json if isinstance(row.payload_json, dict) else {}


def _project_message(
    row: CanonicalRow, artifacts: Mapping[uuid.UUID, Artifact]
) -> WebchatHistoryMessage | None:
    """可见消息投影：无法证实 Run 归属的行不冒认（与「无法证实归属保留审计」同口径）。

    坏载荷空正文兜底（不泄露、不整页失败）；附件只按权威行投影，不伪造不可用字节。
    """
    role = PUBLIC_ROLE_BY_EVENT_TYPE.get(row.event_type)
    if role is None or row.run_id is None:
        return None
    payload = _payload(row)
    text = payload.get("text")
    attachments = (
        _project_attachments(payload.get("attachments"), artifacts) if role == "USER" else []
    )
    return WebchatHistoryMessage(
        event_id=row.id,
        seq=row.seq,
        run_id=row.run_id,
        role=role,
        text=text if isinstance(text, str) else "",
        attachments=attachments,
        created_at=row.create_time,
    )


def _artifact_id_of(entry: object) -> uuid.UUID | None:
    """载荷里的附件引用 id；坏引用不猜测（不冒认别的产物），直接跳过。"""
    if not isinstance(entry, dict):
        return None
    try:
        return uuid.UUID(str(entry.get("artifact_id")))
    except (TypeError, ValueError):
        return None


def project_attachment(
    artifact_id: uuid.UUID, artifacts: Mapping[uuid.UUID, Artifact]
) -> WebchatAttachment | None:
    """附件安全投影（历史/SSE/通知共用的**唯一实现**）：只出引用与元信息；失效附件明确
    `available=false`，不伪造不可用字节。

    无权威行/尺寸不可投影的引用**不伪造**（不猜 checksum）；内部留档（`TRANSCRIPT`）不是可下载的
    公开附件，一律不投影。字节是否可取（清理/预览授权）属下载端点（TASK-055）。
    """
    artifact = artifacts.get(artifact_id)
    if artifact is None or artifact.size < 1:
        return None
    if artifact.artifact_type == TRANSCRIPT_ARTIFACT_TYPE:
        return None
    ref = attachment_ref(artifact)
    return WebchatAttachment(
        artifact_id=artifact_id,
        kind=ref.kind,
        media_type=ref.media_type,
        size=ref.size,
        filename=ref.filename,
        checksum=ref.checksum,
        available=not artifact.is_deleted,
    )


def _project_attachments(
    raw: object, artifacts: Mapping[uuid.UUID, Artifact]
) -> list[WebchatAttachment]:
    if not isinstance(raw, list):
        return []
    projected: list[WebchatAttachment] = []
    for entry in raw:
        artifact_id = _artifact_id_of(entry)
        if artifact_id is None:
            continue
        attachment = project_attachment(artifact_id, artifacts)
        if attachment is not None:
            projected.append(attachment)
    return projected


def _project_stream_event(
    row: CanonicalRow, artifacts: Mapping[uuid.UUID, Artifact]
) -> PublicWebchatEvent | None:
    """公开流事件投影：白名单之外的 stream_type 默认不公开（过滤跳号合法）。"""
    run_id = row.run_id
    if run_id is None:
        # 无法证实 Run 归属的流事件不冒认（与消息投影同口径）。
        return None
    public_type = PUBLIC_STREAM_EVENT_TYPES.get(row.stream_type or "")
    if public_type == "message.delta":
        return _delta_event(row, run_id)
    if public_type == "message.snapshot":
        return _snapshot_event(row, run_id)
    if public_type == "run.stage":
        return _stage_event(row, run_id)
    if public_type == "run.terminal":
        return _terminal_event(row, run_id)
    if public_type == "run.error":
        return _error_event(row, run_id)
    if public_type == "artifact.created":
        return _artifact_event(row, run_id, artifacts)
    if public_type == "confirmation.required":
        return _confirmation_event(row, run_id)
    return None


def _identity(row: CanonicalRow, run_id: uuid.UUID) -> dict[str, Any]:
    """公开事件身份：持久行事实（不新分配 seq；过滤跳号不补号）。"""
    return {
        "event_id": row.id,
        "conversation_id": row.conversation_id,
        "run_id": run_id,
        "seq": row.seq,
        "timestamp": row.create_time,
    }


def _malformed(row: CanonicalRow) -> AppError:
    """已知公开事件的坏载荷：显式内部错误（只带安全身份，不回显原始载荷）。"""
    return AppError(
        ErrorCode.COMMON_INTERNAL_ERROR,
        data={"event_id": str(row.id), "seq": row.seq, "stream_type": row.stream_type},
    )


def _delta_event(row: CanonicalRow, run_id: uuid.UUID) -> WebchatDeltaEvent:
    """正文增量：只出文本与生成号；缺省生成号 0 = 首次生成（多代由载荷显式携带）。"""
    payload = _payload(row)
    try:
        data = WebchatReplyDelta.model_validate(
            {"reply_generation": payload.get("reply_generation", 0), "text": payload.get("delta")}
        )
    except ValidationError as exc:
        raise _malformed(row) from exc
    return WebchatDeltaEvent(type="message.delta", data=data, **_identity(row, run_id))


def _snapshot_event(row: CanonicalRow, run_id: uuid.UUID) -> WebchatSnapshotEvent:
    """新代替换边界（TASK-062）：整组替换旧代正文；分片坐标在公开边拆分（本行即整组）。

    坏载荷显式失败：它是正文替换事实，不能静默当过滤（否则旧代正文会与新代 delta 拼接）。
    """
    payload = _payload(row)
    try:
        data = WebchatReplySnapshot.model_validate(
            {
                "reply_generation": payload.get("reply_generation", 0),
                "text": payload.get("text"),
                "part_index": 0,
                "part_count": 1,
            }
        )
    except ValidationError as exc:
        raise _malformed(row) from exc
    return WebchatSnapshotEvent(type="message.snapshot", data=data, **_identity(row, run_id))


def _stage_event(row: CanonicalRow, run_id: uuid.UUID) -> WebchatStageEvent:
    """业务阶段：载荷本身就是 `WebchatExecutionStatus` 线格式（写入方同源），严格解析。"""
    try:
        data = WebchatExecutionStatus.model_validate(_payload(row))
    except ValidationError as exc:
        raise _malformed(row) from exc
    return WebchatStageEvent(type="run.stage", data=data, **_identity(row, run_id))


def _terminal_event(row: CanonicalRow, run_id: uuid.UUID) -> WebchatTerminalEvent:
    """终态：只出状态词表；正文/原因等载荷字段不透传（正文归历史与增量）。"""
    payload = _payload(row)
    try:
        data = WebchatTerminal.model_validate({"status": payload.get("status")})
    except ValidationError as exc:
        raise _malformed(row) from exc
    return WebchatTerminalEvent(type="run.terminal", data=data, **_identity(row, run_id))


def _error_event(row: CanonicalRow, run_id: uuid.UUID) -> WebchatErrorEvent:
    """安全错误：只出稳定码与保守重试性；上游原文/异常细节永不透传。"""
    payload = _payload(row)
    try:
        data = WebchatSafeError.model_validate(
            {"code": payload.get("error_code"), "retryable": payload.get("retryable", False)}
        )
    except ValidationError as exc:
        raise _malformed(row) from exc
    return WebchatErrorEvent(type="run.error", data=data, **_identity(row, run_id))


def _artifact_event(
    row: CanonicalRow, run_id: uuid.UUID, artifacts: Mapping[uuid.UUID, Artifact]
) -> WebchatArtifactEvent | None:
    """附件引用：权威 `artifact` 行优先（行 `artifact_id` 列，兼容载荷回退），不伪造元信息。"""
    artifact_id = row.artifact_id or _artifact_id_of(_payload(row))
    if artifact_id is None:
        return None
    attachment = project_attachment(artifact_id, artifacts)
    if attachment is None:
        return None
    return WebchatArtifactEvent(type="artifact.created", data=attachment, **_identity(row, run_id))


def _confirmation_event(row: CanonicalRow, run_id: uuid.UUID) -> WebchatConfirmationEvent | None:
    """真实用户确认：载荷本身是完整安全 DTO 才公开。

    记忆确认的当前载荷（proposal_id/proposal_hash/…）不是公开 DTO：**不公开、不合成选项**；
    完整候选投影属 TASK-056。坏形状不报错也不透传，避免内部载荷借确认事件外泄。
    """
    try:
        data = WebchatConfirmation.model_validate(_payload(row))
    except ValidationError:
        return None
    return WebchatConfirmationEvent(
        type="confirmation.required", data=data, **_identity(row, run_id)
    )
