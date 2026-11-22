from collections.abc import Mapping
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any, Literal, cast
from uuid import UUID

import sqlalchemy as sa
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts import (
    TASK_PROMPT_TEMPLATE_VERSION,
    AsyncToolPolicy,
    ResolvedAgent,
    ResolveDefinitionRequest,
    ResolveDefinitionResponse,
    ResolvedModel,
    ResolvedSkill,
    ResolveModelRequest,
    ResolveModelResponse,
    ScheduleAdmissionMaterials,
    ScheduleAdmissionRequest,
    ScheduleGrantMaterials,
    ScheduleMemoryCandidate,
    ScheduleMemoryMaterials,
    ScheduleMode,
    ScheduleModelMaterials,
    ScheduleToolMaterials,
    SkillExecutionMode,
    SkillSource,
    ToolAllowlistEntry,
)
from muad_contracts.memory import MEMORY_MAX_RECORDS
from muad_contracts.model_pool import SelectedModelCredential
from muad_contracts.platform_settings import PlatformSettings, compaction_payload
from muad_logging import get_logger
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.models.control import (
    AgentDefinition,
    ModelDefinition,
    Skill,
    SkillArtifact,
)
from ..infrastructure.repositories.agent_access_grant_repository import AgentAccessGrantRepository
from ..infrastructure.repositories.agent_repository import AgentRepository
from ..infrastructure.repositories.model_deployment_repository import ModelDeploymentRepository
from ..infrastructure.repositories.skill_repository import SkillRepository
from .agent_mcp_service import AgentMcpService
from .platform_settings_service import PlatformSettingsService
from .runtime_credentials import selected_credential

_logger = get_logger(__name__)


