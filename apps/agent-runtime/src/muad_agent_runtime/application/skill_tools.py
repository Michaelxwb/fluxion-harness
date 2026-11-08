from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Mapping, Sequence
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
    build_sandboxed_skill_executor,
)
from muad_agent_core.skill.selection import select_entry_script
from muad_agent_core.tools import ToolConcurrency, ToolDefinition, ToolEffect, ToolRegistry
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_artifact_store import NfsArtifactStore, SkillArtifactCache, SkillArtifactCacheError
from muad_common import SharedSettings, process_settings
from muad_contracts import (
    DEFAULT_TOOL_RETRY_SAFETY,
    CompletionMode,
    ResolvedSkill,
    SkillExecutionMode,
    ToolRetrySafety,
)
from muad_contracts.skill_invocation import (
    MAX_SKILL_ARGV_ITEMS,
    validate_skill_argv,
)
from muad_platform_sdk.skill_access import SkillPlatformContext
from muad_skill_sdk.skill_package import (
    SkillPackage,
    SkillPackageError,
    is_script_path,
    locate_package_root,
)
from pydantic import JsonValue

from ..metrics import (
    SANDBOX_OUTPUT_LIMIT_METRIC,
    SANDBOX_TIMEOUT_METRIC,
    SKILL_EXECUTION_DURATION_SECONDS,
    SKILL_EXECUTION_METRIC,
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
WORKSPACE_SHELL_TOOL = "workspace_shell"
SKILL_DRAFT_SHELL_TOOL = "skill_draft_shell"
LOAD_SKILL_DESCRIPTION = "Load a skill's manifest and full SKILL.md instructions by skill key."
READ_SKILL_RESOURCE_TOOL_DESCRIPTION = (
    "Read a file shipped inside a skill package (references/assets) by skill key and path."
)
EXECUTE_SKILL_DESCRIPTION = (
    "Preferred way to run a skill's main business action. Executes the declared entry script and "
    "honors the skill's execution mode (SYNC runs inline; ASYNC submits a background Task). "
    "Pass legacy CLI flags from SKILL.md as argv (strings after the script path, without "
    "python/node or shell quoting); input is optional JSON stdin and is not converted to CLI "
    "flags. Do not use run_skill_script as a substitute for this tool."
)
RUN_SKILL_SCRIPT_DESCRIPTION = (
    "Debug-only primitive for one specific script inside a skill package, addressed by "
    "package-relative path. It is not exposed by default: skills must route their own actions "
    "through execute_skill. When enabled (debug), it always runs inline and bypasses "
    "SYNC/ASYNC execution routing. argv is passed as CLI arguments (no shell)."
)
WORKSPACE_SHELL_DESCRIPTION = (
    "Run a one-off shell command inside this user's persistent workspace for the current Agent. "
    "The command runs with the workspace as its working directory: locate files with `ls`/`grep` "
    "inside the workspace instead of scanning the whole filesystem (`find /` can take minutes and "
    "will not find anything the workspace does not already contain). Do not run or write files "
    "under skill-drafts/ — that is the live skill draft managed by the platform; test draft code "
    "with skill_draft_shell instead. "
    "The workspace is shared across runs of the same user + Agent: keep durable files under "
    "$SKILL_DATA_DIR and write per-call results under $SKILL_OUTPUT_DIR. Network is available. "
    "System Python/Node dependencies are preinstalled; temporary pip/npm installs are allowed but "
    "live only inside this workspace and may be lost when the workspace is rebuilt. There is no "
    "session-manager or platform login state in this shell."
)
SKILL_DRAFT_SHELL_DESCRIPTION = (
    "Run one command against the current user's skill draft copy. Use it to validate "
    "SKILL.md/scripts before submitting: python -m py_compile, python scripts/<entry>.py, "
    "node --check, and session-manager get-state are supported. Only files inside the draft are "
    "written back, and only after path/extension/size/symlink validation and a revision CAS; "
    "a revision conflict returns DRAFT_REVISION_CONFLICT. It is not an interactive session and "
    "does not accept skill_key."
)
# 工具描述里写清**何时用**：系统提示里的目录只列了靠前的一部分，其余技能只能从这里找到。
SEARCH_SKILLS_DESCRIPTION = (
    "Search the available skills by keyword (matches key, name and description). "
    "The system prompt lists only the leading part of the skill catalog; use this to find any "
    "skill that is not listed there. Returns catalog entries, so a match can be passed straight "
    "to load_skill or execute_skill."
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
    "type": "array", "items": {"type": "string"}, "maxItems": MAX_SKILL_ARGV_ITEMS,
    "description": "Script arguments in order, preserving spaces and repeated flags as separate items.",
}
_NUMBER_SCHEMA: Mapping[str, Any] = {"type": "number", "exclusiveMinimum": 0}
_CATALOG_RESOURCE_KEY = "skills:catalog"
logger = logging.getLogger(__name__)


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


class SkillToolError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        detail: dict[str, object] | None = None,
        retryable: bool = False,
    ) -> None:
        self.code = code
        self.message = message
        self.detail = detail
        self.retryable = retryable
        super().__init__(message)


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
        async_retry_safety: ToolRetrySafety = DEFAULT_TOOL_RETRY_SAFETY,
    ) -> None:
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
        if self._workspace_shell is not None and self._workspace_shell_context is not None:
            registry.register(self._workspace_shell_definition())
        # 草稿 Shell 同样与已生效技能无关：用户可以在还没有任何已批准 Skill 时调试草稿。
        if self._draft_shell is not None and self._draft_shell_context is not None:
            registry.register(self._draft_shell_definition())
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
        if self._enable_run_skill_script:
            registry.register(self._run_skill_script_definition())
        return registry

    async def load_skill(self, arguments: Mapping[str, Any], *, call_id: str) -> str:
        try:
            skill = self._require_skill(arguments)
            package = await self._package(skill)
            return json.dumps(
                {"manifest": _manifest_payload(package), "instructions": package.instructions},
                ensure_ascii=False,
            )
        except SkillToolError as exc:
            return _error_result(exc.code, exc.message)

    async def read_skill_resource(self, arguments: Mapping[str, Any], *, call_id: str) -> str:
        try:
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
            return json.dumps({"path": relative, "content": content}, ensure_ascii=False)
        except SkillToolError as exc:
            return _error_result(exc.code, exc.message)

    async def execute_skill(self, arguments: Mapping[str, Any], *, call_id: str) -> str:
        try:
            skill = self._require_skill(arguments)
            input_data = _require_input(arguments)
            argv = _require_argv(arguments)
            if skill.execution_mode is SkillExecutionMode.ASYNC:
                mode = CompletionMode(arguments.get("completion_mode", "JOIN"))
                return await self._submit_background(skill, input_data, call_id, mode, argv=argv)
            if "completion_mode" in arguments:
                raise SkillToolError(
                    ErrorCode.TOOL_COMPLETION_MODE_INVALID.value, "SYNC Skill does not accept completion_mode"
                )
            package = await self._package(skill)
            return await self._execute(
                package.root, input_data, script=None, skill=skill, argv=argv, call_id=call_id
            )
        except SkillToolError as exc:
            return _error_result(
                exc.code, exc.message, exc.detail, retryable=exc.retryable
            )

    async def run_skill_script(self, arguments: Mapping[str, Any], *, call_id: str) -> str:
        try:
            skill = self._require_skill(arguments)
            name = _require_string(arguments, "script")
            input_data = _require_input(arguments)
            argv = _require_argv(arguments)
            package = await self._package(skill)
            script = _select_script(package, name)
            if skill.execution_mode is SkillExecutionMode.ASYNC:
                _reject_async_entrypoint_bypass(package, script)
            return await self._execute(
                package.root, input_data, script=script, skill=skill, argv=argv, call_id=call_id
            )
        except SkillToolError as exc:
            return _error_result(
                exc.code, exc.message, exc.detail, retryable=exc.retryable
            )

    async def search_skills(self, arguments: Mapping[str, Any], *, call_id: str) -> str:
        """按关键词检索**本次 Run 的全部生效技能**，返回目录条目。

        存在的理由：目录有上限（`MAX_CATALOG_BYTES` / `MAX_CATALOG_ENTRIES`），装不下的技能模型
        看不见 key 就等于不存在。数据本来就在手上（`self._skills` 是全量），所以这里只是一次
        本地过滤——不查库、不新增端点。
        """
        query = _require_string(arguments, "query")
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
        return json.dumps(result, ensure_ascii=False)

    async def workspace_shell(self, arguments: Mapping[str, Any], *, call_id: str) -> str:
        """执行一次性 Shell；身份取自 Run 上下文，模型不能选择 tenant/user/agent。"""
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
                    execution_id=_workspace_execution_id(context.run_id, call_id),
                    command=command,
                    cwd=cwd.strip(),
                    timeout_sec=timeout_sec,
                    run_id=context.run_id,
                )
            )
        except (WorkspaceError, SandboxError) as exc:
            return _error_result(exc.code, exc.message)
        except SkillToolError as exc:
            return _error_result(exc.code, exc.message)
        except ValueError as exc:
            return _error_result(ErrorCode.COMMON_VALIDATION_ERROR.value, str(exc))
        return json.dumps(
            {
                "stdout": result.stdout,
                "stderr": result.stderr,
                "exit_code": result.exit_code,
                "duration_ms": result.duration_ms,
                "changed_files": list(result.changed_files),
            },
            ensure_ascii=False,
        )

    async def skill_draft_shell(self, arguments: Mapping[str, Any], *, call_id: str) -> str:
        """在当前用户当前草稿的副本中执行一条命令；身份取自 Run 上下文。"""
        runner = self._draft_shell
        context = self._draft_shell_context
        if runner is None or context is None:
            return _error_result("COMMON_INTERNAL_ERROR", "draft shell is not configured")
        if "skill_key" in arguments:
            return _error_result(
                ErrorCode.COMMON_VALIDATION_ERROR.value,
                "skill_key is not accepted by skill_draft_shell; pass draft_id",
            )
        try:
            draft_id = _require_string(arguments, "draft_id")
            command = _require_string(arguments, "command")
            cwd = arguments.get("cwd", ".")
            if not isinstance(cwd, str):
                raise SkillToolError(
                    ErrorCode.COMMON_VALIDATION_ERROR.value, "cwd must be a string"
                )
            timeout_sec = _optional_timeout(arguments, limit=self._timeout_sec)
            result = await runner.run(
                DraftShellRequest(
                    tenant_id=context.tenant_id,
                    owner_user_id=context.owner_user_id,
                    draft_id=draft_id,
                    command=command,
                    cwd=cwd,
                    timeout_sec=timeout_sec,
                    execution_id=_workspace_execution_id(context.run_id, call_id),
                )
            )
        except DraftShellError as exc:
            payload: dict[str, Any] = {"error": {"code": exc.code, "message": exc.message}}
            if exc.result is not None:
                payload["stdout"] = exc.result.stdout
                payload["stderr"] = exc.result.stderr
                payload["exit_code"] = exc.result.exit_code
            return json.dumps(payload, ensure_ascii=False)
        except SkillToolError as exc:
            return _error_result(exc.code, exc.message)
        except ValueError as exc:
            return _error_result(ErrorCode.COMMON_VALIDATION_ERROR.value, str(exc))
        return json.dumps(
            {
                "stdout": result.stdout,
                "stderr": result.stderr,
                "exit_code": result.exit_code,
                "duration_ms": result.duration_ms,
                "changed_files": list(result.changed_files),
                "revision": result.revision,
            },
            ensure_ascii=False,
        )

    def _catalog(self) -> tuple[PromptSkill, ...]:
        """全部生效技能 → 目录条目，顺序与提示词目录一致（`sort_order, key`）。"""
        return tuple(prompt_skill(skill) for skill in self._skills.values())

    def _workspace_shell_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=WORKSPACE_SHELL_TOOL,
            description=WORKSPACE_SHELL_DESCRIPTION,
            input_schema=_input_schema(
                {
                    "command": _STRING_SCHEMA,
                    "cwd": _STRING_SCHEMA,
                    "timeout_sec": _NUMBER_SCHEMA,
                },
                ("command",),
            ),
            effect=ToolEffect.EXTERNAL,
            handler=self.workspace_shell,
        )

    def _draft_shell_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=SKILL_DRAFT_SHELL_TOOL,
            description=SKILL_DRAFT_SHELL_DESCRIPTION,
            input_schema=_input_schema(
                {
                    "draft_id": _STRING_SCHEMA,
                    "command": _STRING_SCHEMA,
                    "cwd": _STRING_SCHEMA,
                    "timeout_sec": _NUMBER_SCHEMA,
                },
                ("draft_id", "command"),
            ),
            effect=ToolEffect.EXTERNAL,
            handler=self.skill_draft_shell,
        )

    def _definitions(self) -> tuple[ToolDefinition, ...]:
        return (
            ToolDefinition(
                name=SEARCH_SKILLS_TOOL,
                description=SEARCH_SKILLS_DESCRIPTION,
                input_schema=_input_schema({"query": _STRING_SCHEMA}, ("query",)),
                effect=ToolEffect.READ,
                handler=self.search_skills,
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
                # 同上：references 正文同样属于内容投递
                externalizable_result=False,
                # 只读文件读取：按 skill_key 隔离资源锁（会写本工具的包缓存，异技能才可重叠）。
                concurrency=ToolConcurrency.PARALLEL_READ,
                resource_key=_skill_resource_key,
            ),
            ToolDefinition(
                name=EXECUTE_SKILL_TOOL,
                description=EXECUTE_SKILL_DESCRIPTION,
                input_schema=_input_schema(
                    {
                        "skill_key": _STRING_SCHEMA,
                        "input": _OBJECT_SCHEMA,
                        "completion_mode": {"type": "string", "enum": ["JOIN", "DETACH"]},
                        "argv": _ARGV_SCHEMA,
                    },
                    ("skill_key",),
                ),
                effect=ToolEffect.EXTERNAL,
                handler=self.execute_skill,
                retry_safety=self._async_retry_safety,
            ),
        )

    def _run_skill_script_definition(self) -> ToolDefinition:
        """调试/高级工具：默认不注册（模型只能通过 execute_skill 执行 Skill）。"""
        return ToolDefinition(
            name=RUN_SKILL_SCRIPT_TOOL,
            description=RUN_SKILL_SCRIPT_DESCRIPTION,
            input_schema=_input_schema(
                {
                    "skill_key": _STRING_SCHEMA,
                    "script": _STRING_SCHEMA,
                    "input": _OBJECT_SCHEMA,
                    "argv": _ARGV_SCHEMA,
                },
                ("skill_key", "script"),
            ),
            effect=ToolEffect.EXTERNAL,
            handler=self.run_skill_script,
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
        self, skill: ResolvedSkill, input_data: Mapping[str, Any], call_id: str, mode: CompletionMode,
        *, argv: tuple[str, ...] = (),
    ) -> str:
        """ASYNC Skill 的 ExecutionRouter：只创建 Parent Task，不本地执行。"""
        if self._task_client is None or self._task_context is None:
            raise SkillToolError(BACKGROUND_SUBMIT_FAILED, "durable task submitter is unavailable")
        try:
            submitted = await self._task_client.submit_task(
                self._task_context,
                skill=skill,
                input_data=input_data,
                call_id=call_id,
                completion_mode=mode,
                retry_safety=self._async_retry_safety,
                argv=argv,
            )
        except AppError as exc:
            raise SkillToolError(BACKGROUND_SUBMIT_FAILED, str(exc.code)) from exc
        return json.dumps(submitted, ensure_ascii=False)

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
        argv: tuple[str, ...] = (),    ) -> str:
        if skill_platform_keys(skill.frontmatter) and self._platform_context is None:
            raise SkillToolError(
                "PLATFORM_CONTEXT_REQUIRED", "Platform Skill requires a trusted execution context"
            )
        # 一次工具调用 = 一个独立执行 id：同一 Run 内的多次 Skill 调用不得共用
        # outputs/<run_id>，否则后一次会把前一次留下的文件当成自己的输出。
        execution_id = (
            _workspace_execution_id(self._execution_id, call_id)
            if self._execution_id
            else uuid.uuid4().hex
        )
        lease = await self._acquire_execution_workspace(execution_id)
        try:
            result = await self._run_executor(
                ready_dir, input_data, script=script, skill=skill, argv=argv,
                execution_id=execution_id,
                workspace_dir=lease.workspace_dir if lease is not None else self._workspace_dir,
            )
            self._record_execution(skill, result)
            outputs = await self._register_outputs(result)
            return _execution_payload(result, outputs)
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
        argv: tuple[str, ...] = (),
    ) -> SkillExecutionResult:
        try:
            return await self._executor.execute(
                SkillExecutionRequest(
                    ready_dir=ready_dir,
                    input=input_data,
                    argv=argv,
                    timeout_sec=self._timeout_sec,
                    source=str(skill.source),
                    workspace_dir=workspace_dir,
                    execution_id=execution_id,
                    script=script,
                    platform_context=replace(
                        self._platform_context, platform_keys=skill_platform_keys(skill.frontmatter)
                    )
                    if self._platform_context
                    else None,
                )
            )
        except SkillExecutionError as exc:
            raise SkillToolError(
                SKILL_EXECUTION_FAILED, exc.message, detail=exc.detail, retryable=exc.retryable
            ) from exc

    async def _acquire_execution_workspace(
        self, execution_id: str
    ) -> WorkspaceLease | None:
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

    async def _register_outputs(
        self, result: SkillExecutionResult
    ) -> list[dict[str, Any]]:
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
    """一次工具调用 = 一个独立 output 目录；只保留安全字符并受列长限制。"""
    safe = "".join(
        character if character.isalnum() or character in "-_." else "_"
        for character in str(call_id)
    ).strip("._-") or "call"
    prefix = str(run_id).replace("-", "")[:8] or "run"
    return f"{prefix}-{safe}"[:64]


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


