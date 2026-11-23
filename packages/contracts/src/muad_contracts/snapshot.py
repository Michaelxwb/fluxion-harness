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

from pydantic import JsonValue, ValidationError

from .canonical import canonical_json
from .egress_budget import resolve_egress_budget
from .enums import ScheduleMode
from .platform_settings import TaskSettings, validate_task_execution_timeout
from .resolve import ResolvedAgent, ResolvedMcpServer, ResolvedModel, ResolvedSkill, WorkspaceRef
from .scheduling import (
    SCHEDULE_SNAPSHOT_SCHEMA_VERSION,
    SnapshotAgent,
    SnapshotBudget,
    SnapshotGrants,
    SnapshotMcpServer,
    SnapshotMemory,
    SnapshotModel,
    SnapshotSkill,
    SnapshotTool,
    TaskExecutionSnapshotV4,
)
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


# ---------------------------------------------------------------------------
# Snapshot v4（定时任务，design §3.5.7）
#
# v3（上面的 `build_task_snapshot`）只服务立即任务的单 Skill 路径，不在这里扩展排程概念：
# v4 是另一份按模式判别的格式，由本节的构建器唯一产出。
# ---------------------------------------------------------------------------


class ScheduleSnapshotTooLarge(ValueError):
    """快照超过 `ScheduleSettings.max_snapshot_bytes`：拒绝而不是截断。"""

    code = "SNAPSHOT_TOO_LARGE"


class ScheduleSnapshotInvalid(ValueError):
    """快照不是合法的 v4 文档（版本不符 / 结构或密钥校验失败）。"""

    code = "SCHEMA_INCOMPATIBLE"


#: `snapshot_bytes` 是**包含自身**的序列化字节数；自指只影响十进制位数，最多两轮收敛。
_MAX_BYTES_SEAL_ROUNDS = 4


def schedule_snapshot_hash(snapshot: Mapping[str, Any]) -> str:
    """v4 快照的规范哈希：对**实际落库文本**（含 `snapshot_bytes`）取 sha256。"""
    return snapshot_hash(dict(snapshot))


def _seal_snapshot(document: dict[str, Any]) -> dict[str, Any]:
    """把 `snapshot_bytes` 固定成最终文本自身的字节数（不动点迭代）。"""
    for _ in range(_MAX_BYTES_SEAL_ROUNDS):
        size = len(canonical_json(document).encode("utf-8"))
        if document["snapshot_bytes"] == size:
            return document
        document["snapshot_bytes"] = size
    raise ScheduleSnapshotInvalid("snapshot_bytes did not converge")


