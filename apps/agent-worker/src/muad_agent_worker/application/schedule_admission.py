"""每次 Fire 的 Admission：解析依赖 → 冻结 Snapshot v4（design §2.3.4 / §3.5.5 / §3.5.7）。

三条硬约束：

1. **每次 Fire 重新解析**当前依赖，不沿用创建时的定义；失败分类为永久/临时，永久失败
   写显式 `FAILED_ADMISSION` 与 Schedule 的 attention/ended 结果，**不建**可执行 Execution；
2. 快照是**依赖范围内**的完整非密钥 JSON：靠它本身就能恢复执行（不重新解析当前配置），
   创建后不可变；大小超过 `ScheduleSettings.max_snapshot_bytes` 明确拒绝，不截断；
3. 记忆只读：准入读一次、冻结实际注入块；读取失败降级为空块 + `degraded`，不阻断任务。

本模块是纯组装与校验：网络 IO 只在 `admit()` 里发生（调用方保证在 Schedule 行锁外），
落库函数只操作调用方传入的 Session，不自己开事务。
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from muad_agent_core.memory.memory_render import render_memory
from muad_agent_core.memory.selection import (
    MemoryRecord,
    QueryContext,
    QueryScopeState,
    select_memory,
)
from muad_api import AppError
from muad_contracts import (
    SCHEDULE_SNAPSHOT_SCHEMA_VERSION,
    TASK_PROMPT_TEMPLATE_VERSION,
    AgenticExecutionSpec,
    ModeExecutionSpec,
    ReminderExecutionSpec,
    ScheduleAdmissionMaterials,
    ScheduleAdmissionRequest,
    ScheduleMode,
    ScheduleSnapshotInvalid,
    ScheduleSnapshotTooLarge,
    SkillExecutionSpec,
    SnapshotAgent,
    SnapshotBudget,
    SnapshotGrants,
    SnapshotMcpServer,
    SnapshotMemory,
    SnapshotModel,
    SnapshotSkill,
    SnapshotTool,
    ToolAllowlistEntry,
    build_schedule_snapshot,
    canonical_json,
    frozen_retry_safety,
    schedule_snapshot_hash,
)
from muad_contracts.memory import ScopeState
from muad_contracts.platform_settings import PlatformSettings
from pydantic import TypeAdapter, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.models.task import ScheduleFire, TaskSchedule, TaskScheduleRevision
from ..scheduler.client import ScheduleMaterialsError, ScheduleMaterialsProtocol
from ..scheduler.templates import validate_input_template

logger = logging.getLogger(__name__)

#: 永久错误：同一依赖不修复就永远不会再成功。
PERMANENT_ADMISSION_CODES = frozenset(
    {
        "AGENT_MISSING",
        "AGENT_DISABLED",
        "GRANT_REVOKED",
        "SKILL_MISSING",
        "SKILL_DISABLED",
        "ARTIFACT_MISSING",
        "MODEL_MISSING",
        "MODEL_DISABLED",
        "CREDENTIAL_MISSING",
        "TOOL_MISSING",
        "TOOL_REVOKED",
        "SCHEMA_INCOMPATIBLE",
        "ASYNC_OPERATION_UNAVAILABLE",
        "SNAPSHOT_TOO_LARGE",
    }
)

#: 临时错误：下一次 occurrence 可能自愈。
TRANSIENT_ADMISSION_CODES = frozenset({"RESOLVE_UNAVAILABLE", "DB_TIMEOUT", "NETWORK_TIMEOUT"})

#: 用户修复动作（稳定键，界面文案由 Console 负责）。
REPAIR_ACTIONS: dict[str, str] = {
    "AGENT_MISSING": "UPDATE_SCHEDULE_DEPENDENCY",
    "AGENT_DISABLED": "ENABLE_AGENT",
    "GRANT_REVOKED": "RESTORE_AGENT_GRANT",
    "SKILL_MISSING": "UPDATE_SCHEDULE_DEPENDENCY",
    "SKILL_DISABLED": "UPDATE_SCHEDULE_DEPENDENCY",
    "ARTIFACT_MISSING": "REIMPORT_SKILL",
    "MODEL_MISSING": "CONFIGURE_AGENT_MODEL",
    "MODEL_DISABLED": "CONFIGURE_AGENT_MODEL",
    "CREDENTIAL_MISSING": "RESTORE_MODEL_CREDENTIAL",
    "TOOL_MISSING": "RESTORE_TOOL_GRANT",
    "TOOL_REVOKED": "RESTORE_TOOL_GRANT",
    "SCHEMA_INCOMPATIBLE": "UPDATE_SCHEDULE_DEPENDENCY",
    "ASYNC_OPERATION_UNAVAILABLE": "REIMPORT_SKILL",
    "SNAPSHOT_TOO_LARGE": "REDUCE_SCHEDULE_DEPENDENCIES",
    "SCHEDULE_DELIVERY_UNSUPPORTED": "CHANGE_DELIVERY_INTENT",
    "RESOLVE_UNAVAILABLE": "RETRY_LATER",
}

#: 主动投递（PUSH）能力：只有 IM 渠道能 push；WebChat/MSSWWEB 只在任务中心 pull。
PUSH_CAPABLE_CHANNELS = frozenset({"WECOM"})

#: 记忆 taxonomy 的版本身份（本仓所有写入点都用 1）。
MEMORY_TAXONOMY_VERSION = 1
SNAPSHOT_HASH_PREFIX = "sha256:"
_EXECUTION_SPEC_ADAPTER: TypeAdapter[Any] = TypeAdapter(ModeExecutionSpec)


@dataclass(frozen=True, slots=True)
class AdmissionError:
    code: str
    permanent: bool
    repair: str


@dataclass(frozen=True, slots=True)
class AdmissionOutcome:
    """一次 Admission 的结果：要么有可落库的快照，要么有显式失败原因。"""

    snapshot: dict[str, Any] | None = None
    snapshot_hash: str | None = None
    snapshot_bytes: int = 0
    error: AdmissionError | None = None
    memory_degraded: bool = False

    @property
    def accepted(self) -> bool:
        return self.error is None


@dataclass(frozen=True, slots=True)
class AdmissionRequest:
    """这次要执行什么（来自不可变 Revision，不在准入时读 Schedule 配置）。"""

    tenant_id: str
    agent_id: UUID
    actor_user_id: UUID
    mode: ScheduleMode
    schedule_revision_id: UUID | None = None
    skill_id: UUID | None = None
    tool_allowlist: tuple[ToolAllowlistEntry, ...] = ()
    budget: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)
    deadline_at: datetime | None = None
    #: 调用方解析出的投递事实：`delivery_channel` 来自 `task.delivery_route.channel`。
    delivery_intent: str | None = None
    delivery_channel: str | None = None


def classify_failure(code: str, data: dict[str, Any] | None = None) -> AdmissionError:
    """Console/传输错误 → 永久/临时 + 用户修复动作。

    未登记的新错误码按**临时**处理（下一次 occurrence 再试），避免一次未知错误把用户
    的任务直接判死；连续临时失败由调度侧按阈值升级。
    """
    payload = data or {}
    admission_code = payload.get("admission_code")
    resolved = admission_code if isinstance(admission_code, str) and admission_code else code
    permanent = resolved in PERMANENT_ADMISSION_CODES
    repair = payload.get("repair")
    if not isinstance(repair, str) or not repair:
        repair = REPAIR_ACTIONS.get(resolved, "RETRY_LATER")
    return AdmissionError(code=resolved, permanent=permanent, repair=repair)


def delivery_capability_error(
    delivery_intent: str | None, channel: str | None
) -> AdmissionError | None:
    """投递意图与渠道能力匹配（design §3.5.5）；渠道未解析时不做假设。"""
    if delivery_intent != "PUSH" or channel is None:
        return None
    if channel in PUSH_CAPABLE_CHANNELS:
        return None
    return AdmissionError(
        "SCHEDULE_DELIVERY_UNSUPPORTED", True, REPAIR_ACTIONS["SCHEDULE_DELIVERY_UNSUPPORTED"]
    )


def _canonical_hash(value: Any) -> str:
    return SNAPSHOT_HASH_PREFIX + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


class AdmissionRejected(Exception):
    """Revision 本身不可受理（永久错误，重试无意义）。"""

    def __init__(self, error: AdmissionError) -> None:
        super().__init__(error.code)
        self.error = error


def admission_request_from_revision(
    revision: TaskScheduleRevision,
    *,
    tenant_id: str,
    agent_id: UUID,
    actor_user_id: UUID,
    deadline_at: datetime | None = None,
) -> AdmissionRequest:
    """把不可变 Revision 解析成准入请求；配置结构不合法即 `SCHEMA_INCOMPATIBLE`。"""
    try:
        spec = _EXECUTION_SPEC_ADAPTER.validate_python(revision.execution_spec)
    except ValidationError as exc:
        raise AdmissionRejected(
            AdmissionError("SCHEMA_INCOMPATIBLE", True, REPAIR_ACTIONS["SCHEMA_INCOMPATIBLE"])
        ) from exc
    if spec.mode != revision.mode:
        raise AdmissionRejected(
            AdmissionError("SCHEMA_INCOMPATIBLE", True, REPAIR_ACTIONS["SCHEMA_INCOMPATIBLE"])
        )
    base = {
        "tenant_id": tenant_id,
        "agent_id": agent_id,
        "actor_user_id": actor_user_id,
        "mode": ScheduleMode(revision.mode),
        "schedule_revision_id": revision.id,
        "deadline_at": deadline_at,
        "delivery_intent": revision.delivery_intent,
    }
    if isinstance(spec, ReminderExecutionSpec):
        return AdmissionRequest(**base)
    if isinstance(spec, SkillExecutionSpec):
        try:
            validate_input_template(spec.input_template)
        except AppError as exc:
            raise AdmissionRejected(
                AdmissionError(
                    "SCHEMA_INCOMPATIBLE", True, REPAIR_ACTIONS["SCHEMA_INCOMPATIBLE"]
                )
            ) from exc
        return AdmissionRequest(**base, skill_id=spec.skill_id)
    assert isinstance(spec, AgenticExecutionSpec)
    return AdmissionRequest(
        **base,
        tool_allowlist=tuple(spec.tool_allowlist),
        budget=spec.budget.model_dump(mode="json"),
        context={"goal_hash": _canonical_hash({"goal": spec.goal})},
    )


def allowlist_key(entry: ToolAllowlistEntry) -> str:
    if entry.source == "MCP":
        return f"MCP:{entry.server_key}:{entry.tool_name}"
    return f"SKILL:{entry.skill_id}:{entry.tool_name}"


def build_memory_snapshot(
    materials: ScheduleAdmissionMaterials, settings: PlatformSettings
) -> SnapshotMemory:
    """只读记忆块：按 LIST 语义选偏好并渲染一次，冻结实际注入内容。"""
    memory = materials.memory
    if memory is None or memory.degraded:
        return SnapshotMemory(
            taxonomy_version=memory.taxonomy_version if memory is not None else MEMORY_TAXONOMY_VERSION,
            revision=memory.revision if memory is not None else 0,
            block={},
            degraded=True,
        )
    records = [
        MemoryRecord(
            memory_key=candidate.memory_key,
            memory_kind=candidate.memory_kind,
            predicate=candidate.predicate,
            scope_state=ScopeState(candidate.scope_state),
            scope=dict(candidate.scope),
            content=dict(candidate.content),
            title=candidate.title,
            summary=candidate.summary,
            tags=tuple(candidate.tags),
            cardinality=candidate.cardinality,
            priority=candidate.priority,
            author=candidate.author,
            version=candidate.version,
            updated_at=candidate.updated_at,
            expires_at=candidate.expires_at,
            enabled=candidate.enabled,
        )
        for candidate in memory.candidates
    ]
    # 定时任务没有用户 query ⇒ LIST 口径：只走偏好车道，不做检索打分。
    selected = select_memory(
        records,
        QueryContext(query=None, now=memory.as_of, scope_state=QueryScopeState.ABSENT),
        top_k=settings.memory.max_injected_memories,
        min_score=settings.learning.retrieval_min_score,
        inject_inferred=settings.memory.inject_inferred,
        preference_items=settings.learning.retrieval_preference_items,
    )
    rendered = render_memory(
        (item.record for item in selected),
        max_records=settings.memory.max_injected_memories,
        max_bytes=settings.memory.max_injected_bytes,
    )
    entries = [
        {
            "memory_key": item.record.memory_key,
            "version": item.record.version,
            "memory_kind": item.record.memory_kind,
            "predicate": item.record.predicate,
            "title": item.record.title,
        }
        for item in selected
        if item.record.memory_key in rendered.included_keys
    ]
    return SnapshotMemory(
        taxonomy_version=memory.taxonomy_version,
        revision=memory.revision,
        payload_hash=_canonical_hash([[item["memory_key"], item["version"]] for item in entries]),
        block={"entries": entries, "rendered_text": rendered.text},
        degraded=False,
    )


def _snapshot_skills(materials: ScheduleAdmissionMaterials) -> list[SnapshotSkill]:
    return [
        SnapshotSkill(
            skill_id=skill.skill_id,
            artifact_id=skill.artifact_id,
            version=skill.version,
            checksum=skill.checksum,
            storage_key=skill.storage_key,
            source=str(skill.source),
            execution_mode=str(skill.execution_mode),
            frontmatter=dict(skill.frontmatter),
        )
        for skill in materials.skills
    ]


def _snapshot_tools(materials: ScheduleAdmissionMaterials) -> list[SnapshotTool]:
    return [
        SnapshotTool(
            source=tool.source,
            tool_name=tool.tool_name,
            input_schema=dict(tool.input_schema),
            schema_hash=_canonical_hash(tool.input_schema),
            catalog_revision=tool.catalog_revision,
            retry_safety=tool.retry_safety,
            skill_id=tool.skill_id,
            artifact_id=tool.artifact_id,
            checksum=tool.checksum,
            execution_mode=tool.execution_mode,
        )
        for tool in materials.tools
    ]


def _snapshot_mcp(materials: ScheduleAdmissionMaterials) -> list[SnapshotMcpServer]:
    """MCP 只冻结非密定义：`secret_header` 指出凭据头名，值恢复时按凭据解析。"""
    return [
        SnapshotMcpServer(
            server_id=server.mcp_server_id,
            key=server.key,
            endpoint=server.endpoint,
            headers=dict(server.headers),
            secret_header=server.secret_header,
            catalog_revision=server.catalog_revision or None,
            catalog_hash=server.catalog_hash,
            definitions=list(server.definitions),
        )
        for server in materials.mcp_servers
    ]


def _snapshot_budget(materials: ScheduleAdmissionMaterials, request: AdmissionRequest) -> SnapshotBudget:
    retry_safety = frozen_retry_safety(materials.agent.runtime_config.get("budget", {}))
    return SnapshotBudget(
        max_steps=request.budget.get("max_steps"),
        max_tool_calls=request.budget.get("max_tool_calls"),
        max_tokens=request.budget.get("max_tokens"),
        deadline_at=request.deadline_at,
        retry_safety=str(retry_safety),
    )


def build_snapshot(
    materials: ScheduleAdmissionMaterials, request: AdmissionRequest, settings: PlatformSettings
) -> AdmissionOutcome:
    """组装 Snapshot v4：模式形状、无密钥、字节上限一次校验（纯函数）。"""
    model = materials.model
    policy = dict(materials.policy)
    memory = build_memory_snapshot(materials, settings) if request.mode is ScheduleMode.AGENTIC else None
    try:
        snapshot = build_schedule_snapshot(
            mode=request.mode,
            agent=SnapshotAgent(
                id=materials.agent.id,
                key=materials.agent.key,
                revision=materials.agent.revision,
                instructions=materials.agent.instructions,
                runtime_config=dict(materials.agent.runtime_config),
            ),
            model=SnapshotModel(
                id=model.id,
                revision=model.revision,
                deployment_id=model.deployment_id,
                policy_hash=_canonical_hash(policy),
                model_json={
                    "model_id": model.model_id,
                    "protocol": model.protocol,
                    "params": dict(model.params),
                    **({"base_url": model.base_url} if model.base_url else {}),
                },
                pool_members=list(model.pool_members),
                summary_model=model.summary_model,
            ),
            max_snapshot_bytes=settings.schedule.max_snapshot_bytes,
            policy=policy,
            skills=_snapshot_skills(materials),
            tools=_snapshot_tools(materials),
            mcp=_snapshot_mcp(materials),
            memory=memory,
            budget=_snapshot_budget(materials, request),
            allowlist=[allowlist_key(entry) for entry in request.tool_allowlist],
            prompt_template={
                "version": TASK_PROMPT_TEMPLATE_VERSION,
                "source": "agent.instructions",
            },
            context=dict(request.context),
            grants=SnapshotGrants(
                agent_access=materials.grants.agent_access,
                skill_grants=list(materials.grants.skill_ids),
                mcp_grants=list(materials.grants.mcp_server_ids),
            ),
        )
    except ScheduleSnapshotTooLarge as exc:
        logger.warning("schedule_snapshot_too_large", extra={"reason": str(exc)})
        return AdmissionOutcome(
            error=AdmissionError("SNAPSHOT_TOO_LARGE", True, REPAIR_ACTIONS["SNAPSHOT_TOO_LARGE"])
        )
    except ScheduleSnapshotInvalid as exc:
        logger.warning("schedule_snapshot_invalid", extra={"reason": str(exc)})
        return AdmissionOutcome(
            error=AdmissionError(
                "SCHEMA_INCOMPATIBLE", True, REPAIR_ACTIONS["SCHEMA_INCOMPATIBLE"]
            )
        )
    return AdmissionOutcome(
        snapshot=snapshot,
        snapshot_hash=schedule_snapshot_hash(snapshot),
        snapshot_bytes=int(snapshot["snapshot_bytes"]),
        memory_degraded=bool(memory.degraded) if memory is not None else False,
    )


class ScheduleAdmissionService:
    """每次 Fire 的受理入口：一次网络取数（锁外）+ 纯组装。"""

    def __init__(self, client: ScheduleMaterialsProtocol, *, settings: PlatformSettings) -> None:
        self._client = client
        self._settings = settings

    async def admit(
        self, request: AdmissionRequest, *, retry_transient_once: bool = False
    ) -> AdmissionOutcome:
        """`retry_transient_once` 只给 ONCE 用：同一次 Admission 内一次有界重试。"""
        delivery_error = delivery_capability_error(
            request.delivery_intent, request.delivery_channel
        )
        if delivery_error is not None:
            return AdmissionOutcome(error=delivery_error)
        attempts = 2 if retry_transient_once else 1
        outcome = AdmissionOutcome()
        for _attempt in range(attempts):
            outcome = await self._attempt(request)
            if outcome.accepted or outcome.error is None or outcome.error.permanent:
                return outcome
        return outcome

    async def _attempt(self, request: AdmissionRequest) -> AdmissionOutcome:
        payload = ScheduleAdmissionRequest(
            agent_id=request.agent_id,
            actor_user_id=request.actor_user_id,
            mode=request.mode,
            skill_id=request.skill_id,
            tool_allowlist=list(request.tool_allowlist),
        )
        try:
            materials = await self._client.resolve_schedule_materials(request.tenant_id, payload)
        except ScheduleMaterialsError as exc:
            error = classify_failure(exc.code, exc.data)
            logger.warning(
                "schedule_admission_materials_failed",
                extra={
                    "tenant_id": request.tenant_id,
                    "mode": str(request.mode),
                    "code": error.code,
                    "permanent": error.permanent,
                },
            )
            return AdmissionOutcome(error=error)
        outcome = build_snapshot(materials, request, self._settings)
        if outcome.memory_degraded:
            logger.warning(
                "schedule_memory_degraded",
                extra={"tenant_id": request.tenant_id, "reason": "MEMORY_READ_FAILED"},
            )
        return outcome


def execution_snapshot_columns(outcome: AdmissionOutcome) -> dict[str, Any]:
    """Execution 行的快照列：与 Execution 同一事务写入，之后只读。"""
    if outcome.snapshot is None or outcome.snapshot_hash is None:
        raise ValueError("an accepted admission outcome is required")
    return {
        "execution_snapshot_schema_version": SCHEDULE_SNAPSHOT_SCHEMA_VERSION,
        "execution_snapshot_json": outcome.snapshot,
        "snapshot_hash": outcome.snapshot_hash,
        "snapshot_bytes": outcome.snapshot_bytes,
    }


async def record_admission_failure(
    session: AsyncSession,
    *,
    fire: ScheduleFire,
    schedule: TaskSchedule,
    error: AdmissionError,
    is_once: bool,
    moment: datetime,
) -> None:
    """把失败写成显式台账：Fire `FAILED_ADMISSION`；绝不建"假执行"的 Execution。

    永久失败：ONCE `ENDED(ADMISSION_FAILED)`；CRON `NEEDS_ATTENTION`（有修复动作可恢复）。
    临时失败：只记 Fire，Schedule 保持原状态，由调度侧按连续失败阈值升级。
    """
    fire.status = "FAILED_ADMISSION"
    fire.admission_error_code = error.code
    if not error.permanent:
        return
    if is_once:
        schedule.status = "ENDED"
        schedule.end_reason = "ADMISSION_FAILED"
        schedule.ended_at = moment
        schedule.next_fire_at = None
        schedule.attention_reason = None
        schedule.attention_since = None
        return
    schedule.status = "NEEDS_ATTENTION"
    schedule.attention_reason = error.code[:64]
    schedule.attention_since = moment


__all__ = [
    "AdmissionError",
    "AdmissionOutcome",
    "AdmissionRejected",
    "AdmissionRequest",
    "PERMANENT_ADMISSION_CODES",
    "REPAIR_ACTIONS",
    "ScheduleAdmissionService",
    "TRANSIENT_ADMISSION_CODES",
    "admission_request_from_revision",
    "allowlist_key",
    "build_memory_snapshot",
    "build_snapshot",
    "classify_failure",
    "delivery_capability_error",
    "execution_snapshot_columns",
    "record_admission_failure",
]