def _manifest_payload(package: SkillPackage) -> dict[str, Any]:
    manifest = package.manifest
    return {
        "name": manifest.name,
        "description": manifest.description,
        "execution": manifest.execution.value,
        "platform_label": manifest.platform_label,
    }


def _execution_payload(
    result: SkillExecutionResult, outputs: Sequence[Mapping[str, Any]] = ()
) -> str:
    return json.dumps(
        {
            "status": result.status.value,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "result": result.result,
            "duration_ms": result.duration_ms,
            "exit_code": result.exit_code,
            "outputs": list(outputs),
            "output_notice": result.output_notice,
        },
        ensure_ascii=False,
    )


def _error_result(
    code: str,
    message: str,
    detail: dict[str, object] | None = None,
    *,
    retryable: bool = False,
) -> str:
    error: dict[str, object] = {"code": code, "message": message}
    if detail:
        # 平台非正常响应的可读原因（HTTP 非 2xx / 业务 code≠0）；成功与其它失败没有它。
        error["detail"] = detail
    if retryable:
        # 只有可重试失败（网络/超时/5xx/429）才标注；缺省不写＝确定性失败，第一次即终止 Run。
        error["retryable"] = True
    return json.dumps({"error": error}, ensure_ascii=False)


def skill_platform_keys(frontmatter: Mapping[str, Any]) -> tuple[str, ...]:
    value = frontmatter.get("platforms", [])
    if not isinstance(value, list) or any(not isinstance(k, str) or not k for k in value):
        raise SkillToolError("SKILL_PLATFORMS_INVALID", "Skill platforms must be a list of platform keys")
    return tuple(dict.fromkeys(value))