def build_schedule_snapshot(
    *,
    mode: ScheduleMode,
    agent: SnapshotAgent,
    model: SnapshotModel,
    max_snapshot_bytes: int,
    policy: Mapping[str, Any] | None = None,
    skills: Sequence[SnapshotSkill] = (),
    tools: Sequence[SnapshotTool] = (),
    mcp: Sequence[SnapshotMcpServer] = (),
    memory: SnapshotMemory | None = None,
    budget: SnapshotBudget | None = None,
    allowlist: Sequence[str] = (),
    prompt_template: Mapping[str, Any] | None = None,
    context: Mapping[str, Any] | None = None,
    grants: SnapshotGrants | None = None,
    plan_input: Mapping[str, Any] | None = None,
    argv: Sequence[str] = (),
    window: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """组装并封存 Snapshot v4：模式形状、无密钥、规范字节数一次校验。

    `plan_input`/`argv`/`window` 是 SKILL_PLAN 本次 occurrence 的渲染事实（design §10.6）：
    与依赖材料一起封存，`snapshot_bytes` 与 hash 都覆盖它们。其它模式保持空值。

    纯函数：不查库、不联网、无副作用，静态 Preflight 与 Fire Admission 共用同一口径。
    超限抛 `ScheduleSnapshotTooLarge`（`SNAPSHOT_TOO_LARGE`），不做截断。
    """
    snapshot = _validate_schedule_snapshot(
        mode=mode,
        agent=agent,
        model=model,
        policy=policy,
        skills=skills,
        tools=tools,
        mcp=mcp,
        memory=memory,
        budget=budget,
        allowlist=allowlist,
        prompt_template=prompt_template,
        context=context,
        grants=grants,
        plan_input=plan_input,
        argv=argv,
        window=window,
    )
    document = snapshot.model_dump(mode="json")
    document["snapshot_bytes"] = 0
    sealed = _seal_snapshot(document)
    if sealed["snapshot_bytes"] > max_snapshot_bytes:
        raise ScheduleSnapshotTooLarge(
            f"execution snapshot is {sealed['snapshot_bytes']} bytes, "
            f"limit is {max_snapshot_bytes}"
        )
    return sealed


def _validate_schedule_snapshot(
    *,
    mode: ScheduleMode,
    agent: SnapshotAgent,
    model: SnapshotModel,
    policy: Mapping[str, Any] | None,
    skills: Sequence[SnapshotSkill],
    tools: Sequence[SnapshotTool],
    mcp: Sequence[SnapshotMcpServer],
    memory: SnapshotMemory | None,
    budget: SnapshotBudget | None,
    allowlist: Sequence[str],
    prompt_template: Mapping[str, Any] | None,
    context: Mapping[str, Any] | None,
    grants: SnapshotGrants | None,
    plan_input: Mapping[str, Any] | None,
    argv: Sequence[str],
    window: Mapping[str, Any] | None,
) -> TaskExecutionSnapshotV4:
    """结构/模式/密钥校验的唯一入口：非法一律 `ScheduleSnapshotInvalid`，不外泄裸校验错。"""
    try:
        return TaskExecutionSnapshotV4(
            mode=mode,
            agent=agent,
            model=model,
            policy=dict(policy or {}),
            skills=list(skills),
            tools=list(tools),
            mcp=list(mcp),
            memory=memory,
            budget=(budget or SnapshotBudget()).model_copy(update={
                "egress_budget": resolve_egress_budget(agent.runtime_config.get("egress_budget", {})),
            }),
            allowlist=list(allowlist),
            prompt_template=dict(prompt_template or {}),
            context=dict(context or {}),
            grants=grants or SnapshotGrants(),
            input=dict(plan_input or {}),
            argv=list(argv),
            window=dict(window) if window is not None else None,
        )
    except ValidationError as exc:
        raise ScheduleSnapshotInvalid(str(exc)) from exc


def load_schedule_snapshot(raw: Mapping[str, Any]) -> TaskExecutionSnapshotV4:
    """从**已落库的 JSON 本身**重建 v4 快照：不重新解析任何当前配置。

    版本不是 4 一律拒绝（不猜测字段、不做兼容映射）；结构或密钥校验失败同样拒绝。
    """
    document = dict(raw)
    if document.get("schema_version") != SCHEDULE_SNAPSHOT_SCHEMA_VERSION:
        raise ScheduleSnapshotInvalid(
            f"unsupported schedule snapshot schema_version: {document.get('schema_version')!r}"
        )
    try:
        return TaskExecutionSnapshotV4.model_validate(document)
    except ValueError as exc:
        raise ScheduleSnapshotInvalid(str(exc)) from exc


def schedule_snapshot_bytes(snapshot: Mapping[str, Any]) -> int:
    """按实际序列化文本重新度量（用于校验落库值与文本一致）。"""
    return len(canonical_json(dict(snapshot)).encode("utf-8"))


__all__ = [
    "REQUIRED_SNAPSHOT_KEYS",
    "TASK_PROMPT_TEMPLATE_VERSION",
    "TASK_SNAPSHOT_SCHEMA_VERSION",
    "ScheduleSnapshotInvalid",
    "ScheduleSnapshotTooLarge",
    "build_schedule_snapshot",
    "build_task_snapshot",
    "freeze_task_execution_timeout",
    "load_schedule_snapshot",
    "schedule_snapshot_bytes",
    "schedule_snapshot_hash",
    "snapshot_hash",
    "task_execution_timeout",
]
