"""Task execution snapshot 的唯一构建口径（设计 §3.3.3）。

Runtime 立即提交与 Worker Schedule 触发共用本构建器：
- 只冻结本次 Task 要执行的那一个 Skill（`skills` 仍是数组，但长度恒为 1），
  执行器按 `skill_artifact_id` 精确匹配，不会误执行 Agent 的其它 Skill；
- 字段白名单构建，模型 `api_key`、MCP endpoint/凭据等不进入快照；
- Skill 的来源、artifact/checksum、workspace 归属与平台键一并冻结（RULE-snapshot-001）。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from pydantic import JsonValue

from .egress_budget import resolve_egress_budget
from .platform_settings import TaskSettings, validate_task_execution_timeout
from .resolve import ResolvedAgent, ResolvedMcpServer, ResolvedModel, ResolvedSkill, WorkspaceRef
from .skill_invocation import validate_skill_argv

#: v3：budget 冻结单次 Skill 执行超时；缺失预算的旧快照不能参与执行。
TASK_SNAPSHOT_SCHEMA_VERSION = 3
TASK_PROMPT_TEMPLATE_VERSION = "v1"
SNAPSHOT_HASH_PREFIX = "sha256:"
REQUIRED_SNAPSHOT_KEYS = (
    "schema_version",
    "agent",
    "model",
    "skills",
    "mcp",
    "prompt_template_version",
    "budget",
)


def _skill_entry(skill: ResolvedSkill, workspace: WorkspaceRef) -> dict[str, Any]:
    platforms = skill.frontmatter.get("platforms", [])
    if not isinstance(platforms, list) or any(not isinstance(k, str) or not k for k in platforms):
        raise ValueError("Skill platforms must be a list of platform keys")
    return {
        "skill_id": str(skill.skill_id),
        "artifact_id": str(skill.artifact_id),
        "key": skill.key,
        "version": skill.version,
        "checksum": skill.checksum,
        "storage_key": skill.storage_key,
        "execution_mode": str(skill.execution_mode),
        "source": str(skill.source),
        "workspace": {
            "tenant_id": workspace.tenant_id,
            "user_id": str(workspace.user_id),
            "agent_id": str(workspace.agent_id),
        },
        "platforms": list(dict.fromkeys(platforms)),
    }


def _mcp_entry(server: ResolvedMcpServer) -> dict[str, Any]:
    return {
        "mcp_server_id": str(server.mcp_server_id),
        "catalog_revision": server.catalog_revision,
        "catalog_hash": server.catalog_hash,
        "tools": [str(tool.get("name")) for tool in server.definitions if tool.get("name")],
    }


def build_task_snapshot(
    *,
    agent: ResolvedAgent,
    model: ResolvedModel,
    skill: ResolvedSkill,
    workspace: WorkspaceRef,
    mcp_servers: Sequence[ResolvedMcpServer] = (),
    argv: tuple[str, ...] = (),
    execution_timeout_sec: int | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": TASK_SNAPSHOT_SCHEMA_VERSION,
        "agent": {"id": str(agent.id), "key": agent.key, "revision": agent.revision},
        "model": {
            "id": str(model.id),
            "revision": model.revision,
            "provider": model.protocol,
            "model": model.model_id,
            "params": dict(model.params),
        },
        "skills": [_skill_entry(skill, workspace)],
        "mcp": [_mcp_entry(server) for server in mcp_servers],
        "prompt_template_version": TASK_PROMPT_TEMPLATE_VERSION,
        "budget": {
            **dict(agent.runtime_config.get("budget") or {}),
            "egress_budget": resolve_egress_budget(
                agent.runtime_config.get("egress_budget", {})
            ).model_dump(mode="json"),
            "execution_timeout_sec": validate_task_execution_timeout(
                execution_timeout_sec
                if execution_timeout_sec is not None else TaskSettings().execution_timeout_sec
            ),
        },
        "argv": list(validate_skill_argv(argv)),
    }


def freeze_task_execution_timeout(
    snapshot: Mapping[str, JsonValue], timeout_sec: int,
) -> dict[str, JsonValue]:
    frozen = deepcopy(dict(snapshot))
    frozen["schema_version"] = TASK_SNAPSHOT_SCHEMA_VERSION
    budget = frozen.get("budget")
    if not isinstance(budget, dict):
        raise ValueError("Task snapshot budget must be an object")
    budget["execution_timeout_sec"] = validate_task_execution_timeout(timeout_sec)
    return frozen


def task_execution_timeout(snapshot: Mapping[str, JsonValue]) -> int:
    budget = snapshot.get("budget")
    if not isinstance(budget, dict):
        raise ValueError("Task snapshot budget must be an object")
    return validate_task_execution_timeout(budget.get("execution_timeout_sec"))


def snapshot_hash(snapshot: dict[str, Any]) -> str:
    canonical = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return SNAPSHOT_HASH_PREFIX + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


__all__ = [
    "REQUIRED_SNAPSHOT_KEYS",
    "TASK_PROMPT_TEMPLATE_VERSION",
    "TASK_SNAPSHOT_SCHEMA_VERSION",
    "build_task_snapshot",
    "freeze_task_execution_timeout",
    "snapshot_hash",
    "task_execution_timeout",
]
