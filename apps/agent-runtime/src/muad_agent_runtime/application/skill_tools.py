from __future__ import annotations

import json
import mimetypes
import uuid
from collections.abc import Mapping, Sequence
from contextlib import AsyncExitStack
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from muad_agent_core.agent import AgentPolicy
from muad_agent_core.prompt import PromptSkill, bounded_catalog, prompt_skill, render_skill_entry
from muad_agent_core.sandbox import (
    WORKSPACE_MOUNT,
    SandboxError,
    WorkspaceDeleting,
    WorkspaceError,
    WorkspaceLease,
    WorkspaceQuotaExceeded,
    WorkspaceShellRequest,
    WorkspaceShellRunner,
    WorkspaceStore,
    WorkspaceUnavailable,
)
from muad_agent_core.skill import (
    DraftShellError,
    DraftShellRequest,
    DraftShellRunner,
    OutputFile,
    SkillExecutionError,
    SkillExecutionRequest,
    SkillExecutionResult,
    SkillExecutionStatus,
    SkillExecutor,
    SkillInvocationError,
    build_sandboxed_skill_executor,
    validate_skill_invocation,
)
from muad_agent_core.skill.result_profiles import RESULT_INVALID
from muad_agent_core.skill.review_archive import review_target
from muad_agent_core.skill.selection import select_entry_script
from muad_agent_core.tools import (
    ArtifactRef,
    ArtifactRole,
    ExecutionOutcome,
    ToolAssembly,
    ToolCall,
    ToolCapability,
    ToolConcurrency,
    ToolDefinition,
    ToolEffect,
    ToolError,
    ToolRegistry,
    ToolResult,
    shell_outcome,
    skill_result_outcome,
)
from muad_agent_core.tools.outcome import BusinessStatus, FailureLayer
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_artifact_store import NfsArtifactStore, SkillArtifactCache, SkillArtifactCacheError
from muad_common import SharedSettings, process_settings
from muad_contracts import (
    DEFAULT_TOOL_RETRY_SAFETY,
    CompletionMode,
    DeliveryRouteInput,
    ResolvedSkill,
    SkillExecutionMode,
    SkillInvocationContract,
    ToolRetrySafety,
)
from muad_contracts.egress_identity import egress_operation_key
from muad_contracts.enums import OUTBOUND_CHANNELS
from muad_contracts.skill_invocation import (
    MAX_SKILL_ARGV_ITEMS,
    validate_skill_argv,
)
from muad_contracts.skill_results import SkillResultProfile, frozen_skill_result_contract
from muad_logging import get_logger
from muad_platform_sdk.adapter.mss_config import PlatformAuthError
from muad_platform_sdk.egress_attempt_audit import AttemptObserver
from muad_platform_sdk.egress_ipc import InvocationEgressBroker, PlatformBaseURLLoader
from muad_platform_sdk.host_egress_execution import HostEgressExecution
from muad_platform_sdk.shared_redis import MeteringUnavailable, SharedLimitExceeded
from muad_platform_sdk.skill_access import SkillPlatformContext
from muad_skill_sdk.skill_package import (
    SkillPackage,
    SkillPackageError,
    is_script_path,
    locate_package_root,
)
from pydantic import JsonValue, ValidationError
from redis.exceptions import RedisClusterException, RedisError

from ..metrics import (
    SANDBOX_OUTPUT_LIMIT_METRIC,
    SANDBOX_TIMEOUT_METRIC,
    SKILL_EXECUTION_DURATION_SECONDS,
    SKILL_EXECUTION_METRIC,
    SKILL_INVOCATION_GUARD_METRIC,
    SKILL_LOAD_METRIC,
    record_latency,
    record_outcome,
)
from .draft_tools import SkillDraftTools
from .task_client import TaskSubmissionContext

LOAD_SKILL_TOOL = "load_skill"
READ_SKILL_RESOURCE_TOOL = "read_skill_resource"
EXECUTE_SKILL_TOOL = "execute_skill"
RUN_SKILL_SCRIPT_TOOL = "run_skill_script"
SEARCH_SKILLS_TOOL = "search_skills"
EXECUTE_TOOL = "execute"
LOAD_SKILL_DESCRIPTION = "Load a skill's manifest and full SKILL.md instructions by skill key."
READ_SKILL_RESOURCE_TOOL_DESCRIPTION = (
    "Read a file shipped inside a skill package (references/assets) by skill key and path."
)
# 工具描述写清何时用；省略 query 列出全部（2026-11-21：空 query 曾把 Run 首轮判死）。
SEARCH_SKILLS_DESCRIPTION = (
    "Search the available skills by keyword (matches key, name and description); "
    "omit query to list the catalog (bounded). The system prompt lists only the leading part "
    "of the catalog; use this to find any skill that is not listed there. Returns catalog "
    "entries, so a match can be passed straight to load_skill or execute target=skill."
)

MAX_RESOURCE_BYTES = 256 * 1024
SKILL_NOT_EFFECTIVE = "SKILL_NOT_EFFECTIVE"
SKILL_RESOURCE_NOT_FOUND = "SKILL_RESOURCE_NOT_FOUND"
SKILL_SCRIPT_NOT_FOUND = "SKILL_SCRIPT_NOT_FOUND"
SKILL_EXECUTION_FAILED = "SKILL_EXECUTION_FAILED"
BACKGROUND_SUBMIT_FAILED = "BACKGROUND_SUBMIT_FAILED"
_STRING_SCHEMA: Mapping[str, Any] = {"type": "string"}
_OBJECT_SCHEMA: Mapping[str, Any] = {"type": "object"}
_ARGV_SCHEMA: Mapping[str, Any] = {
    "type": "array",
    "items": {"type": "string"},
    "maxItems": MAX_SKILL_ARGV_ITEMS,
    "description": "Script arguments in order, preserving spaces and repeated flags as separate items.",
}
_NUMBER_SCHEMA: Mapping[str, Any] = {"type": "number", "exclusiveMinimum": 0}
_CATALOG_RESOURCE_KEY = "skills:catalog"
logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class WorkspaceShellContext:
    """``workspace_shell`` 的受信身份：只从 Run 上下文取，不接受模型参数。"""

    tenant_id: str
    user_id: str
    agent_id: str
    run_id: str

    def __post_init__(self) -> None:
        for field_name in ("tenant_id", "user_id", "agent_id", "run_id"):
            if not str(getattr(self, field_name)).strip():
                raise ValueError(f"workspace shell {field_name} must not be empty")


@dataclass(frozen=True, slots=True)
class DraftShellContext:
    """``skill_draft_shell`` 的受信身份：tenant/owner 从 Run 上下文取，模型只能选 draft_id。"""

    tenant_id: str
    owner_user_id: str
    agent_id: str
    run_id: str

    def __post_init__(self) -> None:
        for field_name in ("tenant_id", "owner_user_id", "agent_id", "run_id"):
            if not str(getattr(self, field_name)).strip():
                raise ValueError(f"draft shell {field_name} must not be empty")


def _catalog_resource_key(arguments: Mapping[str, Any]) -> str:
    """技能目录是 Run 内不可变快照：所有检索读同一资源键（同键串行）。"""
    return _CATALOG_RESOURCE_KEY


def _skill_resource_key(arguments: Mapping[str, Any]) -> str | None:
    """资源读取按 skill_key 锁定：同技能读取串行（共享包缓存），异技能可重叠。"""
    key = arguments.get("skill_key")
    return f"skill:{key}" if isinstance(key, str) and key else None


