"""工具结果与失败的类型化契约。

成功正文是数据，失败由 `ToolError` 或 `ToolResult.outcome` 表达；pipeline 不再解析正文
JSON 判断成败。`ToolError` 的序列化形状仍是模型可见的 `{"error": {...}}`，但它只由
工具自己抛出，不再作为成功结果的副产物被反向识别。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from .identity import ToolCall
from .outcome import (
    ExecutionOutcome,
    not_applicable_outcome,
    platform_sessions_summary,
    tool_exception_outcome,
)

if TYPE_CHECKING:
    from ..model.provider import ModelMessage


class ArtifactRole(StrEnum):
    RESULT = "RESULT"
    IMAGE = "IMAGE"
    ATTACHMENT = "ATTACHMENT"
    REPORT = "REPORT"
    TRANSCRIPT = "TRANSCRIPT"


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    artifact_id: str
    media_type: str
    filename: str | None = None
    checksum: str | None = None
    size: int | None = None
    role: ArtifactRole | None = None


@dataclass(frozen=True, slots=True)
class ToolEventArtifact:
    artifact_id: str
    role: ArtifactRole
    media_type: str
    filename: str | None = None
    checksum: str | None = None
    size: int | None = None


@dataclass(frozen=True, slots=True)
class ToolFollowUp:
    kind: str
    artifact_id: str | None = None
    media_type: str | None = None
    text: str | None = None


@dataclass(frozen=True, slots=True)
class ToolCompleted:
    tool_call_id: str
    tool_name: str
    status: str
    outcome: ExecutionOutcome
    content: str
    artifacts: tuple[ToolEventArtifact, ...] = ()
    follow_ups: tuple[ToolFollowUp, ...] = ()
    handler_entered: bool = False
    attempt: str | None = None


@dataclass(frozen=True, slots=True)
class ToolResult:
    content: str
    outcome: ExecutionOutcome = field(default_factory=not_applicable_outcome)
    follow_ups: tuple[ModelMessage, ...] = ()
    artifacts: tuple[ArtifactRef, ...] = ()
    call_id: str = ""
    #: 工具可声明“正文本身必须内联”（read_attachment/load_skill），整轮外置不得再包装。
    externalizable: bool = True

    def with_identity(self, call: ToolCall) -> ToolResult:
        return ToolResult(
            content=self.content,
            outcome=self.outcome.with_identity(
                call_id=call.call_id,
                tool_name=call.name,
                transport_status=self.outcome.transport_status,
            ),
            follow_ups=self.follow_ups,
            artifacts=self.artifacts,
            call_id=call.call_id,
            externalizable=self.externalizable,
        )


class ToolError(Exception):
    """工具自己宣告的失败；pipeline 只做序列化与审计，不再推断。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        detail: Mapping[str, Any] | None = None,
        retryable: bool | None = None,
        retry_after: str | None = None,
        status_code: int | None = None,
        platform_sessions: Mapping[str, Any] | None = None,
        sent: bool = False,
        outcome_known: bool = True,
    ) -> None:
        self.code = code
        self.message = message
        self.detail = dict(detail) if detail else None
        self.retryable = retryable
        self.retry_after = retry_after
        self.status_code = status_code
        self.sent = sent
        self.outcome_known = outcome_known
        self.platform_sessions = dict(platform_sessions) if platform_sessions else None
        super().__init__(message)

    def content(self) -> str:
        error: dict[str, object] = {"code": self.code, "message": self.message}
        if self.detail:
            error["detail"] = self.detail
        if self.retryable and self.outcome_known:
            error["retryable"] = True
        if not self.outcome_known:
            error["outcome_known"] = False
        payload: dict[str, object] = {"error": error}
        if self.platform_sessions:
            payload["platform_sessions"] = self.platform_sessions
        return json.dumps(payload, ensure_ascii=False)

    def outcome(self) -> ExecutionOutcome:
        summary = platform_sessions_summary(self.platform_sessions)
        evidence_text = "; ".join(part for part in (self.message, summary) if part)
        return tool_exception_outcome(
            code=self.code,
            message=self.message,
            retryable=self.retryable,
            retry_after=self.retry_after,
            status_code=self.status_code,
            evidence_text=evidence_text,
            sent=self.sent,
            outcome_known=self.outcome_known,
        )

    def to_result(self) -> ToolResult:
        return ToolResult(content=self.content(), outcome=self.outcome())


@dataclass(frozen=True, slots=True)
class ToolExecutionResult:
    """pipeline 的最终返回：调用身份 + typed 结果 + transport 状态。"""

    call: ToolCall
    result: ToolResult
    status: str


def completed_from_result(
    call: ToolCall, result: ToolResult, status: str
) -> ToolCompleted:
    """宿主/恢复视图消费的完成事实；正文、产物 role 与 follow-up 引用一起冻结。"""
    return ToolCompleted(
        tool_call_id=call.call_id,
        tool_name=call.name,
        status=status,
        outcome=result.outcome,
        content=result.content,
        artifacts=tuple(
            ToolEventArtifact(
                artifact_id=artifact.artifact_id,
                role=artifact.role or (
                    ArtifactRole.IMAGE
                    if artifact.media_type.startswith("image/")
                    else ArtifactRole.RESULT
                ),
                media_type=artifact.media_type,
                filename=artifact.filename,
                checksum=artifact.checksum,
                size=artifact.size,
            )
            for artifact in result.artifacts
        ),
        follow_ups=tuple(
            follow_up
            for message in result.follow_ups
            if (follow_up := _follow_up_from_message(message)) is not None
        ),
    )


def _follow_up_from_message(message: object) -> ToolFollowUp | None:
    """把进程内 follow-up 消息压成事件引用；base64 图像字节绝不进入事件。"""
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return ToolFollowUp(kind="text", text=content)
    if not isinstance(content, tuple):
        return None
    texts = [part for part in content if isinstance(part, str)]
    images = [part for part in content if not isinstance(part, str)]
    if images:
        media_type = getattr(images[0], "media_type", None)
        artifact_id = getattr(images[0], "artifact_id", None)
        return ToolFollowUp(
            kind="image",
            artifact_id=artifact_id if isinstance(artifact_id, str) and artifact_id else None,
            media_type=media_type if isinstance(media_type, str) else None,
            text="\n".join(texts) or None,
        )
    return ToolFollowUp(kind="text", text="\n".join(texts) or None)


def failure_result(call: ToolCall, error: ToolError, *, status: str = "FAILED") -> ToolExecutionResult:
    result = error.to_result().with_identity(call)
    return ToolExecutionResult(call=call, result=result, status=status)