class ResolveService:
    def __init__(self, session: AsyncSession) -> None:
        self._agents = AgentRepository(session)
        self._grants = AgentAccessGrantRepository(session)
        self._skills = SkillRepository(session)
        self._deployments = ModelDeploymentRepository(session)
        self._mcp = AgentMcpService(session)

    async def resolve_definition(
        self,
        tenant_id: str,
        payload: ResolveDefinitionRequest,
    ) -> ResolveDefinitionResponse:
        resolved, _ = await self.resolve_admission(tenant_id, payload)
        return resolved

    async def resolve_admission(
        self,
        tenant_id: str,
        payload: ResolveDefinitionRequest,
    ) -> tuple[ResolveDefinitionResponse, SelectedModelCredential]:
        """受理材料一次解析：定义 + 首个可执行成员凭据，不重复查 model/credential。"""
        agent = await self._agents.get(tenant_id, payload.agent_id)
        if agent is None:
            raise AppError(ErrorCode.AGENT_NOT_FOUND)
        if not agent.enabled:
            raise AppError(ErrorCode.AGENT_DISABLED)
        granted = await self._grants.has_active_grant(
            tenant_id,
            payload.actor_user_id,
            payload.agent_id,
        )
        if not granted:
            raise AppError(ErrorCode.AGENT_ACCESS_DENIED)
        model = await self._agents.get_model(tenant_id, agent.model_id)
        if model is None:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
        if not model.enabled:
            raise AppError(ErrorCode.MODEL_DISABLED)
        selection = await self._deployments.find_execution_selection(tenant_id, model.id)
        if selection is None:
            raise AppError(ErrorCode.CREDENTIAL_MISSING)
        deployment, credential = selection
        resolved = ResolveDefinitionResponse(
            agent=ResolvedAgent(
                id=agent.id,
                key=agent.key,
                revision=agent.revision,
                instructions=agent.instructions,
                runtime_config=agent.runtime_config,
            ),
            model=ResolvedModel(
                id=model.id,
                revision=model.revision,
                protocol=cast(Literal["OPENAI"], model.protocol),
                model_id=model.model_id,
                base_url=credential.base_url,
                api_key=credential.api_key,
                params=model.params_json,
            ),
            skills=await self._resolved_skills(tenant_id, payload),
            mcp_servers=await self._mcp.effective_mcp_servers(
                tenant_id, payload.agent_id, payload.actor_user_id
            ),
        )
        return resolved, selected_credential(
            tenant_id, model.id, deployment.id, credential
        )

    async def resolve_model(
        self,
        tenant_id: str,
        payload: ResolveModelRequest,
    ) -> ResolveModelResponse:
        """按**既有模型定义的主键**解析单个模型（ADR-06 的 `compaction.summary.model_ref`）。

        与解析 Agent 主模型走**同一套校验与失败码**：同租户、必须 enabled。摘要跑在自己那条
        模型定义上（endpoint / 凭据 / 参数都不同），所以这里绝不"顺手回退主模型"——解析不出来
        就如实报错，由 Runtime 决定这一层跑不跑（RULE-04：压缩不得让 Run 失败）。
        """
        model = await self._agents.get_model(tenant_id, payload.model_id)
        if model is None:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
        if not model.enabled:
            raise AppError(ErrorCode.MODEL_DISABLED)
        return ResolveModelResponse(model=await self._resolved_model(tenant_id, model))

    async def _resolved_model(self, tenant_id: str, model: ModelDefinition) -> ResolvedModel:
        """解析执行入口：连接与密钥只来自当前可用成员凭据。

        逻辑模型本身不再持有 `base_url`/`api_key`；没有可用成员就是"没有执行入口"，
        明确失败（CREDENTIAL_MISSING），不退回任何旧列/环境变量/平台默认。
        """
        selection = await self._deployments.find_execution_selection(tenant_id, model.id)
        if selection is None:
            raise AppError(ErrorCode.CREDENTIAL_MISSING)
        _deployment, credential = selection
        return ResolvedModel(
            id=model.id,
            revision=model.revision,
            protocol=cast(Literal["OPENAI"], model.protocol),
            model_id=model.model_id,
            base_url=credential.base_url,
            api_key=credential.api_key,
            params=model.params_json,
        )

    async def _resolved_skills(
        self,
        tenant_id: str,
        payload: ResolveDefinitionRequest,
    ) -> list[ResolvedSkill]:
        rows = await self._skills.list_effective_for_agent(
            tenant_id,
            payload.agent_id,
            payload.actor_user_id,
        )
        return [
            ResolvedSkill(
                skill_id=skill.id,
                artifact_id=artifact.id,
                key=skill.key,
                name=skill.name,
                description=skill.description,
                version=artifact.version,
                checksum=artifact.checksum,
                storage_key=artifact.storage_key,
                execution_mode=cast(SkillExecutionMode, artifact.execution_mode),
                frontmatter=artifact.frontmatter_json,
                source=SkillSource(skill.source),
            )
            for skill, artifact in rows
        ]


# ---------------------------------------------------------------------------
# 每次 Fire 的受理材料（scheduled-task-v2 §2.3.4 / §3.5.5 / §3.5.7）
#
# 只读：本模块不发 INSERT/UPDATE/DELETE，不执行 Skill、不调用模型、不联网。
# 失败用既有错误码 + `data.admission_code` 给出**精确**原因与用户修复动作；
# 永久/临时的判定归 Worker（同一份错误在不同 Schedule 类型下后果不同）。
# ---------------------------------------------------------------------------

#: 记忆读取失败时的降级码（与 Runtime `MemoryResolution` 同值）。
MEMORY_READ_FAILED = "MEMORY_READ_FAILED"
#: 行上没有 taxonomy 版本时使用的版本身份（本仓所有写入点都用 1）。
MEMORY_TAXONOMY_VERSION = 1
#: v2 事实记忆的 kind；经验/TASK_STATE 走学习链路，不进任务只读块。
V2_MEMORY_KINDS = ("PREFERENCE", "FACT", "NOTE")
RETRY_SAFETY_VALUES = frozenset({"READ_ONLY", "IDEMPOTENT", "NON_RETRYABLE"})
EXECUTION_MODES = frozenset({"SYNC", "ASYNC"})