class SkillToolError(ToolError):
    def __init__(
        self,
        code: str,
        message: str,
        detail: dict[str, object] | None = None,
        retryable: bool | None = None,
        platform_sessions: dict[str, object] | None = None,
    ) -> None:
        super().__init__(
            code,
            message,
            detail=detail,
            retryable=retryable,
            platform_sessions=platform_sessions,
        )


def build_default_skill_cache(settings: SharedSettings | None = None) -> SkillArtifactCache:
    resolved = settings or process_settings()
    return SkillArtifactCache(
        NfsArtifactStore(resolved.artifact_root),
        resolved.skill_cache_root,
    )


class TaskSubmissionProtocol(Protocol):
    async def submit_task(
        self,
        context: TaskSubmissionContext,
        *,
        skill: ResolvedSkill,
        input_data: Mapping[str, Any],
        call_id: str,
        completion_mode: CompletionMode,
        retry_safety: ToolRetrySafety | None = None,
        argv: tuple[str, ...] = (),
    ) -> dict[str, JsonValue]: ...


class SkillOutputRegistrar(Protocol):
    async def register(self, output: OutputFile) -> str: ...


def default_skill_executor() -> SkillExecutor:
    """装配统一沙箱执行器；bwrap 在首次执行时才探测，服务启动不受宿主机影响。"""
    return build_sandboxed_skill_executor(process_settings())


def build_skill_registry(
    *,
    cache: SkillArtifactCache,
    skills: Sequence[ResolvedSkill],
    policy: AgentPolicy,
    task_client: TaskSubmissionProtocol | None = None,
    task_context: TaskSubmissionContext | None = None,
    platform_context: SkillPlatformContext | None = None,
    executor: SkillExecutor | None = None,
    workspace_dir: Path | None = None,
    execution_id: str | None = None,
    output_registrar: SkillOutputRegistrar | None = None,
    workspace_shell: WorkspaceShellRunner | None = None,
    workspace_shell_context: WorkspaceShellContext | None = None,
    workspace_store: WorkspaceStore | None = None,
    workspace_context: WorkspaceShellContext | None = None,
    draft_shell: DraftShellRunner | None = None,
    draft_shell_context: DraftShellContext | None = None,
    draft_tools: SkillDraftTools | None = None,
    enable_run_skill_script: bool = False,
    egress: HostEgressExecution | None = None,
    trace_observer: AttemptObserver | None = None,
    load_platform_base_urls: PlatformBaseURLLoader | None = None,
) -> ToolRegistry:
    tool_set = SkillToolSet(
        cache=cache,
        skills=skills,
        timeout_sec=policy.deadline_ms / 1000.0,
        task_client=task_client,
        task_context=task_context,
        platform_context=platform_context,
        executor=executor,
        workspace_dir=workspace_dir,
        execution_id=execution_id,
        output_registrar=output_registrar,
        workspace_shell=workspace_shell,
        workspace_shell_context=workspace_shell_context,
        workspace_store=workspace_store,
        workspace_context=workspace_context,
        draft_shell=draft_shell,
        draft_shell_context=draft_shell_context,
        draft_tools=draft_tools,
        enable_run_skill_script=enable_run_skill_script,
        egress=egress,
        trace_observer=trace_observer,
        load_platform_base_urls=load_platform_base_urls,
    )
    return tool_set.registry()


