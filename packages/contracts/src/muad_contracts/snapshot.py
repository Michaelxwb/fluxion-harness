"""Task execution snapshot 的唯一构建口径（设计 §3.3.3）。

Runtime 立即提交与 Worker Schedule 触发共用本构建器：
- 只冻结本次 Task 要执行的那一个 Skill（`skills` 仍是数组，但长度恒为 1），
  执行器按 `skill_artifact_id` 精确匹配，不会误执行 Agent 的其它 Skill；
- 字段白名单构建，模型 `api_key`、MCP endpoint/凭据等不进入快照。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import Any

from .resolve import ResolvedAgent, ResolvedMcpServer, ResolvedModel, ResolvedSkill

TASK_SNAPSHOT_SCHEMA_VERSION = 1
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


def _skill_entry(skill: ResolvedSkill) -> dict[str, Any]:
    return {
        "skill_id": str(skill.skill_id),
        "artifact_id": str(skill.artifact_id),
        "key": skill.key,
        "version": skill.version,
        "checksum": skill.checksum,
        "storage_key": skill.storage_key,
        "execution_mode": str(skill.execution_mode),
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
    mcp_servers: Sequence[ResolvedMcpServer] = (),
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
        "skills": [_skill_entry(skill)],
        "mcp": [_mcp_entry(server) for server in mcp_servers],
        "prompt_template_version": TASK_PROMPT_TEMPLATE_VERSION,
        "budget": dict(agent.runtime_config.get("budget") or {}),
    }


def snapshot_hash(snapshot: dict[str, Any]) -> str:
    canonical = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return SNAPSHOT_HASH_PREFIX + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


__all__ = [
    "REQUIRED_SNAPSHOT_KEYS",
    "TASK_PROMPT_TEMPLATE_VERSION",
    "TASK_SNAPSHOT_SCHEMA_VERSION",
    "build_task_snapshot",
    "snapshot_hash",
]