REPAIR_UPDATE_DEPENDENCY = "UPDATE_SCHEDULE_DEPENDENCY"
REPAIR_RESTORE_AGENT_GRANT = "RESTORE_AGENT_GRANT"
REPAIR_ENABLE_AGENT = "ENABLE_AGENT"
REPAIR_CONFIGURE_MODEL = "CONFIGURE_AGENT_MODEL"
REPAIR_RESTORE_MODEL = "RESTORE_MODEL_CREDENTIAL"
REPAIR_REIMPORT_SKILL = "REIMPORT_SKILL"
REPAIR_RESTORE_TOOL_GRANT = "RESTORE_TOOL_GRANT"

_MEMORY_SQL = sa.text(
    """
    SELECT memory_key, memory_kind, predicate, scope_state, scope_json, content_json,
           title, summary, tags_json, cardinality, priority, author, version,
           update_time, expires_at, enabled
    FROM runtime.user_memory
    WHERE tenant_id = :tenant_id
      AND user_id = :user_id
      AND is_deleted = false
      AND enabled = true
      AND memory_kind = ANY(:kinds)
      AND (expires_at IS NULL OR expires_at > statement_timestamp())
    ORDER BY memory_key
    LIMIT :limit
    """
)
_MEMORY_REVISION_SQL = sa.text(
    """
    SELECT revision FROM runtime.memory_scope
    WHERE tenant_id = :tenant_id AND user_id = :user_id AND is_deleted = false
    """
)


def _admission_error(code: ErrorCode, admission_code: str, repair: str) -> AppError:
    return AppError(code, data={"admission_code": admission_code, "repair": repair})


def _summary_model_ref(runtime_config: dict[str, Any]) -> Any:
    """`compaction.summary.model_ref`（ADR-06）：只有显式引用才解析摘要模型。"""
    compaction = runtime_config.get("compaction")
    summary = compaction.get("summary") if isinstance(compaction, dict) else None
    return summary.get("model_ref") if isinstance(summary, dict) else None


def _task_policy(settings: PlatformSettings) -> dict[str, Any]:
    """Task 快照的执行期策略：冻结压缩/出网/异步/学习与超时（design §3.5.7）。"""
    return {
        "compaction": compaction_payload(settings.compaction),
        "egress": {"enabled": settings.egress.enabled},
        "async_tools": AsyncToolPolicy().model_dump(mode="json"),
        "learning": {"enabled": settings.learning.learning_enabled},
        "task": {"execution_timeout_sec": settings.task.execution_timeout_sec},
    }


def _tool_retry_safety(definition: Mapping[str, Any]) -> str | None:
    value = definition.get("retry_safety")
    return value if isinstance(value, str) and value in RETRY_SAFETY_VALUES else None


