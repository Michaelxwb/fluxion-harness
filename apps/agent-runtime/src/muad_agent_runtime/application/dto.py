from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from muad_contracts import RunStatus
from pydantic import BaseModel, ConfigDict

from ..infrastructure.models.runtime import Conversation, RunRecord, RuntimeSnapshot


class ResumeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["text"] = "text"
    text: str = ""


class ResumeRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input: ResumeInput


class CancelActiveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: uuid.UUID
    platform_user_id: uuid.UUID


class CreateConversationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: uuid.UUID
    platform_user_id: uuid.UUID


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
    return {
        "snapshot_id": str(snapshot.id),
        "schema_version": snapshot.schema_version,
        "agent_revision": snapshot.agent_revision,
        "model_revision": snapshot.model_revision,
        "prompt_template_version": snapshot.prompt_template_version,
        "content_hash": snapshot.content_hash,
    }


def run_view(run: RunRecord, snapshot: RuntimeSnapshot | None) -> dict[str, Any]:
    return {
        "run_id": str(run.id),
        "conversation_id": str(run.conversation_id),
        "agent_id": str(run.agent_id),
        "user_id": str(run.user_id),
        "status": display_run_status(run.status, run.cancel_requested),
        "trace_id": run.trace_id,
        "cancel_requested": run.cancel_requested,
        "error_code": run.error_code,
        "start_time": _isoformat(run.start_time),
        "end_time": _isoformat(run.end_time),
        "snapshot": snapshot_summary(snapshot),
    }


def run_cancel_view(run: RunRecord) -> dict[str, Any]:
    return {
        "run_id": str(run.id),
        "status": display_run_status(run.status, run.cancel_requested),
    }