class SkillToolSet:
    def __init__(
        self,
        *,
        cache: SkillArtifactCache,
        skills: Sequence[ResolvedSkill],
        timeout_sec: float,
        executor: SkillExecutor | None = None,
        task_client: TaskSubmissionProtocol | None = None,
        task_context: TaskSubmissionContext | None = None,
        platform_context: SkillPlatformContext | None = None,
        workspace_dir: Path | None = None,
        execution_id: str | None = None,
        output_registrar: SkillOutputRegistrar | None = None,
        workspace_shell: WorkspaceShellRunner | None = None,
        workspace_shell_context: WorkspaceShellContext | None = None,
        workspace_store: WorkspaceStore | None = None,
        workspace_context: WorkspaceShellContext | None = None,
        draft_shell: DraftShellRunner | None = None,
        draft_shell_context: DraftShellContext | None = None,
        draft_tools: SkillDraftTools | None = None,
        enable_run_skill_script: bool = False,
        egress: HostEgressExecution | None = None,
        trace_observer: AttemptObserver | None = None,
        load_platform_base_urls: PlatformBaseURLLoader | None = None,
        async_retry_safety: ToolRetrySafety = DEFAULT_TOOL_RETRY_SAFETY,
    ) -> None:
        self._egress = egress
        self._trace_observer = trace_observer
        self._load_platform_base_urls = load_platform_base_urls
        if platform_context is not None and platform_context.budget_scope is not None:
            raise ValueError("Runtime local budget scopes are unsupported; configure shared egress")
        self._cache = cache
        self._skills = {skill.key: skill for skill in skills}
        self._timeout_sec = timeout_sec
        self._executor = executor or default_skill_executor()
        self._ready_dirs: dict[str, Path] = {}
        self._packages: dict[str, SkillPackage] = {}
        self._task_client = task_client
        self._task_context = task_context
        self._platform_context = platform_context
        self._workspace_dir = workspace_dir
        self._execution_id = execution_id
        self._output_registrar = output_registrar
        self._workspace_shell = workspace_shell
        self._workspace_shell_context = workspace_shell_context
        self._workspace_store = workspace_store
        self._workspace_context = workspace_context
        self._draft_shell = draft_shell
        self._draft_shell_context = draft_shell_context
        self._draft_tools = draft_tools
        #: 高级/调试工具，默认不暴露给模型：Skill 用 execute_skill 单一入口。
        self._enable_run_skill_script = enable_run_skill_script
        # ASYNC Skill 的 Owner 侧重试安全声明（design §3.6）：默认 NON_RETRYABLE——未声明即未知，
        # 未知不得自动重做外部副作用。声明同时落在 EXECUTE_SKILL_TOOL 定义与操作提交上。
        self._async_retry_safety = async_retry_safety

    def registry(self) -> ToolRegistry:
        registry = ToolRegistry()
        # workspace_shell 与技能目录无关：没有任何 Skill 的 Agent 也必须能执行一次性命令。
        if (self._workspace_shell is not None and self._workspace_shell_context is not None) or (
            self._draft_shell is not None and self._draft_shell_context is not None
        ) or self._skills:
            registry.register(self._execute_definition())
        # 草稿 Shell 同样与已生效技能无关：用户可以在还没有任何已批准 Skill 时调试草稿。
        # 草稿工具与已生效技能无关：用户可能在还没有任何已批准 Skill 时创建第一份草稿。
        if self._draft_tools is not None:
            for definition in self._draft_tools.definitions():
                registry.register(definition)
        if not self._skills:
            # 一个生效技能都没有时，这几个工具**全都无从调用**（key 从哪来？），目录段也是空的
            # —— 模型只能猜一个 key，白跑一轮换回 `SKILL_NOT_EFFECTIVE`。能力不存在就不提供工具。
            return registry
        for definition in self._definitions():
            registry.register(definition)
        assembly = ToolAssembly(registry)
        assembly.require(
            ToolCapability.SKILL_CATALOG,
            ToolCapability.SKILL_INSTRUCTIONS,
            ToolCapability.SKILL_RESOURCE,
            ToolCapability.SKILL_EXECUTE,
        )
        if self._workspace_shell is not None:
            assembly.require(ToolCapability.WORKSPACE)
        if self._draft_tools is not None:
            assembly.require(ToolCapability.DRAFT)
        return registry

    async def execute(self, arguments: Mapping[str, Any], *, call: ToolCall) -> ToolResult:
        target = arguments.get("target")
        allowed = {
            "skill": {"target", "skill_key", "input", "argv", "script", "timeout_sec"},
            "draft": {"target", "draft_id", "command", "cwd", "timeout_sec", "reset_data"},
            "workspace": {"target", "command", "cwd", "timeout_sec", "platforms"},
        }
        if not isinstance(target, str) or target not in allowed:
            raise ToolError("COMMON_VALIDATION_ERROR", "target must be skill, draft or workspace")
        unsupported = sorted(set(arguments) - allowed[target])
        if unsupported:
            # 键名必须回给模型：只说"不匹配"会让它挨个换形状重试，永远猜不到是多带了哪个键。
            raise ToolError(
                "COMMON_VALIDATION_ERROR",
                f"parameters do not match execution target: target={target} cannot use "
                f"{', '.join(unsupported)}; accepted: {', '.join(sorted(allowed[target]))}",
            )
        payload = {key: value for key, value in arguments.items() if key != "target"}
        if target == "skill":
            if "script" in payload:
                if not self._enable_run_skill_script:
                    raise ToolError("FORBIDDEN", "skill script debug execution is unavailable")
                return await self.run_skill_script(payload, call=call)
            return await self.execute_skill(payload, call=call)
        if target == "draft":
            return await self.skill_draft_shell(payload, call=call)
        return await self.workspace_shell(payload, call=call)

    def _execute_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=EXECUTE_TOOL,
            description=(
                "Execute a skill, a draft command, or a command in the current user's workspace. "
                "Set target=skill with skill_key, input and optional argv. ASYNC skills are submitted as "
                "background tasks and return an acceptance receipt; their results appear in the task center "
                "and are delivered to the conversation. "
                "Set target=draft with draft_id and command to test the draft copy. "
                "Set target=workspace with command; the execution always provides session-manager and "
                "optional platforms only narrow its allowed scope (fail-closed). "
                "Workspace files are shared across the user's runs for this Agent. Test draft code with "
                "target=draft, never by writing under skill-drafts/ in the workspace. "
                "Draft commands run against a copy, with revision-checked writes; SKILL_DATA_DIR persists "
                "for the draft, SKILL_OUTPUT_DIR is temporary, reset_data clears draft test data. "
                "Skill argv are literal CLI arguments and input is JSON stdin; do not shell-quote argv. "
                "target=skill accepts only skill_key, input, argv, timeout_sec and script; "
                "command, cwd and reset_data belong to target=workspace or target=draft. "
                "For instructions-only or MCP-only skills, load the skill and use its MCP tools directly. "
                "Use session-manager list-platforms when the user asks to list platform sessions; "
                "it checks actual login state and reports per-platform availability. For a Skill, "
                "call session-manager get-state directly; do not run list first. For one platform "
                "use get-state --platform <key>; use refresh --platform <key> only for an explicit "
                "refresh. Read the per-platform result before answering. "
                "Do not output or persist credentials."
            ),
            input_schema=_input_schema({
                "target": {"type": "string", "enum": ["skill", "draft", "workspace"]},
                "skill_key": _STRING_SCHEMA, "input": _OBJECT_SCHEMA, "argv": _ARGV_SCHEMA,
                "script": _STRING_SCHEMA,
                "draft_id": _STRING_SCHEMA, "command": _STRING_SCHEMA, "cwd": _STRING_SCHEMA,
                "timeout_sec": _NUMBER_SCHEMA, "reset_data": {"type": "boolean"},
                "platforms": {"type": "array", "items": _STRING_SCHEMA, "minItems": 1},
            }, ("target",)),
            effect=ToolEffect.EXTERNAL,
            handler=self.execute,
            capabilities=frozenset(
                {
                    ToolCapability.SKILL_EXECUTE,
                    *(
                        {ToolCapability.WORKSPACE}
                        if self._workspace_shell is not None
                        else set()
                    ),
                    *(
                        {ToolCapability.DRAFT}
                        if self._draft_shell is not None
                        else set()
                    ),
                }
            ),
            retry_safety=self._async_retry_safety,
        )

    async def load_skill(self, arguments: Mapping[str, Any], *, call: ToolCall) -> ToolResult:
        skill = self._require_skill(arguments)
        package = await self._package(skill)
        return ToolResult(
            json.dumps(
                {"manifest": _manifest_payload(package), "instructions": package.instructions},
                ensure_ascii=False,
            )
        )

    async def read_skill_resource(
        self, arguments: Mapping[str, Any], *, call: ToolCall
    ) -> ToolResult:
        skill = self._require_skill(arguments)
        relative = _require_string(arguments, "path")
        package = await self._package(skill)
        target = _resolve_resource(package.root, relative)
        try:
            with target.open("rb") as stream:
                data = stream.read(MAX_RESOURCE_BYTES)
        except OSError as exc:
            raise SkillToolError(
                SKILL_RESOURCE_NOT_FOUND, f"resource is not readable: {relative}"
            ) from exc
        content = data.decode("utf-8", errors="replace")
        return ToolResult(json.dumps({"path": relative, "content": content}, ensure_ascii=False))

    async def execute_skill(
        self, arguments: Mapping[str, Any], *, call: ToolCall
    ) -> ToolResult:
        skill = self._require_skill(arguments)
        input_data = _require_input(arguments)
        argv = _require_argv(arguments)
        _guard_invocation(skill, input_data=input_data, argv=argv)
        if skill.execution_mode is SkillExecutionMode.ASYNC:
            if "timeout_sec" in arguments:
                raise SkillToolError(
                    ErrorCode.COMMON_VALIDATION_ERROR.value,
                    "async Skill does not accept timeout_sec; the Task deadline governs",
                )
            return await self._submit_background(skill, input_data, call.call_id, argv=argv)
        package = await self._package(skill)
        return await self._execute(
            package.root,
            input_data,
            script=None,
            skill=skill,
            argv=argv,
            timeout_sec=self._call_timeout(arguments),
            call_id=call.call_id,
        )

    async def run_skill_script(
        self, arguments: Mapping[str, Any], *, call: ToolCall
    ) -> ToolResult:
        skill = self._require_skill(arguments)
        name = _require_string(arguments, "script")
        input_data = _require_input(arguments)
        argv = _require_argv(arguments)
        package = await self._package(skill)
        script = _select_script(package, name)
        try:
            default_entrypoint = select_entry_script(package.root)
        except SkillExecutionError:
            default_entrypoint = None
        if (
            default_entrypoint is not None
            and script.resolve() == default_entrypoint.resolve()
        ):
            _guard_invocation(skill, input_data=input_data, argv=argv)
        if skill.execution_mode is SkillExecutionMode.ASYNC:
            _reject_async_entrypoint_bypass(package, script)
        return await self._execute(
            package.root,
            input_data,
            script=script,
            skill=skill,
            argv=argv,
            timeout_sec=self._call_timeout(arguments),
            call_id=call.call_id,
        )

    async def search_skills(
        self, arguments: Mapping[str, Any], *, call: ToolCall
    ) -> ToolResult:
        """按关键词检索**本次 Run 的全部生效技能**，返回目录条目。

        存在的理由：目录有上限（`MAX_CATALOG_BYTES` / `MAX_CATALOG_ENTRIES`），装不下的技能模型
        看不见 key 就等于不存在。数据本来就在手上（`self._skills` 是全量），所以这里只是一次
        本地过滤——不查库、不新增端点。

        省略/空 `query` 等于列出全部（空词表的 AND 命中所有技能），同样受 `bounded_catalog`
        收口。用户问「我有哪些技能」时模型没有任何关键词可用，拒绝空查询只会把一次正常提问变成
        一次失败；非字符串查询则按可纠正的校验错误**返回**（不抛异常，避免被当成确定性失败）。
        """
        query = arguments.get("query")
        if query is None:
            query = ""
        if not isinstance(query, str):
            raise ToolError(
                ErrorCode.COMMON_VALIDATION_ERROR.value,
                "query must be a string",
                retryable=True,
            )
        terms = [term for term in query.casefold().split() if term]
        matches = tuple(skill for skill in self._catalog() if _matches(skill, terms))
        kept, dropped = bounded_catalog(matches)
        result: dict[str, Any] = {
            "total_matches": len(matches),
            "skills": [render_skill_entry(skill) for skill in kept],
        }
        if dropped:
            # 结果同样有上限：否则技能一多，"检索"只是把上下文膨胀从提示词搬进工具结果。
            result["truncated"] = True
            result["hint"] = "only the leading matches are shown; narrow the query"
        return ToolResult(json.dumps(result, ensure_ascii=False))

    async def workspace_shell(
        self, arguments: Mapping[str, Any], *, call: ToolCall
    ) -> ToolResult:
        """执行一次性 Shell；身份取自 Run 上下文，模型不能选择 tenant/user/agent。"""
        if "platforms" in arguments:
            return await self._workspace_shell_with_platforms(arguments, call_id=call.call_id)
        runner = self._workspace_shell
        context = self._workspace_shell_context
        if runner is None or context is None:
            raise SkillToolError("WORKSPACE_UNAVAILABLE", "workspace shell is not configured")
        try:
            command = _require_string(arguments, "command")
            cwd = arguments.get("cwd", WORKSPACE_MOUNT)
            if not isinstance(cwd, str) or not cwd.strip():
                raise SkillToolError(
                    ErrorCode.COMMON_VALIDATION_ERROR.value, "cwd must be a non-empty string"
                )
            timeout_sec = _optional_timeout(arguments, limit=self._timeout_sec)
            result = await runner.run(
                WorkspaceShellRequest(
                    tenant_id=context.tenant_id,
                    user_id=context.user_id,
                    agent_id=context.agent_id,
                    execution_id=_workspace_execution_id(context.run_id, call.call_id),
                    command=command,
                    cwd=cwd.strip(),
                    timeout_sec=timeout_sec,
                    run_id=context.run_id,
                    platform_context=self._platform_context,
                )
            )
        except (WorkspaceError, SandboxError) as exc:
            raise ToolError(exc.code, exc.message) from exc
        except ValueError as exc:
            raise ToolError(ErrorCode.COMMON_VALIDATION_ERROR.value, str(exc)) from exc
        return _workspace_result(result)

    async def _workspace_shell_with_platforms(
        self, arguments: Mapping[str, Any], *, call_id: str
    ) -> ToolResult:
        """Run a command in the same workspace with explicitly selected platform sessions."""
        runner = self._workspace_shell
        context = self._workspace_shell_context
        platform = self._platform_context
        if runner is None or context is None or platform is None:
            raise ToolError("WORKSPACE_UNAVAILABLE", "platform workspace is not configured")
        try:
            keys = arguments.get("platforms")
            if not isinstance(keys, list) or not keys or any(
                not isinstance(key, str) or not key.strip() for key in keys
            ):
                raise SkillToolError(
                    ErrorCode.COMMON_VALIDATION_ERROR.value, "platforms must be a non-empty list"
                )
            selected = tuple(dict.fromkeys(key.strip() for key in keys))
            platform = replace(platform, platform_keys=selected, authorize_selection=True)
            timeout_sec = _optional_timeout(arguments, limit=self._timeout_sec)
            platform, broker = await self._workspace_platform_scope(platform, call_id, timeout_sec)
            cwd = arguments.get("cwd", WORKSPACE_MOUNT)
            if not isinstance(cwd, str) or not cwd.strip():
                raise SkillToolError(
                    ErrorCode.COMMON_VALIDATION_ERROR.value, "cwd must be a non-empty string"
                )
            result = await runner.run(WorkspaceShellRequest(
                tenant_id=context.tenant_id, user_id=context.user_id, agent_id=context.agent_id,
                execution_id=_workspace_execution_id(context.run_id, call_id), run_id=context.run_id,
                command=_require_string(arguments, "command"), cwd=cwd.strip(),
                timeout_sec=timeout_sec,
                platform_context=platform, egress_broker=broker,
            ))
        except (WorkspaceError, SandboxError, SkillExecutionError) as exc:
            raise ToolError(exc.code, exc.message) from exc
        except PlatformAuthError as exc:
            failed = _failed_platform_keys(exc.platform_sessions)
            message = "platform session unavailable"
            if failed:
                message = f"{message}: {', '.join(failed)}"
            raise ToolError(
                exc.code,
                message,
                platform_sessions=exc.platform_sessions,
            ) from exc
        except SkillToolError:
            raise
        except (RedisError, RedisClusterException) as exc:
            raise ToolError(
                "PLATFORM_SESSION_STORE_UNAVAILABLE", "platform session store unavailable"
            ) from exc
        except MeteringUnavailable as exc:
            raise ToolError("EGRESS_METERING_UNAVAILABLE", "shared metering unavailable") from exc
        except SharedLimitExceeded as exc:
            code = "LOCAL_BUDGET_EXHAUSTED" if exc.kind == "TOTAL" else "LOCAL_RATE_LIMITED"
            raise ToolError(code, str(exc)) from exc
        except ValueError as exc:
            raise ToolError(ErrorCode.COMMON_VALIDATION_ERROR.value, str(exc)) from exc
        return _workspace_result(result)

    async def _workspace_platform_scope(
        self, platform: SkillPlatformContext, call_id: str, timeout_sec: float,
    ) -> tuple[SkillPlatformContext, InvocationEgressBroker | None]:
        host = self._egress
        if host is None:
            return platform, self._trace_broker(timeout_sec)
        invocation = await host.host_invocation(
            operation_key=egress_operation_key("TOOL_CALL", call_id), skill_key="workspace_shell",
        )
        broker = host.broker(
            invocation, deadline_seconds=timeout_sec, load_platform_base_urls=self._load_platform_base_urls,
        )
        return replace(platform, transport_factory=lambda verify: host.transport(
            invocation, verify=verify, on_limit=broker.note_limit,
        )), broker

    def _trace_broker(self, timeout_sec: float) -> InvocationEgressBroker | None:
        if self._trace_observer is None:
            return None
        return InvocationEgressBroker.for_tracing(
            observe=self._trace_observer, deadline_seconds=timeout_sec,
            cleanup_seconds=process_settings().egress_cleanup_sec,
            load_platform_base_urls=self._load_platform_base_urls,
        )

    async def skill_draft_shell(
        self, arguments: Mapping[str, Any], *, call: ToolCall
    ) -> ToolResult:
        """在当前用户当前草稿的副本中执行一条命令；身份取自 Run 上下文。"""
        runner = self._draft_shell
        context = self._draft_shell_context
        if runner is None or context is None:
            raise ToolError("COMMON_INTERNAL_ERROR", "draft shell is not configured")
        if "skill_key" in arguments:
            raise ToolError(
                ErrorCode.COMMON_VALIDATION_ERROR.value,
                "skill_key is not accepted by skill_draft_shell; pass draft_id",
            )
        try:
            result = await runner.run(
                _draft_shell_request(arguments, context, call.call_id, self._timeout_sec)
            )
        except DraftShellError as exc:
            error_payload: dict[str, Any] = {"error": {"code": exc.code, "message": exc.message}}
            if exc.retryable:
                error_payload["error"]["retryable"] = True
            if exc.result is not None:
                error_payload["stdout"] = exc.result.stdout
                error_payload["stderr"] = exc.result.stderr
                error_payload["exit_code"] = exc.result.exit_code
                error_payload["output_truncated"] = exc.result.output_truncated
            if exc.platform_sessions:
                error_payload["platform_sessions"] = exc.platform_sessions
            return ToolResult(
                content=json.dumps(error_payload, ensure_ascii=False),
                outcome=exc.outcome(),
            )
        except SkillToolError:
            raise
        except ValueError as exc:
            raise ToolError(ErrorCode.COMMON_VALIDATION_ERROR.value, str(exc)) from exc
        payload: dict[str, Any] = {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.exit_code,
            "duration_ms": result.duration_ms,
            "changed_files": list(result.changed_files),
            "revision": result.revision,
            "output_truncated": result.output_truncated,
        }
        if result.platform_sessions:
            payload["platform_sessions"] = result.platform_sessions
        return ToolResult(
            content=json.dumps(payload, ensure_ascii=False),
            outcome=shell_outcome(
                stdout=result.stdout,
                stderr=result.stderr,
                exit_code=result.exit_code,
                output_truncated=result.output_truncated,
            ),
        )

    def _catalog(self) -> tuple[PromptSkill, ...]:
        """全部生效技能 → 目录条目，顺序与提示词目录一致（`sort_order, key`）。"""
        return tuple(prompt_skill(skill) for skill in self._skills.values())

    def _definitions(self) -> tuple[ToolDefinition, ...]:
        return (
            ToolDefinition(
                name=SEARCH_SKILLS_TOOL,
                description=SEARCH_SKILLS_DESCRIPTION,
                input_schema=_input_schema({"query": _STRING_SCHEMA}, ()),
                effect=ToolEffect.READ,
                handler=self.search_skills,
                capabilities=frozenset({ToolCapability.SKILL_CATALOG}),
                # 内容投递：返回的目录条目就是要给模型读的，不得被大结果外置换成预览
                externalizable_result=False,
                # 只读过滤：目录来自不可变的 Run 内技能快照，同资源键的检索之间串行（同资源锁）。
                concurrency=ToolConcurrency.PARALLEL_READ,
                resource_key=_catalog_resource_key,
            ),
            ToolDefinition(
                name=LOAD_SKILL_TOOL,
                description=LOAD_SKILL_DESCRIPTION,
                input_schema=_input_schema({"skill_key": _STRING_SCHEMA}, ("skill_key",)),
                effect=ToolEffect.READ,
                handler=self.load_skill,
                capabilities=frozenset({ToolCapability.SKILL_INSTRUCTIONS}),
                # 内容投递：返回的 SKILL.md 正文**就是要给模型读的**，不得被大结果外置截成预览
                externalizable_result=False,
            ),
            ToolDefinition(
                name=READ_SKILL_RESOURCE_TOOL,
                description=READ_SKILL_RESOURCE_TOOL_DESCRIPTION,
                input_schema=_input_schema(
                    {"skill_key": _STRING_SCHEMA, "path": _STRING_SCHEMA},
                    ("skill_key", "path"),
                ),
                effect=ToolEffect.READ,
                handler=self.read_skill_resource,
                capabilities=frozenset({ToolCapability.SKILL_RESOURCE}),
                # 同上：references 正文同样属于内容投递
                externalizable_result=False,
                # 只读文件读取：按 skill_key 隔离资源锁（会写本工具的包缓存，异技能才可重叠）。
                concurrency=ToolConcurrency.PARALLEL_READ,
                resource_key=_skill_resource_key,
            ),
        )

    def _require_skill(self, arguments: Mapping[str, Any]) -> ResolvedSkill:
        key = arguments.get("skill_key")
        if not isinstance(key, str) or not key.strip():
            raise SkillToolError(
                ErrorCode.COMMON_VALIDATION_ERROR.value,
                "skill_key must be a non-empty string",
            )
        normalized = key.strip()
        skill = self._skills.get(normalized)
        if skill is None:
            raise SkillToolError(
                SKILL_NOT_EFFECTIVE,
                f"skill is not effective for this run: {normalized}",
            )
        return skill

    async def _submit_background(
        self,
        skill: ResolvedSkill,
        input_data: Mapping[str, Any],
        call_id: str,
        *,
        argv: tuple[str, ...] = (),
    ) -> ToolResult:
        """ASYNC Skill 的 ExecutionRouter：固定 DETACH，只创建 Parent Task，不本地执行。"""
        if self._task_client is None or self._task_context is None:
            raise SkillToolError(BACKGROUND_SUBMIT_FAILED, "durable task submitter is unavailable")
        context = self._task_context
        route = await self._default_background_route(context)
        if route is not context.delivery_route:
            context = replace(context, delivery_route=route)
        try:
            submitted = await self._task_client.submit_task(
                context,
                skill=skill,
                input_data=input_data,
                call_id=call_id,
                completion_mode=CompletionMode.DETACH,
                retry_safety=self._async_retry_safety,
                argv=argv,
            )
        except AppError as exc:
            raise SkillToolError(BACKGROUND_SUBMIT_FAILED, str(exc.code)) from exc
        return ToolResult(json.dumps(submitted, ensure_ascii=False))

    async def _default_background_route(
        self, context: TaskSubmissionContext
    ) -> DeliveryRouteInput | None:
        """Background task delivery follows the same default as schedules.

        A direct outbound conversation already carries its route; a web-entry
        background task has no outbound route, so use the user's bound identity.
        Notification is a platform decision, never a user choice.
        """
        current = context.delivery_route
        if current is not None and current.channel in OUTBOUND_CHANNELS:
            return current
        resolve = getattr(self._task_client, "list_delivery_targets", None)
        if resolve is None:
            return current
        try:
            data = await resolve(
                tenant_id=context.tenant_id,
                actor_user_id=context.actor_user_id,
                agent_id=context.agent.id,
                trace_id=context.trace_id,
            )
        except AppError:
            return current
        items = data.get("items") if isinstance(data, Mapping) else None
        if not isinstance(items, list):
            return current
        for item in items:
            if not isinstance(item, Mapping):
                continue
            route = item.get("route")
            if not isinstance(route, Mapping):
                continue
            try:
                parsed = DeliveryRouteInput.model_validate(route)
            except ValidationError:
                continue
            if parsed.channel in OUTBOUND_CHANNELS:
                return parsed
        return current

    def _call_timeout(self, arguments: Mapping[str, Any]) -> float:
        """一次 Skill 调用的超时：缺省用 Run 的策略预算；给了就取较小值（只能收紧，不能放宽）。"""
        try:
            return _optional_timeout(arguments, limit=self._timeout_sec)
        except ValueError as exc:
            raise SkillToolError(ErrorCode.COMMON_VALIDATION_ERROR.value, str(exc)) from exc

    async def _ready_dir(self, skill: ResolvedSkill) -> Path:
        cached = self._ready_dirs.get(skill.key)
        if cached is not None:
            return cached
        try:
            ready_dir = await self._cache.ensure(
                artifact_id=str(skill.artifact_id),
                storage_key=skill.storage_key,
                checksum=skill.checksum,
            )
        except SkillArtifactCacheError as exc:
            record_outcome(SKILL_LOAD_METRIC, exc.code, {"skill": skill.key})
            raise SkillToolError(exc.code, str(exc)) from exc
        record_outcome(SKILL_LOAD_METRIC, "OK", {"skill": skill.key})
        self._ready_dirs[skill.key] = ready_dir
        return ready_dir

    async def _package(self, skill: ResolvedSkill) -> SkillPackage:
        cached = self._packages.get(skill.key)
        if cached is not None:
            return cached
        ready_dir = await self._ready_dir(skill)
        try:
            # 缓存目录是 zip **原样解包**的产物，可能带一层包装目录（zip 一个文件夹打的包，
            # 也是 macOS Finder 压缩的形状）。定位规则必须与导入校验侧共用同一实现，否则
            # 会出现「导入通过、运行时 missing SKILL.md」（2026-10-01 实测事故）。
            package_root, _ = locate_package_root(ready_dir)
            package = SkillPackage.load(package_root)
        except SkillPackageError as exc:
            raise SkillToolError(ErrorCode.SKILL_PACKAGE_INVALID.value, exc.message) from exc
        self._packages[skill.key] = package
        return package

    async def _execute(
        self,
        ready_dir: Path,
        input_data: Mapping[str, Any],
        *,
        script: Path | None,
        skill: ResolvedSkill,
        call_id: str,
        argv: tuple[str, ...] = (),
        timeout_sec: float | None = None,
    ) -> ToolResult:
        try:
            invocation_id = egress_operation_key("TOOL_CALL", call_id)
        except ValueError as exc:
            raise SkillToolError(
                "SKILL_CALL_ID_INVALID", "A persisted tool call identity is required"
            ) from exc
        if skill_platform_keys(skill.frontmatter) and self._platform_context is None:
            raise SkillToolError(
                "PLATFORM_CONTEXT_REQUIRED", "Platform Skill requires a trusted execution context"
            )
        # 一次工具调用 = 一个独立执行 id：同一 Run 内的多次 Skill 调用不得共用
        # outputs/<run_id>，否则后一次会把前一次留下的文件当成自己的输出。
        execution_id = (
            _workspace_execution_id(self._execution_id, call_id) if self._execution_id else uuid.uuid4().hex
        )
        lease = await self._acquire_execution_workspace(execution_id)
        try:
            result = await self._run_executor(
                ready_dir,
                input_data,
                script=script,
                skill=skill,
                argv=argv,
                execution_id=execution_id,
                workspace_dir=lease.workspace_dir if lease is not None else self._workspace_dir,
                invocation_id=invocation_id,
                timeout_sec=timeout_sec,
            )
            self._record_execution(skill, result)
            return _skill_tool_result(result, await self._register_outputs(result))
        finally:
            if lease is not None:
                await self._release_execution_workspace(lease)

    async def _run_executor(
        self,
        ready_dir: Path,
        input_data: Mapping[str, Any],
        *,
        script: Path | None,
        skill: ResolvedSkill,
        execution_id: str,
        workspace_dir: Path | None,
        invocation_id: str,
        argv: tuple[str, ...] = (),
        timeout_sec: float | None = None,
    ) -> SkillExecutionResult:
        try:
            deadline = self._timeout_sec if timeout_sec is None else timeout_sec
            broker, platform_context = await self._execution_egress(skill, invocation_id, deadline)
            request = SkillExecutionRequest(
                ready_dir=ready_dir,
                input=input_data,
                argv=argv,
                timeout_sec=deadline,
                source=str(skill.source),
                workspace_dir=workspace_dir,
                execution_id=execution_id,
                script=script,
                egress_broker=broker,
                platform_context=platform_context,
            )
            return await self._execute_result_contract(request, skill)
        except SkillExecutionError as exc:
            if exc.code in {"LOCAL_BUDGET_EXHAUSTED", RESULT_INVALID}:
                raise SkillToolError(exc.code, exc.message) from exc
            raise SkillToolError(
                SKILL_EXECUTION_FAILED,
                exc.message,
                detail=exc.detail,
                retryable=exc.retryable,
                platform_sessions=exc.platform_sessions,
            ) from exc
        except SharedLimitExceeded as exc:
            code = "LOCAL_BUDGET_EXHAUSTED" if exc.kind == "TOTAL" else "LOCAL_RATE_LIMITED"
            raise SkillToolError(code, str(exc)) from exc
        except MeteringUnavailable:
            raise SkillToolError("EGRESS_METERING_UNAVAILABLE", "shared metering unavailable") from None

    async def _execute_result_contract(
        self, request: SkillExecutionRequest, skill: ResolvedSkill,
    ) -> SkillExecutionResult:
        contract = (
            frozen_skill_result_contract(skill.frontmatter, checksum=skill.checksum)
            if request.script is None else None
        )
        if contract is None:
            return await self._executor.execute(request)
        async with AsyncExitStack() as stack:
            target = None
            if contract.profile is SkillResultProfile.SKILL_REVIEW:
                loader = self._draft_tools.review_archive if self._draft_tools is not None else None
                target = await stack.enter_async_context(
                    review_target(request.argv, loader, settings=process_settings())
                )
            return await self._executor.execute(
                replace(request, result_contract=contract, review_target=target)
            )

    async def _execution_egress(
        self, skill: ResolvedSkill, operation_key: str, timeout_sec: float,
    ) -> tuple[InvocationEgressBroker | None, SkillPlatformContext | None]:
        context, host = self._platform_context, self._egress
        if context is not None:
            context = replace(context, platform_keys=skill_platform_keys(skill.frontmatter))
        if host is None:
            return self._trace_broker(timeout_sec), context
        invocation = await host.host_invocation(operation_key=operation_key, skill_key=skill.key)
        broker = host.broker(
            invocation, deadline_seconds=timeout_sec, load_platform_base_urls=self._load_platform_base_urls,
            capture_window_denials=True,
        )
        if context is not None:
            context = replace(
                context,
                transport_factory=lambda verify: host.transport(
                    invocation,
                    verify=verify,
                    on_limit=broker.note_limit,
                ),
            )
        return broker, context

    async def _acquire_execution_workspace(self, execution_id: str) -> WorkspaceLease | None:
        if self._workspace_store is None or self._workspace_context is None:
            return None
        context = self._workspace_context
        try:
            return await self._workspace_store.acquire(
                tenant_id=context.tenant_id,
                user_id=context.user_id,
                agent_id=context.agent_id,
                execution_id=execution_id,
                run_id=context.run_id,
                lease_sec=max(60.0, self._timeout_sec + 30.0),
            )
        except WorkspaceQuotaExceeded as exc:
            raise SkillToolError(ErrorCode.WORKSPACE_QUOTA_EXCEEDED.value, str(exc)) from exc
        except WorkspaceDeleting as exc:
            raise SkillToolError(ErrorCode.WORKSPACE_DELETING.value, str(exc)) from exc
        except WorkspaceUnavailable as exc:
            raise SkillToolError(ErrorCode.WORKSPACE_UNAVAILABLE.value, str(exc)) from exc

    async def _release_execution_workspace(self, lease: WorkspaceLease) -> None:
        assert self._workspace_store is not None
        try:
            await self._workspace_store.refresh_usage(lease)
        except Exception as exc:  # noqa: BLE001 - 统计失败不得覆盖执行结果
            logger.warning(
                "skill_workspace_usage_refresh_failed execution_id=%s error=%s",
                lease.execution_id,
                type(exc).__name__,
            )
        try:
            await self._workspace_store.release(lease)
        except Exception as exc:  # noqa: BLE001 - 释放失败不得覆盖执行结果
            logger.warning(
                "skill_workspace_release_failed execution_id=%s error=%s",
                lease.execution_id,
                type(exc).__name__,
            )

    def _record_execution(self, skill: ResolvedSkill, result: SkillExecutionResult) -> None:
        labels = {"source": str(skill.source)}
        record_outcome(SKILL_EXECUTION_METRIC, result.status.value, labels)
        record_latency(SKILL_EXECUTION_DURATION_SECONDS, result.duration_ms / 1000.0, labels)
        if result.output_notice:
            record_outcome(SANDBOX_OUTPUT_LIMIT_METRIC, result.output_notice, labels)
        if result.status is SkillExecutionStatus.TIMED_OUT:
            record_outcome(SANDBOX_TIMEOUT_METRIC, result.status.value, labels)
        # 执行日志只带关联字段与封闭状态；trace/request/tenant/user/agent/run 由 logging-kit
        # context 提供，stdout/stderr 正文与平台凭据不得进入日志（design §3.5 可观测性）。
        logger.info(
            "skill_execution",
            extra={
                "skill_key": skill.key,
                "skill_artifact_id": str(skill.artifact_id),
                "source": str(skill.source),
                "status": result.status.value,
                "duration_ms": result.duration_ms,
                "execution_id": self._execution_id,
            },
        )

    async def _register_outputs(self, result: SkillExecutionResult) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for output in result.outputs:
            entry: dict[str, Any] = {
                "relative_path": output.relative_path,
                "size": output.size,
                "checksum": output.checksum,
                "artifact_id": None,
            }
            if self._output_registrar is not None and result.output_notice is None:
                try:
                    entry["artifact_id"] = await self._output_registrar.register(output)
                except Exception as exc:  # noqa: BLE001 - 登记失败必须可见，不能吞掉
                    entry["error"] = str(getattr(exc, "code", type(exc).__name__))
                    logger.warning(
                        "skill_output_registration_failed path=%s error=%s",
                        output.relative_path,
                        entry["error"],
                    )
            entries.append(entry)
        return entries


