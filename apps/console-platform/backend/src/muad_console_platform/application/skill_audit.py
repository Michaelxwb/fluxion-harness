"""Skill 生命周期审计事件（user-skill-sandbox design §3.5 可观测性）。

业务只描述事件，不拼 SQL、不决定审计表结构：所有行都经 `AuditService` 的
`write_config_audit` 原语写入 `control.config_audit_log`，敏感键在落库前统一剔除。

事件动作固定为 `SKILL_*` / `WORKSPACE_CLEANUP`，与下载/解绑/清理的对外语义一致；
`result` 记录业务结果（APPROVE/REJECT/SUCCEEDED/FAILED/DELETED…），不是 HTTP 状态。
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

SKILL_RESOURCE_TYPE = "SKILL"
WORKSPACE_RESOURCE_TYPE = "WORKSPACE"


class SkillAuditAction(StrEnum):
    CREATE = "SKILL_CREATE"
    SUBMIT = "SKILL_SUBMIT"
    REVIEW = "SKILL_REVIEW"
    ARTIFACT_DOWNLOAD = "SKILL_ARTIFACT_DOWNLOAD"
    UNBIND = "SKILL_UNBIND"
    DELETE = "SKILL_DELETE"
    EXECUTION = "SKILL_EXECUTION"
    WORKSPACE_CLEANUP = "WORKSPACE_CLEANUP"


@dataclass(frozen=True)
class SkillAuditEvent:
    tenant_id: str
    actor_id: uuid.UUID
    action: SkillAuditAction
    skill_id: uuid.UUID
    artifact_id: uuid.UUID | None = None
    agent_id: uuid.UUID | None = None
    run_id: str | None = None
    result: str = "SUCCESS"
    detail: Mapping[str, Any] = field(default_factory=dict)
    source_ip: str | None = None


@dataclass(frozen=True)
class WorkspaceCleanupAuditEvent:
    tenant_id: str
    actor_id: uuid.UUID
    workspace_id: uuid.UUID
    result: str
    detail: Mapping[str, Any] = field(default_factory=dict)
    source_ip: str | None = None


def _optional_uuid(value: uuid.UUID | None) -> str | None:
    return None if value is None else str(value)


def skill_event_summary(event: SkillAuditEvent) -> dict[str, Any]:
    """固定字段 + 受控 detail；调用方不能借 detail 覆盖关联标识。"""
    summary: dict[str, Any] = dict(event.detail)
    summary.update(
        {
            "skill_id": str(event.skill_id),
            "artifact_id": _optional_uuid(event.artifact_id),
            "agent_id": _optional_uuid(event.agent_id),
            "run_id": event.run_id,
            "result": event.result,
        }
    )
    return summary


def workspace_cleanup_summary(event: WorkspaceCleanupAuditEvent) -> dict[str, Any]:
    summary: dict[str, Any] = dict(event.detail)
    summary.update(
        {
            "workspace_id": str(event.workspace_id),
            "result": event.result,
        }
    )
    return summary


__all__ = [
    "SKILL_RESOURCE_TYPE",
    "WORKSPACE_RESOURCE_TYPE",
    "SkillAuditAction",
    "SkillAuditEvent",
    "WorkspaceCleanupAuditEvent",
    "skill_event_summary",
    "workspace_cleanup_summary",
]