class ScheduleResolveService:
    """按模式取齐 Snapshot v4 依赖范围内的完整非密钥材料。

    与交互式 `ResolveService` 分开：后者服务一次 Run 的主模型/凭据解析，这里服务**每次
    Fire** 的依赖集合（模式判别、allowlist 逐个工具、记忆只读块），两边的失败语义不同。
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._agents = AgentRepository(session)
        self._grants = AgentAccessGrantRepository(session)
        self._skills = SkillRepository(session)
        self._deployments = ModelDeploymentRepository(session)
        self._mcp = AgentMcpService(session)

    async def resolve(
        self, tenant_id: str, payload: ScheduleAdmissionRequest
    ) -> ScheduleAdmissionMaterials:
        agent = await self._require_agent(tenant_id, payload.agent_id)
        agent_access = await self._grants.has_active_grant(
            tenant_id, payload.actor_user_id, payload.agent_id
        )
        if payload.mode is not ScheduleMode.REMINDER and not agent_access:
            raise _admission_error(
                ErrorCode.SCHEDULE_GRANT_DENIED, "GRANT_REVOKED", REPAIR_RESTORE_AGENT_GRANT
            )
        settings_snapshot = await PlatformSettingsService(self._session).read_current(tenant_id)
        skills = await self._mode_skills(tenant_id, payload)
        mcp_servers = (
            []
            if payload.mode is ScheduleMode.REMINDER
            else await self._mcp.effective_mcp_servers(
                tenant_id, payload.agent_id, payload.actor_user_id
            )
        )
        return ScheduleAdmissionMaterials(
            mode=payload.mode,
            agent=ResolvedAgent(
                id=agent.id,
                key=agent.key,
                revision=agent.revision,
                instructions=agent.instructions,
                runtime_config=agent.runtime_config,
            ),
            model=await self._model_materials(
                tenant_id,
                agent.model_id,
                mode=payload.mode,
                summary_ref=_summary_model_ref(agent.runtime_config),
            ),
            skills=[self._resolved_skill(skill, artifact) for skill, artifact in skills],
            mcp_servers=mcp_servers,
            tools=self._mode_tools(payload, skills, mcp_servers),
            memory=(
                await self._memory_materials(tenant_id, str(payload.actor_user_id))
                if payload.mode is ScheduleMode.AGENTIC
                else None
            ),
            policy=_task_policy(settings_snapshot.settings),
            prompt_template={
                "version": TASK_PROMPT_TEMPLATE_VERSION,
                "source": "agent.instructions",
            },
            grants=ScheduleGrantMaterials(
                agent_access=agent_access,
                skill_ids=[skill.id for skill, _artifact in skills],
                mcp_server_ids=[server.mcp_server_id for server in mcp_servers],
            ),
            settings_revision=settings_snapshot.revision,
            settings=asdict(settings_snapshot.settings),
        )

    async def _require_agent(self, tenant_id: str, agent_id: UUID) -> AgentDefinition:
        agent = await self._agents.get(tenant_id, agent_id)
        if agent is None:
            raise _admission_error(ErrorCode.AGENT_NOT_FOUND, "AGENT_MISSING", REPAIR_ENABLE_AGENT)
        if not agent.enabled:
            raise _admission_error(ErrorCode.AGENT_DISABLED, "AGENT_DISABLED", REPAIR_ENABLE_AGENT)
        return agent

    async def _model_materials(
        self,
        tenant_id: str,
        model_id: UUID,
        *,
        mode: ScheduleMode,
        summary_ref: Any = None,
    ) -> ScheduleModelMaterials:
        """REMINDER 只要模型**身份**；其余模式必须 enabled 且有可执行成员凭据。"""
        model = await self._session.scalar(
            sa.select(ModelDefinition).where(
                ModelDefinition.id == model_id,
                ModelDefinition.tenant_id == tenant_id,
            )
        )
        if model is None or (mode is not ScheduleMode.REMINDER and model.is_deleted):
            raise _admission_error(
                ErrorCode.COMMON_NOT_FOUND, "MODEL_MISSING", REPAIR_CONFIGURE_MODEL
            )
        members = await self._deployments.list_members(tenant_id, model.id)
        pool = [
            {
                "deployment_id": str(member.id),
                "credential_id": str(member.credential_id),
                "key": member.key,
                "weight": member.weight,
                "revision": member.revision,
            }
            for member in members
        ]
        summary_model = await self._summary_model(tenant_id, summary_ref)
        if mode is ScheduleMode.REMINDER:
            return ScheduleModelMaterials(
                id=model.id,
                revision=model.revision,
                model_id=model.model_id,
                protocol=model.protocol,
                params=dict(model.params_json or {}),
                pool_members=pool,
                summary_model=summary_model,
            )
        if not model.enabled:
            raise _admission_error(ErrorCode.MODEL_DISABLED, "MODEL_DISABLED", REPAIR_CONFIGURE_MODEL)
        selection = await self._deployments.find_execution_selection(tenant_id, model.id)
        if selection is None:
            raise _admission_error(
                ErrorCode.CREDENTIAL_MISSING, "CREDENTIAL_MISSING", REPAIR_RESTORE_MODEL
            )
        deployment, credential = selection
        return ScheduleModelMaterials(
            id=model.id,
            revision=model.revision,
            model_id=model.model_id,
            protocol=model.protocol,
            params=dict(model.params_json or {}),
            base_url=credential.base_url,
            deployment_id=deployment.id,
            pool_members=pool,
            summary_model=summary_model,
        )

    async def _summary_model(self, tenant_id: str, model_ref: Any) -> dict[str, Any] | None:
        """摘要模型只冻结引用身份（ADR-06）；解析不到就如实留空，不阻塞受理。"""
        if not isinstance(model_ref, str) or not model_ref:
            return None
        try:
            summary_id = UUID(model_ref)
        except ValueError:
            return None
        model = await self._session.scalar(
            sa.select(ModelDefinition).where(
                ModelDefinition.id == summary_id,
                ModelDefinition.tenant_id == tenant_id,
            )
        )
        if model is None:
            return None
        return {"id": str(model.id), "revision": model.revision, "model_id": model.model_id}

    async def _mode_skills(
        self, tenant_id: str, payload: ScheduleAdmissionRequest
    ) -> list[tuple[Skill, SkillArtifact]]:
        if payload.mode is ScheduleMode.REMINDER:
            return []
        effective = await self._skills.list_effective_for_agent(
            tenant_id, payload.agent_id, payload.actor_user_id
        )
        if payload.mode is ScheduleMode.SKILL:
            return [await self._require_skill(tenant_id, payload.skill_id, effective)]
        if payload.mode is ScheduleMode.AGENTIC and not payload.tool_allowlist:
            return effective
        referenced: list[Any] = []
        for entry in payload.tool_allowlist:
            if entry.source == "SKILL" and entry.skill_id not in referenced:
                referenced.append(entry.skill_id)
        return [
            await self._require_skill(tenant_id, skill_id, effective) for skill_id in referenced
        ]

    async def _require_skill(
        self, tenant_id: str, skill_id: Any, effective: list[tuple[Skill, SkillArtifact]]
    ) -> tuple[Skill, SkillArtifact]:
        if skill_id is None:
            raise _admission_error(
                ErrorCode.SCHEDULE_DEPENDENCY_MISSING, "SKILL_MISSING", REPAIR_UPDATE_DEPENDENCY
            )
        skill = await self._skills.get(tenant_id, skill_id)
        if skill is None:
            raise _admission_error(
                ErrorCode.SCHEDULE_DEPENDENCY_MISSING, "SKILL_MISSING", REPAIR_UPDATE_DEPENDENCY
            )
        if not skill.enabled:
            raise _admission_error(
                ErrorCode.SCHEDULE_DEPENDENCY_MISSING, "SKILL_DISABLED", REPAIR_UPDATE_DEPENDENCY
            )
        artifact = await self._skills.get_current_artifact(skill)
        if artifact is None:
            raise _admission_error(
                ErrorCode.SCHEDULE_DEPENDENCY_MISSING, "ARTIFACT_MISSING", REPAIR_REIMPORT_SKILL
            )
        if skill.id not in {item.id for item, _artifact in effective}:
            raise _admission_error(
                ErrorCode.SCHEDULE_GRANT_DENIED, "GRANT_REVOKED", REPAIR_UPDATE_DEPENDENCY
            )
        return skill, artifact

    def _mode_tools(
        self,
        payload: ScheduleAdmissionRequest,
        skills: list[tuple[Skill, SkillArtifact]],
        mcp_servers: list[Any],
    ) -> list[ScheduleToolMaterials]:
        if payload.mode is not ScheduleMode.AGENTIC:
            return []
        if payload.mode is ScheduleMode.AGENTIC and not payload.tool_allowlist:
            return self._owner_tools(skills, mcp_servers)
        by_key = {server.key: server for server in mcp_servers}
        by_skill = {skill.id: (skill, artifact) for skill, artifact in skills}
        tools: list[ScheduleToolMaterials] = []
        for entry in payload.tool_allowlist:
            tools.append(self._resolve_tool(entry, by_key, by_skill))
        return tools

    def _owner_tools(
        self,
        skills: list[tuple[Any, Any]],
        mcp_servers: list[Any],
    ) -> list[ScheduleToolMaterials]:
        """`tools_allow` 为空/`*`：冻结 owner 当前生效的全部工具材料。"""
        tools: list[ScheduleToolMaterials] = []
        for server in mcp_servers:
            for definition in server.definitions:
                if not isinstance(definition, Mapping):
                    continue
                name = definition.get("name")
                if not isinstance(name, str) or not name:
                    continue
                schema = definition.get("input_schema") or {}
                if not isinstance(schema, dict):
                    continue
                tools.append(
                    ScheduleToolMaterials(
                        source="MCP",
                        tool_name=name,
                        input_schema=dict(schema),
                        retry_safety=_tool_retry_safety(definition),
                        server_id=server.mcp_server_id,
                        server_key=server.key,
                        catalog_revision=server.catalog_revision or None,
                    )
                )
        for skill, artifact in skills:
            execution_mode = artifact.execution_mode
            if execution_mode not in EXECUTION_MODES:
                raise _admission_error(
                    ErrorCode.ASYNC_OPERATION_UNAVAILABLE,
                    "ASYNC_OPERATION_UNAVAILABLE",
                    REPAIR_REIMPORT_SKILL,
                )
            frontmatter = artifact.frontmatter_json or {}
            retry_safety = frontmatter.get("retry_safety")
            tools.append(
                ScheduleToolMaterials(
                    source="SKILL",
                    tool_name="execute_skill",
                    retry_safety=retry_safety if retry_safety in RETRY_SAFETY_VALUES else None,
                    skill_id=skill.id,
                    artifact_id=artifact.id,
                    checksum=artifact.checksum,
                    execution_mode=execution_mode,
                )
            )
        return tools

    def _resolve_tool(
        self,
        entry: ToolAllowlistEntry,
        by_key: dict[str, Any],
        by_skill: dict[Any, tuple[Skill, SkillArtifact]],
    ) -> ScheduleToolMaterials:
        if entry.source == "MCP":
            server = by_key.get(entry.server_key or "")
            if server is None:
                raise _admission_error(
                    ErrorCode.SCHEDULE_GRANT_DENIED, "TOOL_REVOKED", REPAIR_RESTORE_TOOL_GRANT
                )
            definition = next(
                (
                    item
                    for item in server.definitions
                    if isinstance(item, dict) and item.get("name") == entry.tool_name
                ),
                None,
            )
            if definition is None:
                raise _admission_error(
                    ErrorCode.SCHEDULE_DEPENDENCY_MISSING,
                    "TOOL_MISSING",
                    REPAIR_RESTORE_TOOL_GRANT,
                )
            schema = definition.get("input_schema") or {}
            if not isinstance(schema, dict):
                raise _admission_error(
                    ErrorCode.SCHEDULE_DEPENDENCY_MISSING,
                    "SCHEMA_INCOMPATIBLE",
                    REPAIR_RESTORE_TOOL_GRANT,
                )
            return ScheduleToolMaterials(
                source="MCP",
                tool_name=entry.tool_name,
                input_schema=dict(schema),
                retry_safety=_tool_retry_safety(definition),
                server_id=server.mcp_server_id,
                server_key=server.key,
                catalog_revision=server.catalog_revision or None,
            )
        pair = by_skill.get(entry.skill_id)
        if entry.tool_name == "*":
            raise AppError(
                ErrorCode.SCHEDULE_TOOL_WILDCARD_UNSUPPORTED,
                data={
                    "admission_code": "WILDCARD_NOT_SUPPORTED",
                    "repair": "PROVIDE_TOOL_NAME",
                    "allowed_values": [f"SKILL:{entry.skill_id}:execute"],
                },
            )
        if pair is None:
            raise _admission_error(
                ErrorCode.SCHEDULE_DEPENDENCY_MISSING, "SKILL_MISSING", REPAIR_UPDATE_DEPENDENCY
            )
        skill, artifact = pair
        execution_mode = artifact.execution_mode
        if execution_mode not in EXECUTION_MODES:
            raise _admission_error(
                ErrorCode.ASYNC_OPERATION_UNAVAILABLE,
                "ASYNC_OPERATION_UNAVAILABLE",
                REPAIR_REIMPORT_SKILL,
            )
        frontmatter = artifact.frontmatter_json or {}
        retry_safety = frontmatter.get("retry_safety")
        return ScheduleToolMaterials(
            source="SKILL",
            tool_name=entry.tool_name,
            retry_safety=retry_safety if retry_safety in RETRY_SAFETY_VALUES else None,
            skill_id=skill.id,
            artifact_id=artifact.id,
            checksum=artifact.checksum,
            execution_mode=execution_mode,
        )

    @staticmethod
    def _resolved_skill(skill: Skill, artifact: SkillArtifact) -> ResolvedSkill:
        return ResolvedSkill(
            skill_id=skill.id,
            artifact_id=artifact.id,
            key=skill.key,
            name=skill.name,
            description=skill.description,
            version=artifact.version,
            checksum=artifact.checksum,
            storage_key=artifact.storage_key,
            execution_mode=cast(SkillExecutionMode, artifact.execution_mode),
            frontmatter=artifact.frontmatter_json,
            source=SkillSource(skill.source),
        )

    async def _memory_materials(self, tenant_id: str, user_id: str) -> ScheduleMemoryMaterials:
        """一次有界读取：完整候选集合 + scope revision；失败降级为空块，不阻断受理。"""
        scope = {"tenant_id": tenant_id, "user_id": UUID(user_id)}
        as_of = datetime.now(UTC)
        try:
            rows = (
                await self._session.execute(
                    _MEMORY_SQL,
                    {**scope, "kinds": list(V2_MEMORY_KINDS), "limit": MEMORY_MAX_RECORDS},
                )
            ).mappings().all()
            revision = await self._session.scalar(_MEMORY_REVISION_SQL, scope)
        except Exception:
            # 降级必须可见：任务照常受理（空块 + degraded），但原因要留下。
            _logger.warning(
                "schedule_memory_read_failed",
                extra={"fields": {"tenant_id": tenant_id, "user_id": user_id}},
                exc_info=True,
            )
            return ScheduleMemoryMaterials(
                taxonomy_version=MEMORY_TAXONOMY_VERSION,
                revision=0,
                as_of=as_of,
                candidates=[],
                degraded=True,
                error_code=MEMORY_READ_FAILED,
            )
        return ScheduleMemoryMaterials(
            taxonomy_version=MEMORY_TAXONOMY_VERSION,
            revision=int(revision or 0),
            as_of=as_of,
            candidates=[_memory_candidate(row) for row in rows],
        )


def _memory_candidate(row: Any) -> ScheduleMemoryCandidate:
    return ScheduleMemoryCandidate(
        memory_key=row["memory_key"],
        memory_kind=row["memory_kind"],
        predicate=row["predicate"],
        scope_state=row["scope_state"],
        scope=dict(row["scope_json"] or {}),
        content=dict(row["content_json"] or {}),
        title=row["title"] or "",
        summary=row["summary"] or "",
        tags=list(row["tags_json"] or []),
        cardinality=row["cardinality"],
        priority=row["priority"],
        author=row["author"],
        version=int(row["version"]),
        updated_at=row["update_time"],
        expires_at=row["expires_at"],
        enabled=bool(row["enabled"]),
    )