def _draft_shell_request(
    arguments: Mapping[str, Any], context: DraftShellContext, call_id: str, timeout_limit: float
) -> DraftShellRequest:
    cwd = arguments.get("cwd", ".")
    reset_data = arguments.get("reset_data", False)
    if not isinstance(cwd, str) or not isinstance(reset_data, bool):
        raise SkillToolError(
            ErrorCode.COMMON_VALIDATION_ERROR.value,
            "cwd must be a string and reset_data must be a boolean",
        )
    return DraftShellRequest(
        tenant_id=context.tenant_id,
        owner_user_id=context.owner_user_id,
        agent_id=context.agent_id,
        draft_id=_require_string(arguments, "draft_id"),
        command=_require_string(arguments, "command"),
        cwd=cwd,
        reset_data=reset_data,
        timeout_sec=_optional_timeout(arguments, limit=timeout_limit),
        execution_id=_workspace_execution_id(context.run_id, call_id),
        run_id=context.run_id,
    )


def _input_schema(properties: Mapping[str, Any], required: Sequence[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": dict(properties),
        "required": list(required),
        "additionalProperties": False,
    }


def _require_string(arguments: Mapping[str, Any], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SkillToolError(
            ErrorCode.COMMON_VALIDATION_ERROR.value,
            f"{key} must be a non-empty string",
        )
    return value.strip()


def _require_argv(arguments: Mapping[str, Any]) -> tuple[str, ...]:
    try:
        return validate_skill_argv(arguments.get("argv", ()))
    except ValueError as exc:
        raise SkillToolError(ErrorCode.COMMON_VALIDATION_ERROR.value, str(exc)) from exc


def _require_input(arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    value = arguments.get("input")
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise SkillToolError(
            ErrorCode.COMMON_VALIDATION_ERROR.value,
            "input must be a JSON object",
        )
    return value


def _optional_timeout(arguments: Mapping[str, Any], *, limit: float) -> float:
    value = arguments.get("timeout_sec")
    if value is None:
        return max(limit, 0.001)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError("timeout_sec must be a positive number")
    return min(float(value), max(limit, 0.001))


def _workspace_execution_id(run_id: str, call_id: str) -> str:
    """Stable safe directory from the FULL Run/call identity, never a budget key."""
    identity = json.dumps([str(run_id), str(call_id)], ensure_ascii=True, separators=(",", ":"))
    return "skill-" + uuid.uuid5(uuid.NAMESPACE_URL, identity).hex


def _matches(skill: PromptSkill, terms: Sequence[str]) -> bool:
    """空白切词后**每个词都要命中**（`key`/`name`/`description`/`platform_label`，大小写不敏感）。

    取 AND 不取 OR：中文没有词边界，多词查询本就少见，而 OR 会让「策略 检查」匹配一大片无关
    技能、把结果上限吃满。子串匹配是不引入分词器的唯一可靠做法。
    """
    haystack = " ".join(
        part for part in (skill.key, skill.name, skill.description, skill.platform_label or "") if part
    ).casefold()
    return all(term in haystack for term in terms)


def _resolve_resource(root: Path, relative: str) -> Path:
    if Path(relative).is_absolute():
        raise SkillToolError(
            ErrorCode.COMMON_VALIDATION_ERROR.value,
            "resource path must be relative",
        )
    resolved_root = root.resolve()
    target = (resolved_root / relative).resolve()
    if target == resolved_root or resolved_root not in target.parents:
        raise SkillToolError(
            ErrorCode.COMMON_VALIDATION_ERROR.value,
            "resource path escapes the skill package",
        )
    if not target.is_file():
        raise SkillToolError(SKILL_RESOURCE_NOT_FOUND, f"resource not found: {relative}")
    return target


def _reject_async_entrypoint_bypass(package: SkillPackage, script: Path) -> None:
    """ASYNC Skill 的主入口只能走 ``execute_skill``。

    ``run_skill_script`` 不提交后台 Task，直接用主入口会在 Runtime 内联执行长任务，
    绕开 Worker 的租约/重试/交付链路。辅助脚本仍允许按需 inline 执行。
    """
    try:
        entrypoint = select_entry_script(package.root)
    except SkillExecutionError:
        return
    if script.resolve() == entrypoint.resolve():
        raise SkillToolError(
            ErrorCode.COMMON_VALIDATION_ERROR.value,
            "ASYNC skill entrypoints must be executed via execute_skill; "
            "run_skill_script bypasses background task routing",
        )


def _select_script(package: SkillPackage, name: str) -> Path:
    """解析 ``scripts/foo.py``、``foo.py`` 或包内相对路径 ``phase2/check.py``。

    单段名字保留旧行为：在 ``scripts/`` 顶层按 basename 查找。多段路径按包根解析；
    绝对路径、``..``、反斜杠、NUL 和跳出包根的符号链接一律拒绝。
    """
    text = name.strip()
    if not text or "\x00" in text or "\\" in text:
        raise SkillToolError(
            ErrorCode.COMMON_VALIDATION_ERROR.value,
            f"invalid script name: {name}",
        )
    relative = PurePosixPath(text)
    if relative.is_absolute() or ".." in relative.parts or "." in relative.parts:
        raise SkillToolError(
            ErrorCode.COMMON_VALIDATION_ERROR.value,
            f"invalid script name: {name}",
        )
    root = package.root.resolve()
    if len(relative.parts) == 1:
        for path in package.scripts():
            if path.name == text:
                target = path.resolve()
                if root not in target.parents:
                    raise SkillToolError(
                        ErrorCode.COMMON_VALIDATION_ERROR.value,
                        f"script escapes the skill package: {name}",
                    )
                if not is_script_path(target):
                    raise SkillToolError(
                        ErrorCode.COMMON_VALIDATION_ERROR.value,
                        f"unsupported script extension: {name}",
                    )
                return target
        raise SkillToolError(SKILL_SCRIPT_NOT_FOUND, f"script is not part of the skill: {name}")

    target = (root / relative).resolve()
    if root not in target.parents:
        raise SkillToolError(
            ErrorCode.COMMON_VALIDATION_ERROR.value,
            f"script escapes the skill package: {name}",
        )
    if not target.is_file():
        raise SkillToolError(SKILL_SCRIPT_NOT_FOUND, f"script is not part of the skill: {name}")
    if not is_script_path(target):
        raise SkillToolError(
            ErrorCode.COMMON_VALIDATION_ERROR.value,
            f"unsupported script extension: {name}",
        )
    return target


def _guard_invocation(
    skill: ResolvedSkill,
    *,
    input_data: Mapping[str, Any],
    argv: Sequence[str],
) -> None:
    """Validate the default-entrypoint contract before starting a skill process."""
    frontmatter = skill.frontmatter if isinstance(skill.frontmatter, Mapping) else {}
    raw = frontmatter.get("invocation_contract")
    if raw is None:
        record_outcome(
            SKILL_INVOCATION_GUARD_METRIC, "SKIPPED", {"code": "CONTRACT_MISSING"}
        )
        return
    try:
        contract = SkillInvocationContract.model_validate(raw)
    except ValidationError as exc:
        record_outcome(
            SKILL_INVOCATION_GUARD_METRIC, "FAILED", {"code": "CONTRACT_INVALID"}
        )
        raise SkillToolError(
            "SKILL_INVOCATION_INVALID",
            "Skill invocation contract is invalid",
            {"reason": "CONTRACT_INVALID", "repair": "REIMPORT_SKILL"},
            retryable=False,
        ) from exc
    try:
        validate_skill_invocation(
            contract,
            input_data=input_data,
            argv=argv,
            phase="rendered",
        )
    except SkillInvocationError as exc:
        record_outcome(
            SKILL_INVOCATION_GUARD_METRIC, "FAILED", {"code": exc.reason}
        )
        raise SkillToolError(
            "SKILL_INVOCATION_INVALID",
            f"{exc.field}: {exc.reason}",
            exc.as_detail(),
            retryable=True,
        ) from exc
    record_outcome(SKILL_INVOCATION_GUARD_METRIC, "PASSED", {"code": "OK"})


def _manifest_payload(package: SkillPackage) -> dict[str, Any]:
    manifest = package.manifest
    payload: dict[str, Any] = {
        "name": manifest.name,
        "description": manifest.description,
        "execution": manifest.execution.value,
        "platform_label": manifest.platform_label,
    }
    try:
        entrypoint = select_entry_script(package.root)
    except SkillExecutionError:
        entrypoint = None
    if entrypoint is not None:
        payload["entrypoint"] = entrypoint.relative_to(package.root.resolve()).as_posix()
    if manifest.invocation_contract is not None:
        payload["invocation_contract"] = manifest.invocation_contract.model_dump(
            mode="json", by_alias=True
        )
    return payload


def _skill_tool_result(result: SkillExecutionResult, outputs: Sequence[Mapping[str, Any]]) -> ToolResult:
    if result.platform_window_limited:
        return _window_limited_result(result, outputs)
    return ToolResult(
        content=_execution_payload(result, outputs),
        outcome=skill_result_outcome(
            status=str(result.status),
            stdout=result.stdout,
            stderr=result.stderr,
            result=result.result,
            exit_code=result.exit_code,
            output_truncated=result.output_truncated,
            execution_status=result.execution_status,
            business_conclusion=result.business_conclusion,
            result_error_code=result.result_error_code,
        ),
    )


def _window_limited_result(
    result: SkillExecutionResult, outputs: Sequence[Mapping[str, Any]],
) -> ToolResult:
    artifacts = tuple(
        ArtifactRef(
            artifact_id=str(entry["artifact_id"]),
            media_type=mimetypes.guess_type(str(entry["relative_path"]))[0] or "application/octet-stream",
            filename=PurePosixPath(str(entry["relative_path"])).name,
            checksum=str(entry["checksum"]),
            size=int(entry["size"]),
            role=ArtifactRole.ATTACHMENT,
        )
        for entry in outputs if entry.get("artifact_id")
    )
    return ToolResult(
        content=json.dumps({
            "status": BusinessStatus.RATE_LIMITED,
            "error_code": "LOCAL_RATE_LIMITED",
            "output_artifact_ids": [artifact.artifact_id for artifact in artifacts],
        }),
        outcome=ExecutionOutcome(
            business_status=BusinessStatus.RATE_LIMITED,
            failure_layer=FailureLayer.CONTENT,
            error_code="LOCAL_RATE_LIMITED",
            exit_code=result.exit_code,
        ),
        artifacts=artifacts,
    )


def _execution_payload(result: SkillExecutionResult, outputs: Sequence[Mapping[str, Any]] = ()) -> str:
    payload: dict[str, Any] = {
        "status": result.status.value,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "result": result.result,
        "duration_ms": result.duration_ms,
        "exit_code": result.exit_code,
        "outputs": list(outputs),
        "output_notice": result.output_notice,
        "output_truncated": result.output_truncated,
    }
    if result.execution_status is not None:
        payload["execution_status"] = result.execution_status.value
    if result.business_conclusion is not None:
        payload["business_status"] = result.business_conclusion.value
    if result.result_error_code is not None:
        payload["error_code"] = result.result_error_code
    if result.platform_sessions:
        payload["platform_sessions"] = result.platform_sessions
    return json.dumps(payload, ensure_ascii=False)


def _workspace_result(result: Any) -> ToolResult:
    payload: dict[str, Any] = {
        "stdout": result.stdout,
        "stderr": result.stderr,
        "exit_code": result.exit_code,
        "duration_ms": result.duration_ms,
        "changed_files": list(result.changed_files),
        "output_truncated": result.output_truncated,
    }
    platform_sessions = getattr(result, "platform_sessions", None)
    if platform_sessions:
        payload["platform_sessions"] = platform_sessions
    return ToolResult(
        content=json.dumps(payload, ensure_ascii=False),
        outcome=shell_outcome(
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.exit_code,
            output_truncated=result.output_truncated,
        ),
    )


def _failed_platform_keys(report: Mapping[str, object] | None) -> tuple[str, ...]:
    """Point the model at the platform(s) that actually failed, not the first key."""
    if not isinstance(report, Mapping):
        return ()
    platforms = report.get("platforms")
    if not isinstance(platforms, list):
        return ()
    keys: list[str] = []
    for item in platforms:
        if not isinstance(item, Mapping) or item.get("available") is not False:
            continue
        key = item.get("key")
        if isinstance(key, str) and key:
            keys.append(key)
    return tuple(dict.fromkeys(keys))


def skill_platform_keys(frontmatter: Mapping[str, Any]) -> tuple[str, ...]:
    value = frontmatter.get("platforms", [])
    if not isinstance(value, list) or any(not isinstance(k, str) or not k for k in value):
        raise SkillToolError("SKILL_PLATFORMS_INVALID", "Skill platforms must be a list of platform keys")
    return tuple(dict.fromkeys(value))
