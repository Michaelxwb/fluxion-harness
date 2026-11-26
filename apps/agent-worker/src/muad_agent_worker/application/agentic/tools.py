"""ToolRegistry 的冻结装配：**只构造 allowlist 内、且冻结授权仍成立**的工具。

三层，顺序固定，缺一层都不算"授权过"：

1. **allowlist**（Snapshot `allowlist`）：快照里没有的工具名根本不注册——模型看不到它；
2. **有效能力**（Snapshot `grants`）：`agent_access` 为假、Skill 不在 `skill_grants`、
   MCP server 不在 `mcp_grants` 的工具即使出现在 allowlist 也不注册；
3. **每次调用的冻结 Tool Policy**：注册表之外的名字被管线拒绝（`TOOL_NOT_ALLOWED`），
   未授权工具不产生任何外部请求。

工具 schema 只取自快照（`tools[].input_schema` / MCP `definitions`）：不在执行期查 catalog，
也不因为当前配置变化而扩展。

`ask_user` 是 Kernel 的平台内置工具（不属于用户可选的 allowlist）：它不产生外部副作用，
只把"向用户提问"暂存到挂起端口，由 WorkerLoop 在回合边界写入 Human interaction。它和
外部工具一样受 `agent_access` 门控。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, cast
from uuid import UUID
from zoneinfo import ZoneInfo

from muad_agent_core.tools import (
    ArtifactRef,
    PreparedToolCall,
    ToolCall,
    ToolCapability,
    ToolDefinition,
    ToolEffect,
    ToolError,
    ToolPolicyDecision,
    ToolRegistry,
    ToolResult,
)
from muad_agent_core.tools.pipeline import compute_args_hash
from muad_contracts import (
    SnapshotSkill,
    SnapshotTool,
    ToolRetrySafety,
)
from muad_contracts.skill_invocation import validate_skill_argv

from .mcp import (
    McpToolBox,
    McpToolError,
    frozen_mcp_tool_schema,
    mcp_tool_name,
    mcp_tool_trust,
)
from .skill_router import SkillExecutionRouter
from .snapshot import FrozenAgenticExecution
from .turn_result import RECORD_RESULT_ALLOWLIST_KEY, RECORD_RESULT_TOOL, ResultRecorder

TOOL_NOT_ALLOWED = "TOOL_NOT_ALLOWED"
EXECUTE_TOOL = "execute"
#: Skill 工具没有服务端 input schema：入参是任意 JSON，不能把“未知 schema”当成“禁止参数”。
FREE_FORM_TOOL_SCHEMA = {"type": "object", "additionalProperties": True}
ASK_USER_TOOL = "ask_user"
ASK_USER_ALLOWLIST_KEY = "PLATFORM:ask_user"
ASK_USER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["question"],
    "properties": {
        "question": {"type": "string", "minLength": 1, "maxLength": 2000},
        "input_schema": {"type": "object"},
    },
    "additionalProperties": False,
}


class SkillToolRunner(Protocol):
    """SYNC/ASYNC Skill 工具的执行者（异步提交与挂起由它自己完成）。

    `input` 只走 stdin，`argv` 追加到脚本；两者在工具边界就分开，执行者不得再从 `input`
    里猜参数。
    """

    async def call(
        self,
        skill: SnapshotSkill,
        tool_name: str,
        input: Mapping[str, Any],
        argv: Sequence[str],
        *,
        call_id: str,
        args_hash: str | None = None,
    ) -> str | ToolResult: ...


class TaskQueryPort(Protocol):
    """Read-only task facts for the scheduled agent; already owner/agent scoped."""

    async def list_tasks(self, *, limit: int) -> list[dict[str, Any]]: ...

    async def get_task(self, task_id: UUID) -> dict[str, Any] | None: ...


class HumanInputPort(Protocol):
    """把"向用户提问"暂存到挂起端口的窄接口（Worker 的 AgenticSuspension 实现它）。"""

    def stage_human(
        self,
        *,
        question: str,
        input_schema: Mapping[str, object] | None,
        deadline_at: datetime,
    ) -> None: ...


def allowlist_key(
    source: str, *, tool_name: str, server_key: str | None = None, skill_id: UUID | None = None
) -> str:
    if source == "MCP":
        return f"MCP:{server_key}:{tool_name}"
    return f"SKILL:{skill_id}:{tool_name}"


@dataclass(frozen=True, slots=True)
class RegisteredTool:
    name: str
    allowlist_key: str
    source: str
    skill_id: UUID | None = None
    server_id: UUID | None = None


class AgenticToolRegistry:
    """装配结果：注册表 + 允许清单 + 每次调用的策略。"""

    def __init__(self, registry: ToolRegistry, allowed: Mapping[str, RegisteredTool]) -> None:
        self.registry = registry
        self.allowed = dict(allowed)

    def policy(self, call: PreparedToolCall) -> ToolPolicyDecision:
        entry = self.allowed.get(call.tool_name)
        if entry is None:
            return ToolPolicyDecision(False, "tool is not in the frozen allowlist", TOOL_NOT_ALLOWED)
        return ToolPolicyDecision(True)


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class ScheduledPlatformTools:
    """AGENT_TURN 的定时上下文平台工具：可信时间 + 结构化结果记录。

    `zone` 来自冻结 Revision 的 IANA 时区；`clock` 只用于测试注入，生产走系统时钟。
    """

    zone: ZoneInfo
    result_recorder: ResultRecorder
    clock: Callable[[], datetime] = _utc_now
    task_query: TaskQueryPort | None = None

    def register(
        self,
        registry: ToolRegistry,
        allowed: dict[str, RegisteredTool],
        *,
        current_time: bool,
        task_query: bool = False,
    ) -> None:
        if current_time:
            _register_current_time(registry, allowed, self)
        if task_query and self.task_query is not None:
            _register_task_query(registry, allowed, self.task_query)
        self.result_recorder.register(registry)
        allowed[RECORD_RESULT_TOOL] = RegisteredTool(
            RECORD_RESULT_TOOL, RECORD_RESULT_ALLOWLIST_KEY, "PLATFORM"
        )


def _register_current_time(
    registry: ToolRegistry, allowed: dict[str, RegisteredTool], platform: ScheduledPlatformTools
) -> None:
    async def current_time(arguments: Mapping[str, Any], *, call: ToolCall) -> ToolResult:
        now = platform.clock().astimezone(platform.zone)
        return ToolResult(f"{now.isoformat(timespec='seconds')} ({platform.zone.key})")

    registry.register(
        ToolDefinition(
            name="current_time",
            description=(
                "Get the current date and time in this schedule's IANA time zone. Call it before "
                "reasoning about 'now' or relative dates instead of guessing."
            ),
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            effect=ToolEffect.READ,
            handler=current_time,
        )
    )
    allowed["current_time"] = RegisteredTool("current_time", "PLATFORM:current_time", "PLATFORM")


def _register_task_query(
    registry: ToolRegistry,
    allowed: dict[str, RegisteredTool],
    query: TaskQueryPort,
) -> None:
    """Owner-scoped read-only task facts; list is bounded, get is exact-id only."""

    async def list_tasks(arguments: Mapping[str, Any], *, call: ToolCall) -> ToolResult:
        raw_limit = arguments.get("limit", 10)
        if isinstance(raw_limit, bool) or not isinstance(raw_limit, int):
            return ToolResult(
                json.dumps({"status": "INVALID", "error": "limit must be an integer"})
            )
        limit = max(1, min(raw_limit, 20))
        items = await query.list_tasks(limit=limit)
        return ToolResult(json.dumps({"tasks": items}, ensure_ascii=False, default=str))

    async def get_task(arguments: Mapping[str, Any], *, call: ToolCall) -> ToolResult:
        raw = arguments.get("task_id")
        try:
            task_id = UUID(str(raw))
        except (TypeError, ValueError):
            return ToolResult(
                json.dumps({"status": "INVALID", "error": "task_id must be a UUID"})
            )
        item = await query.get_task(task_id)
        return ToolResult(
            json.dumps(
                {"status": "FOUND", "task": item} if item is not None else {"status": "NOT_FOUND"},
                ensure_ascii=False,
                default=str,
            )
        )

    for name, description, schema, handler in (
        (
            "list_tasks",
            "List this creator's recent tasks (bounded, newest first). Use it to inspect "
            "previous scheduled executions; task summaries only.",
            {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                },
                "additionalProperties": False,
            },
            list_tasks,
        ),
        (
            "get_task",
            "Read one task by id, scoped to this creator and agent. Returns a bounded summary.",
            {
                "type": "object",
                "properties": {"task_id": {"type": "string"}},
                "required": ["task_id"],
                "additionalProperties": False,
            },
            get_task,
        ),
    ):
        registry.register(
            ToolDefinition(
                name=name,
                description=description,
                input_schema=schema,
                effect=ToolEffect.READ,
                handler=handler,
            )
        )
        allowed[name] = RegisteredTool(name, f"PLATFORM:{name}", "PLATFORM")


def build_registry(
    frozen: FrozenAgenticExecution,
    *,
    mcp: McpToolBox | None,
    skills: SkillToolRunner | None,
    human: HumanInputPort | None = None,
    platform: ScheduledPlatformTools | None = None,
    attachments: Any = None,
) -> AgenticToolRegistry:
    """按快照构造注册表；未注册 = 模型看不到、调用即 `unknown tool`。"""
    snapshot = frozen.snapshot
    allowlist = set(snapshot.allowlist)
    grants = snapshot.grants
    registry = ToolRegistry()
    allowed: dict[str, RegisteredTool] = {}
    if not grants.agent_access:
        return AgenticToolRegistry(registry, allowed)
    mcp_ids = set(grants.mcp_grants)
    _register_mcp(frozen, registry, allowed, allowlist, mcp_ids, mcp)
    _register_execute(frozen, registry, allowed, skills)
    if platform is not None and frozen.scheduled:
        configured = frozen.tools_allow
        follows_owner = not configured or "*" in configured
        current_time = follows_owner or "PLATFORM:current_time" in configured
        task_query = follows_owner or any(
            f"PLATFORM:{name}" in configured for name in ("list_tasks", "get_task")
        )
        platform.register(
            registry, allowed, current_time=current_time, task_query=task_query
        )
    if human is not None:
        _register_human(registry, allowed, human, deadline_at=frozen.deadline_at)
    if attachments is not None:
        attachments.register(registry)
        for name in ("read_attachment", "view_image"):
            allowed[name] = RegisteredTool(name, f"PLATFORM:{name}", "PLATFORM")
    return AgenticToolRegistry(registry, allowed)


def _register_human(
    registry: ToolRegistry,
    allowed: dict[str, RegisteredTool],
    human: HumanInputPort,
    *,
    deadline_at: datetime,
) -> None:
    """平台内置提问工具：不依赖外部 allowlist，但受 `agent_access` 门控。"""

    async def ask(arguments: Mapping[str, Any], *, call: ToolCall) -> ToolResult:
        question = arguments.get("question")
        if not isinstance(question, str) or not question.strip():
            return ToolResult(
                json.dumps(
                    {"status": "INVALID", "error": "question must be a non-empty string"},
                    ensure_ascii=False,
                )
            )
        input_schema = arguments.get("input_schema")
        if input_schema is not None and not isinstance(input_schema, Mapping):
            return ToolResult(
                json.dumps(
                    {"status": "INVALID", "error": "input_schema must be an object"},
                    ensure_ascii=False,
                )
            )
        try:
            human.stage_human(
                question=question.strip(),
                input_schema=input_schema,
                deadline_at=deadline_at,
            )
        except ValueError as exc:
            # 同一回合已有 ASYNC 操作时不能混两种挂起：把拒绝交回模型，不中断整次执行。
            return ToolResult(
                json.dumps({"status": "UNAVAILABLE", "error": str(exc)}, ensure_ascii=False)
            )
        return ToolResult(json.dumps({"status": "QUESTION_PENDING"}, ensure_ascii=False))

    registry.register(
        ToolDefinition(
            name=ASK_USER_TOOL,
            description=(
                "Ask the user for missing information or confirmation. The execution pauses at "
                "the end of this turn and resumes with the user's answer."
            ),
            input_schema=ASK_USER_SCHEMA,
            effect=ToolEffect.READ,
            handler=ask,
        )
    )
    allowed[ASK_USER_TOOL] = RegisteredTool(ASK_USER_TOOL, ASK_USER_ALLOWLIST_KEY, "PLATFORM")


def _register_mcp(
    frozen: FrozenAgenticExecution,
    registry: ToolRegistry,
    allowed: dict[str, RegisteredTool],
    allowlist: set[str],
    mcp_ids: set[UUID],
    mcp: McpToolBox | None,
) -> None:
    if mcp is None:
        return
    for server in frozen.snapshot.mcp:
        if server.server_id not in mcp_ids:
            continue
        for definition in server.definitions:
            tool_name = definition.get("name")
            if not isinstance(tool_name, str) or not tool_name:
                continue
            key = allowlist_key("MCP", tool_name=tool_name, server_key=server.key)
            if key not in allowlist:
                continue
            schema = frozen_mcp_tool_schema(server, tool_name)
            if schema is None:
                continue
            trust = mcp_tool_trust(tuple(frozen.snapshot.tools), tool_name)
            # SnapshotTool is keyed by name. The server-scoped definition must corroborate replay safety.
            retry_safety = (
                _retry_safety(trust) if trust and definition.get("retry_safety") == trust.retry_safety
                else ToolRetrySafety.NON_RETRYABLE
            )
            name = mcp_tool_name(server.key, tool_name)
            registry.register(
                ToolDefinition(
                    name=name,
                    description=str(definition.get("description") or tool_name),
                    input_schema=schema,
                    effect=ToolEffect.READ,
                    retry_safety=retry_safety,
                    handler=_mcp_handler(mcp, server.server_id, tool_name, retry_safety),
                    capabilities=frozenset({ToolCapability.MCP_CALL}),
                )
            )
            allowed[name] = RegisteredTool(name, key, "MCP", server_id=server.server_id)


def _mcp_handler(
    mcp: McpToolBox,
    server_id: UUID,
    tool_name: str,
    retry_safety: ToolRetrySafety,
) -> Any:
    async def call(arguments: Mapping[str, Any], *, call: ToolCall) -> ToolResult:
        try:
            result = await mcp.call(
                server_id,
                tool_name,
                arguments,
                retry_safety=retry_safety,
                call_id=call.call_id,
            )
        except McpToolError as exc:
            raise ToolError(
                exc.code,
                str(exc),
                retryable=exc.retryable,
            ) from exc
        return ToolResult(
            result.text,
            artifacts=tuple(
                ArtifactRef(
                    artifact_id=str(item.artifact_id),
                    media_type=item.media_type,
                    filename=item.filename,
                    size=len(item.data),
                )
                for item in result.artifacts
                if item.artifact_id
            ),
        )

    return call


def _register_execute(
    frozen: FrozenAgenticExecution,
    registry: ToolRegistry,
    allowed: dict[str, RegisteredTool],
    skills: SkillToolRunner | None,
) -> None:
    """One model-facing skill entry: execute(target=skill)."""
    if skills is None:
        return
    router = SkillExecutionRouter(
        skills=frozen.snapshot.skills,
        tools=frozen.snapshot.tools,
        allowlist=frozen.snapshot.allowlist,
        skill_grants=frozen.snapshot.grants.skill_grants,
    )

    async def execute(arguments: Mapping[str, Any], *, call: ToolCall) -> ToolResult:
        if arguments.get("target") != "skill":
            raise ToolError("COMMON_VALIDATION_ERROR", "target must be skill")
        route = router.resolve(arguments.get("skill_key"), arguments.get("tool"))
        input_value = router.validate_input(route, arguments.get("input"))
        argv = router.validate_argv(arguments.get("argv"))
        try:
            content = await skills.call(
                route.skill,
                skill_tool_name(route.skill.skill_id, route.tool.tool_name),
                input_value,
                argv,
                call_id=call.call_id,
                args_hash=compute_args_hash(arguments),
            )
        except ToolError:
            raise
        except Exception as exc:
            code = getattr(exc, "code", None)
            if not isinstance(code, str) or not code:
                raise
            raise ToolError(
                code,
                str(exc),
                retryable=bool(getattr(exc, "retryable", False)),
            ) from exc
        return content if isinstance(content, ToolResult) else ToolResult(content)

    registry.register(
        ToolDefinition(
            name=EXECUTE_TOOL,
            description=(
                "Execute one tool of a frozen skill. Pass skill_key; pass tool only when the "
                "skill has multiple executable tools. input is JSON stdin, argv are literal CLI "
                "arguments. For instructions-only skills use load_skill and MCP tools instead."
            ),
            input_schema={
                "type": "object",
                "required": ["target", "skill_key"],
                "additionalProperties": False,
                "properties": {
                    "target": {"type": "string", "enum": ["skill"]},
                    "skill_key": {"type": "string", "minLength": 1},
                    "tool": {"type": "string", "minLength": 1},
                    "input": {"type": "object"},
                    "argv": {"type": "array", "items": {"type": "string"}},
                },
            },
            effect=ToolEffect.EXTERNAL,
            handler=execute,
            capabilities=frozenset({ToolCapability.SKILL_EXECUTE}),
        )
    )
    allowed[EXECUTE_TOOL] = RegisteredTool(EXECUTE_TOOL, "SKILL:execute", "SKILL")

    if hasattr(skills, "load_skill"):
        instruction_skills = cast(Any, skills)

        async def load_skill(arguments: Mapping[str, Any], *, call: ToolCall) -> ToolResult:
            key = arguments.get("skill_key")
            if not isinstance(key, str) or not key:
                raise ToolError("COMMON_VALIDATION_ERROR", "skill_key must be a non-empty string")
            return ToolResult(await instruction_skills.load_skill(key))

        async def read_resource(arguments: Mapping[str, Any], *, call: ToolCall) -> ToolResult:
            key = arguments.get("skill_key")
            path = arguments.get("path")
            if not isinstance(key, str) or not key or not isinstance(path, str) or not path:
                raise ToolError("COMMON_VALIDATION_ERROR", "skill_key and path are required")
            return ToolResult(await instruction_skills.read_skill_resource(key, path))

        async def search(arguments: Mapping[str, Any], *, call: ToolCall) -> ToolResult:
            query = arguments.get("query", "")
            if not isinstance(query, str):
                raise ToolError("COMMON_VALIDATION_ERROR", "query must be a string")
            return ToolResult(await instruction_skills.search_skills(query))

        registry.register(
            ToolDefinition(
                name="load_skill",
                description="Load a frozen skill's instructions.",
                input_schema={
                    "type": "object",
                    "required": ["skill_key"],
                    "additionalProperties": False,
                    "properties": {"skill_key": {"type": "string"}},
                },
                effect=ToolEffect.READ,
                handler=load_skill,
                externalizable_result=False,
                capabilities=frozenset({ToolCapability.SKILL_INSTRUCTIONS}),
            )
        )
        registry.register(
            ToolDefinition(
                name="read_skill_resource",
                description="Read one text resource from a frozen skill.",
                input_schema={
                    "type": "object",
                    "required": ["skill_key", "path"],
                    "additionalProperties": False,
                    "properties": {"skill_key": {"type": "string"}, "path": {"type": "string"}},
                },
                effect=ToolEffect.READ,
                handler=read_resource,
                externalizable_result=False,
                capabilities=frozenset({ToolCapability.SKILL_RESOURCE}),
            )
        )
        registry.register(
            ToolDefinition(
                name="search_skills",
                description="Search the frozen skill catalog by key or name.",
                input_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"query": {"type": "string"}},
                },
                effect=ToolEffect.READ,
                handler=search,
                capabilities=frozenset({ToolCapability.SKILL_CATALOG}),
            )
        )
        allowed["load_skill"] = RegisteredTool("load_skill", "PLATFORM:load_skill", "PLATFORM")
        allowed["read_skill_resource"] = RegisteredTool(
            "read_skill_resource", "PLATFORM:read_skill_resource", "PLATFORM"
        )
        allowed["search_skills"] = RegisteredTool("search_skills", "PLATFORM:search_skills", "PLATFORM")


def _skill_tool_schema(tool: SnapshotTool) -> dict[str, Any]:
    """Skill 工具的入参信封：`input` 只写 stdin，`argv` 追加到脚本。

    `input` 保留冻结的业务 schema；没有 schema 时是任意 JSON 对象。两者分开是因为
    argparse 参数与 stdin 文档是两种契约，混在一起时脚本无法区分。
    """
    business = dict(tool.input_schema) or dict(FREE_FORM_TOOL_SCHEMA)
    return {
        "type": "object",
        "properties": {
            "input": business,
            "argv": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Business CLI arguments appended after the script.",
            },
        },
        "additionalProperties": False,
    }


def _parse_skill_arguments(
    arguments: Mapping[str, Any],
) -> tuple[dict[str, Any], tuple[str, ...]] | ToolError:
    """把模型参数拆成 `(input, argv)`；形状不对时返回 typed 工具失败。"""
    if "input" in arguments:
        raw_input = arguments["input"]
    else:
        raw_input = {}
    if not isinstance(raw_input, Mapping):
        return ToolError("COMMON_VALIDATION_ERROR", "input must be an object")
    raw_argv = arguments.get("argv", [])
    try:
        argv = validate_skill_argv(raw_argv)
    except ValueError as exc:
        return ToolError("COMMON_VALIDATION_ERROR", str(exc))
    return dict(raw_input), argv


def _skill_handler(runner: SkillToolRunner, skill: SnapshotSkill, tool_name: str) -> Any:
    async def call(arguments: Mapping[str, Any], *, call: ToolCall) -> ToolResult:
        parsed = _parse_skill_arguments(arguments)
        if isinstance(parsed, ToolError):
            raise parsed
        input_value, argv = parsed
        try:
            content = await runner.call(
                skill,
                tool_name,
                input_value,
                argv,
                call_id=call.call_id,
                args_hash=compute_args_hash(arguments),
            )
        except ToolError:
            raise
        except Exception as exc:
            code = getattr(exc, "code", None)
            if not isinstance(code, str) or not code:
                raise
            raise ToolError(
                code,
                str(exc),
                retryable=bool(getattr(exc, "retryable", False)),
            ) from exc
        return content if isinstance(content, ToolResult) else ToolResult(content)

    return call


def skill_tool_name(skill_id: UUID, tool_name: str) -> str:
    return f"skill::{skill_id}::{tool_name}"


def _retry_safety(tool: SnapshotTool | None) -> ToolRetrySafety:
    if tool is None or not tool.retry_safety:
        return ToolRetrySafety.NON_RETRYABLE
    try:
        return ToolRetrySafety(tool.retry_safety)
    except ValueError:
        return ToolRetrySafety.NON_RETRYABLE


def policy_for(agentic: AgenticToolRegistry) -> Callable[[PreparedToolCall], ToolPolicyDecision]:
    return agentic.policy


__all__ = [
    "AgenticToolRegistry",
    "RegisteredTool",
    "ScheduledPlatformTools",
    "SkillToolRunner",
    "TOOL_NOT_ALLOWED",
    "allowlist_key",
    "build_registry",
    "policy_for",
    "skill_tool_name",
]
