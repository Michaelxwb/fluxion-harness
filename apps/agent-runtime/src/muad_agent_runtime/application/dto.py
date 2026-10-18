from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Self

from muad_contracts import RunStatus, WaitReason
from pydantic import BaseModel, ConfigDict, model_validator

from ..infrastructure.models.runtime import Conversation, RunRecord, RuntimeSnapshot
from .execution_status import project_execution_status
from .memory_confirmation_resume import ConfirmationBinding


@dataclass(frozen=True, slots=True)
class RunWaitSummary:
    """API-05：Run 详情的只读等待投影（等待起点/原因 + 异步操作计数）。"""

    waiting_since: datetime | None
    waiting_reason: WaitReason | None
    pending_join_count: int
    pending_submission_count: int
    continuation_count: int


class ResumeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str | None = None
    type: Literal["text"] = "text"
    text: str = ""


class ResumeRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    platform_user_id: uuid.UUID
    input: ResumeInput
    #: 待确认恢复的原提议身份（API-08 `WebchatResumeRequest` 的两个判别键）：要么都给、要么都
    #: 不给 —— 半个绑定不是请求语义，显式拒绝；给了就在锁内逐项复核（`ConfirmationBinding`）。
    submission_id: uuid.UUID | None = None
    source_event_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _confirmation_binding_is_complete(self) -> Self:
        if (self.submission_id is None) != (self.source_event_id is None):
            raise ValueError("confirmation binding requires both submission_id and source_event_id")
        return self

    def confirmation_binding(self) -> ConfirmationBinding | None:
        """请求字段 → 应用层绑定对象（无绑定返回 None；半个绑定在校验层已被拒）。"""
        if self.submission_id is None or self.source_event_id is None:
            return None
        return ConfirmationBinding(
            submission_id=self.submission_id, source_event_id=self.source_event_id
        )


class CancelSource(BaseModel):
    """精确取消的来源身份（design §3.4 API-08 / RISK-F03：不用「用户+Agent 最新 Run」定位）。

    显式会话（网页：`conversation_id`）或入口+外线线程（入口指针：`entry_id`/
    `external_thread_id`）**至少给一个**；两个都给时以会话为准，入口归属仍要一致
    （核属在应用层完成，不在这里猜）。
    """

    model_config = ConfigDict(extra="forbid")

    conversation_id: uuid.UUID | None = None
    entry_id: uuid.UUID | None = None
    external_thread_id: str = ""

    @model_validator(mode="after")
    def _identifies_a_source(self) -> Self:
        if self.conversation_id is None and self.entry_id is None:
            raise ValueError("cancel source must carry a conversation_id or an entry_id")
        return self


class CancelActiveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: uuid.UUID
    platform_user_id: uuid.UUID
    #: 来源身份：给了就只取消**该来源**的活跃 Run；没给保留既有 (agent,user) 口径
    #: （调用方没有来源事实时不猜来源）。
    source: CancelSource | None = None


class CreateConversationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: uuid.UUID
    platform_user_id: uuid.UUID
    #: 入口账号（网页稳定入口的 `bot_account_id`）：可归属入口随会话冻结入口归属并登记当前指针；
    #: 缺省 = 不可归属入口（与 `RunRequest.channel.bot_id` 的非 UUID 口径同一语义，不编造入口）。
    entry_id: uuid.UUID | None = None
    #: 外线线程：无显式会话的入口按 (入口, 线程) 维护当前指针；网页不依赖该指针（RULE-10）。
    external_thread_id: str = ""


def display_run_status(status: str, cancel_requested: bool) -> str:
    if cancel_requested and status in (RunStatus.CREATED, RunStatus.RUNNING):
        return "CANCELLING"
    return status


def _isoformat(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def conversation_view(conversation: Conversation) -> dict[str, Any]:
    return {
        "conversation_id": str(conversation.id),
        "agent_id": str(conversation.agent_id),
        "user_id": str(conversation.user_id),
        # 入口归属是会话的公开事实（API-08 201 的 `entry_id` 投影）：不可归属入口为 None。
        "entry_id": (
            str(conversation.channel_account_id)
            if conversation.channel_account_id is not None
            else None
        ),
        "title": conversation.title,
        "status": conversation.status,
        "last_seq": conversation.last_seq,
        "last_run_id": str(conversation.last_run_id) if conversation.last_run_id else None,
        "create_time": _isoformat(conversation.create_time),
        "update_time": _isoformat(conversation.update_time),
    }


def snapshot_summary(snapshot: RuntimeSnapshot | None) -> dict[str, Any] | None:
    if snapshot is None:
        return None
    catalog_revision, catalog_hash = _mcp_catalog_summary(snapshot.mcp_catalog_json)
    return {
        "snapshot_id": str(snapshot.id),
        "schema_version": snapshot.schema_version,
        "agent_revision": snapshot.agent_revision,
        "model_revision": snapshot.model_revision,
        "prompt_template_version": snapshot.prompt_template_version,
        "content_hash": snapshot.content_hash,
        "mcp_catalog_revision": catalog_revision,
        "mcp_catalog_hash": catalog_hash,
    }


def _mcp_catalog_summary(catalog: list[dict[str, Any]] | None) -> tuple[int | None, str | None]:
    if not catalog:
        return None, None
    first = catalog[0]
    revision = first.get("catalog_revision")
    catalog_hash = first.get("catalog_hash")
    return (
        revision if isinstance(revision, int) else None,
        catalog_hash if isinstance(catalog_hash, str) else None,
    )


def run_view(run: RunRecord, snapshot: RuntimeSnapshot | None, wait: RunWaitSummary) -> dict[str, Any]:
    return {
        "run_id": str(run.id),
        "conversation_id": str(run.conversation_id),
        "agent_id": str(run.agent_id),
        "user_id": str(run.user_id),
        "status": display_run_status(run.status, run.cancel_requested),
        # 持久阶段投影（TASK-036 的唯一投影函数）：网页 202/恢复回执与 SSE/详情同源，不推算。
        "execution_status": project_execution_status(run).model_dump(mode="json"),
        "trace_id": run.trace_id,
        "cancel_requested": run.cancel_requested,
        "error_code": run.error_code,
        "error_message": run.error_message,
        "start_time": _isoformat(run.start_time),
        "end_time": _isoformat(run.end_time),
        "deadline_at": _isoformat(run.deadline_at),
        "waiting_since": _isoformat(wait.waiting_since),
        "waiting_reason": wait.waiting_reason.value if wait.waiting_reason is not None else None,
        "pending_join_count": wait.pending_join_count,
        "pending_submission_count": wait.pending_submission_count,
        "continuation_count": wait.continuation_count,
        "snapshot": snapshot_summary(snapshot),
    }


def run_cancel_view(
    run: RunRecord, *, include_cancel_requested: bool = False
) -> dict[str, Any]:
    view: dict[str, Any] = {
        "run_id": str(run.id),
        "status": display_run_status(run.status, run.cancel_requested),
    }
    if include_cancel_requested:
        view["cancel_requested"] = run.cancel_requested
    return view
