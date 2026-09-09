from __future__ import annotations

import hashlib
import secrets
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from time import perf_counter
from typing import Protocol
from uuid import uuid4

from fluxion.commands import (
    CommandContext,
    CommandInvocation,
    CommandOutcome,
    CommandRegistry,
    ImmediateReply,
    RuntimeInvocation,
    dispatch,
    parse_command,
)
from fluxion.commands.errors import BOUND, ChannelBindError
from fluxion.commands.handlers.bind import BindCommandHandler, hash_bind_code
from fluxion.commands.handlers.help import HelpCommandHandler
from fluxion.commands.handlers.new import NewCommandHandler
from fluxion.commands.handlers.skill import SkillCommandHandler
from fluxion.commands.handlers.skills import SkillsCommandHandler
from fluxion.commands.handlers.status import StatusCommandHandler
from fluxion.commands.handlers.stop import StopCommandHandler
from fluxion.commands.metrics import CommandMetrics
from fluxion.observability.tracing import traced_scope
from fluxion.protocols.channel import (
    ChannelAdapter,
    ChannelMessage,
    ChannelResult,
    ExternalChannelMessage,
)
from fluxion.registry import (
    AuditRecord,
    BindCodeRecord,
    ChannelIdentityRecord,
    ChannelRegistryStore,
    ChatAccessRecord,
    PlatformUserRecord,
)
from fluxion.services.capability_query_service import CapabilityQueryService
from fluxion.services.channel_auth import ChannelAuthError, VerifiedChannelIdentity
from fluxion.services.chat_session_service import ChatSessionService, SessionActivityProbe
from fluxion.services.execution_control_service import ExecutionControlService
from fluxion.services.runtime_app import (
    RunRuntimeRequest,
    RunRuntimeResult,
    RuntimeStreamEvent,
)
from fluxion.services.runtime_contracts import (
    CancelExecutionResult,
    InvocationDirective,
    SessionExecutionStatus,
)


class RuntimeGateway(Protocol):
    async def run(self, request: RunRuntimeRequest) -> RunRuntimeResult: ...

    def stream(self, request: RunRuntimeRequest) -> AsyncIterator[RuntimeStreamEvent]: ...

    async def cancel_active_execution(
        self,
        *,
        tenant_id: str,
        user_id: str,
        agent_definition_id: str,
        session_id: str,
    ) -> CancelExecutionResult: ...

    async def get_session_status(
        self,
        *,
        tenant_id: str,
        user_id: str,
        agent_definition_id: str,
        session_id: str,
    ) -> SessionExecutionStatus: ...


# ChannelBindError 由 fluxion.commands.errors 统一定义；列入 __all__ 即为
# 对外重导出（mypy/ruff 均认可），保持 api 与既有测试的导入路径可用。
__all__ = [
    "ChannelAccessError",
    "ChannelApplicationService",
    "ChannelBindError",
    "ChannelProfileResolutionError",
    "IssuedBindCode",
    "RuntimeGateway",
]


class ChannelAccessError(RuntimeError):
    code = "chat_access_denied"

    def __init__(self) -> None:
        super().__init__("Chat 访问链接无效或已撤销")


class ChannelProfileResolutionError(RuntimeError):
    """ADR-A010：Agent 的 RuntimeProfile 不可解析（同名回退已废弃，fail-closed）。"""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True, slots=True)
class IssuedBindCode:
    code: str
    expires_at: datetime


class ChannelApplicationService:
    def __init__(
        self,
        store: ChannelRegistryStore,
        runtime: RuntimeGateway,
        *,
        code_factory: Callable[[], str] | None = None,
        clock: Callable[[], datetime] | None = None,
        session_activity_probe: SessionActivityProbe | None = None,
        metrics: CommandMetrics | None = None,
    ) -> None:
        self._store = store
        self._runtime = runtime
        self._code_factory = code_factory or (lambda: secrets.token_urlsafe(18))
        self._clock = clock or (lambda: datetime.now(UTC))
        # §22.2 命令指标（进程内注册表；实例持有，测试可断言）。
        self._metrics = metrics or CommandMetrics()
        self._sessions = ChatSessionService(store, clock=self._clock)
        # TASK-005：/new 冲突探针接真实 Control Store（读 active 记录）。
        # 构造参数仍允许注入替换（测试/未来多 Pod 信号源）。
        self._execution_control = ExecutionControlService(
            store, service_instance_id="channel-app"
        )
        self._session_activity_probe = session_activity_probe or self._execution_control.has_active_execution
        # Command Plane：启动期一次性构建 Registry（§7.6）。
        # 本期注册 bind + help + new + skills + skill + stop + status，全量就绪。
        self._commands = CommandRegistry()
        self._commands.register(BindCommandHandler(store, self._clock))
        self._commands.register(
            NewCommandHandler(self._sessions, self._session_activity_probe)
        )
        self._capabilities = CapabilityQueryService(store)
        self._commands.register(SkillsCommandHandler(self._capabilities))
        self._commands.register(SkillCommandHandler(self._capabilities))
        self._commands.register(StopCommandHandler(self._sessions, self._runtime))
        self._commands.register(StatusCommandHandler(self._sessions, self._runtime))
        self._commands.register(HelpCommandHandler(self._commands))

    @property
    def metrics(self) -> CommandMetrics:
        """命令指标快照入口（测试/调试）。"""
        return self._metrics

    async def _active_session(
        self,
        *,
        tenant_id: str,
        channel_type: str,
        external_conversation_id: str,
        platform_user_id: str,
        agent_id: str,
    ) -> str:
        """外部会话 → Runtime 逻辑 Session（§10.5，不再直传 conversation_id）。"""
        return await self._sessions.resolve_session(
            tenant_id, channel_type, external_conversation_id, platform_user_id, agent_id
        )

    async def _dispatch_command(
        self, context: CommandContext, invocation: CommandInvocation
    ) -> CommandOutcome:
        """命令分发统一出口（§22 可观测）：span + 指标 + 审计。

        审计只记命令名/结果码/session（§22.1），永不记 prompt 原文与 secret。
        """
        started = perf_counter()
        async with traced_scope(
            "chat.command",
            attributes={
                "fluxion.command.name": invocation.name,
                "fluxion.agent_id": context.agent_id,
                "fluxion.session_id": context.active_session_id or "",
            },
        ):
            outcome = await dispatch(self._commands, context, invocation)
        code = outcome.code if isinstance(outcome, ImmediateReply) else "runtime_invocation"
        self._metrics.observe(invocation.name, code, (perf_counter() - started) * 1000)
        await self._audit_command(context, invocation, code, outcome)
        return outcome

    async def _audit_command(
        self,
        context: CommandContext,
        invocation: CommandInvocation,
        code: str,
        outcome: CommandOutcome,
    ) -> None:
        """命令审计（§22.1）：executed/rejected + skill 显式选择 + 取消请求。"""
        action = "command.executed" if code in _EXECUTED_CODES else "command.rejected"
        after: dict[str, object] = {
            "command": invocation.name,
            "result_code": code,
            "session_id": context.active_session_id or "",
        }
        await self._store.append_audit(
            AuditRecord(
                audit_id=f"audit_{uuid4().hex}",
                tenant_id=context.tenant_id,
                actor_id=context.platform_user_id or "anonymous",
                request_id=context.request_id,
                action=action,
                target_type="command",
                target_id=invocation.name,
                before=None,
                after=after,
                created_at=self._clock(),
                trace_id=context.trace_id,
            )
        )
        if isinstance(outcome, RuntimeInvocation) and _skill_directive(outcome) is not None:
            await self._store.append_audit(
                AuditRecord(
                    audit_id=f"audit_{uuid4().hex}",
                    tenant_id=context.tenant_id,
                    actor_id=context.platform_user_id or "anonymous",
                    request_id=context.request_id,
                    action="skill.explicitly_selected",
                    target_type="skill",
                    target_id=_skill_directive(outcome) or "",
                    before=None,
                    after={"skill_id": _skill_directive(outcome)},
                    created_at=self._clock(),
                    trace_id=context.trace_id,
                )
            )
        if invocation.name == "stop" and code == "stop_requested":
            session_id = ""
            if isinstance(outcome, ImmediateReply):
                session_id = str(outcome.data.get("session_id", ""))
            await self._store.append_audit(
                AuditRecord(
                    audit_id=f"audit_{uuid4().hex}",
                    tenant_id=context.tenant_id,
                    actor_id=context.platform_user_id or "anonymous",
                    request_id=context.request_id,
                    action="execution.cancel_requested",
                    target_type="execution",
                    target_id=session_id,
                    before=None,
                    after={"session_id": session_id, "reason": "stop_requested"},
                    created_at=self._clock(),
                    trace_id=context.trace_id,
                )
            )

    async def create_platform_user(
        self, tenant_id: str, platform_user_id: str, *, display_name: str = ""
    ) -> PlatformUserRecord:
        now = self._clock()
        record = PlatformUserRecord(
            tenant_id=tenant_id,
            platform_user_id=platform_user_id,
            display_name=display_name or platform_user_id,
            created_at=now,
        )
        return await self._store.create_platform_user(record)

    async def issue_bind_code(
        self,
        tenant_id: str,
        platform_user_id: str,
        *,
        expires_at: datetime | None = None,
    ) -> IssuedBindCode:
        code = self._code_factory()
        now = self._clock()
        expiry = expires_at or now + timedelta(minutes=10)
        await self._store.create_bind_code(
            BindCodeRecord(
                bind_code_id=f"bind_code_{uuid4().hex}",
                tenant_id=tenant_id,
                platform_user_id=platform_user_id,
                code_hash=hash_bind_code(code),
                expires_at=expiry,
                created_at=now,
            )
        )
        return IssuedBindCode(code=code, expires_at=expiry)

    async def resolve_identity(
        self, tenant_id: str, channel_type: str, channel_user_id: str
    ) -> ChannelIdentityRecord | None:
        return await self._store.resolve_channel_identity(
            tenant_id=tenant_id,
            channel_type=channel_type,
            channel_user_id=channel_user_id,
        )

    async def resolve_chat_access(self, token: str) -> ChatAccessRecord:
        if not token.strip():
            raise ChannelAccessError()
        record = await self._store.resolve_chat_access(token_hash=_hash_access_token(token))
        if record is None:
            raise ChannelAccessError()
        return record

    async def handle_chat_access(
        self,
        token: str,
        *,
        conversation_id: str,
        content: str,
        request_id: str,
        trace_id: str,
    ) -> ChannelResult:
        access = await self.resolve_chat_access(token)
        parsed = parse_command(content)
        if parsed.kind == "command" and parsed.invocation is not None:
            return await self._execute_access_command(
                access, conversation_id, parsed.invocation, request_id, trace_id
            )
        runtime_result = await self._runtime.run(
            RunRuntimeRequest(
                tenant_id=access.tenant_id,
                user_id=access.platform_user_id,
                runtime_profile_id=await self._profile_id_for(access.tenant_id, access.agent_id),
                agent_definition_id=access.agent_id,
                session_id=await self._active_session(
                    tenant_id=access.tenant_id,
                    channel_type="web",
                    external_conversation_id=conversation_id,
                    platform_user_id=access.platform_user_id,
                    agent_id=access.agent_id,
                ),
                input_message=parsed.literal_text,
                request_id=request_id,
                trace_id=trace_id,
            )
        )
        return ChannelResult(
            kind="message",
            output=runtime_result.output,
            platform_user_id=access.platform_user_id,
            request_id=runtime_result.request_id,
            trace_id=runtime_result.trace_id,
            execution_id=runtime_result.execution_id,
        )

    async def _execute_access_command(
        self,
        access: ChatAccessRecord,
        conversation_id: str,
        invocation: CommandInvocation,
        request_id: str,
        trace_id: str,
    ) -> ChannelResult:
        """Web Chat 命令执行（§9.3：Web 不展示 /bind，命中即 command_not_available）。"""
        session_id = await self._active_session(
            tenant_id=access.tenant_id,
            channel_type="web",
            external_conversation_id=conversation_id,
            platform_user_id=access.platform_user_id,
            agent_id=access.agent_id,
        )
        outcome = await self._dispatch_command(
            CommandContext(
                tenant_id=access.tenant_id,
                platform_user_id=access.platform_user_id,
                agent_id=access.agent_id,
                ingress_type="web_chat",
                channel_type="web",
                channel_user_id=None,
                external_conversation_id=conversation_id,
                active_session_id=session_id,
                request_id=request_id,
                trace_id=trace_id,
                authenticated=True,
            ),
            invocation,
        )
        if isinstance(outcome, RuntimeInvocation):
            return await self._run_access_prompt(
                access, conversation_id, outcome.prompt, request_id, trace_id,
                directive=outcome.directive,
            )
        return _command_result(outcome, access.platform_user_id, request_id, trace_id)

    async def _run_access_prompt(
        self,
        access: ChatAccessRecord,
        conversation_id: str,
        prompt: str,
        request_id: str,
        trace_id: str,
        *,
        directive: InvocationDirective | None = None,
    ) -> ChannelResult:
        runtime_result = await self._runtime.run(
            RunRuntimeRequest(
                tenant_id=access.tenant_id,
                user_id=access.platform_user_id,
                runtime_profile_id=await self._profile_id_for(access.tenant_id, access.agent_id),
                agent_definition_id=access.agent_id,
                session_id=await self._active_session(
                    tenant_id=access.tenant_id,
                    channel_type="web",
                    external_conversation_id=conversation_id,
                    platform_user_id=access.platform_user_id,
                    agent_id=access.agent_id,
                ),
                input_message=prompt,
                request_id=request_id,
                trace_id=trace_id,
                invocation_directive=directive,
            )
        )
        return ChannelResult(
            kind="message",
            output=runtime_result.output,
            platform_user_id=access.platform_user_id,
            request_id=runtime_result.request_id,
            trace_id=runtime_result.trace_id,
            execution_id=runtime_result.execution_id,
        )

    async def stream_chat_access(
        self,
        token: str,
        *,
        conversation_id: str,
        content: str,
        request_id: str,
        trace_id: str,
    ) -> AsyncGenerator[RuntimeStreamEvent, None]:
        """流式转发 Runtime 的 started/token 事件，completed 包装为 ChannelResult 结构。

        命令命中时只产出单个 terminal completed 事件（§20），不伪造 started。
        """
        access = await self.resolve_chat_access(token)
        parsed = parse_command(content)
        if parsed.kind == "command" and parsed.invocation is not None:
            result = await self._execute_access_command(
                access, conversation_id, parsed.invocation, request_id, trace_id
            )
            yield RuntimeStreamEvent(event="completed", data=result.to_payload())
            return
        request = RunRuntimeRequest(
            tenant_id=access.tenant_id,
            user_id=access.platform_user_id,
            runtime_profile_id=await self._profile_id_for(access.tenant_id, access.agent_id),
            agent_definition_id=access.agent_id,
            session_id=await self._active_session(
                tenant_id=access.tenant_id,
                channel_type="web",
                external_conversation_id=conversation_id,
                platform_user_id=access.platform_user_id,
                agent_id=access.agent_id,
            ),
            input_message=parsed.literal_text,
            request_id=request_id,
            trace_id=trace_id,
        )
        async for event in self._runtime.stream(request):
            if event.event == "completed":
                yield RuntimeStreamEvent(
                    event="completed",
                    data={
                        "kind": "message",
                        "output": event.data.get("output"),
                        "platform_user_id": access.platform_user_id,
                        "request_id": event.data.get("request_id"),
                        "trace_id": event.data.get("trace_id"),
                        "execution_id": event.data.get("execution_id"),
                    },
                )
            else:
                yield event

    async def handle(
        self,
        adapter: ChannelAdapter,
        external: ExternalChannelMessage,
        *,
        verified: VerifiedChannelIdentity | None,
    ) -> ChannelResult:
        """IM/匿名通道统一入口（S1）：调用方必须传入验证结论。

        - `verified=None`（匿名）：只走未绑定流（bind 兑换/提示），即使该
          channel_user_id 已绑定也不执行——冒充在结构上不可能；
        - `verified` 与消息 channel_user_id 不一致：`ChannelAuthError`；
        - 一致：按 resolve 映射执行（verified 只做门禁，不替代映射）。
        """
        message = adapter.normalize_inbound(external)
        if verified is not None and verified.external_user_id != message.channel_user_id:
            raise ChannelAuthError(
                method="channel_identity", reason="identity_mismatch", status_code=403
            )
        identity = await self.resolve_identity(
            message.tenant_id, message.channel_type, message.channel_user_id
        )
        if identity is None or verified is None:
            result = await self._handle_unbound(message)
        else:
            result = await self._run_bound(message, identity.platform_user_id)
        await adapter.push_outbound(result)
        return result

    async def _handle_unbound(self, message: ChannelMessage) -> ChannelResult:
        # Command Plane（Phase 1）：未绑定用户只允许命令，且 Dispatcher 对
        # 非 bind 一律回 identity_binding_required（§9.3）；特判路径已删除。
        parsed = parse_command(message.content)
        if parsed.kind != "command" or parsed.invocation is None:
            return ChannelResult(
                kind="unbound",
                output="请先使用 /bind <code> 完成绑定",
                request_id=message.request_id,
                trace_id=message.trace_id,
            )
        outcome = await self._dispatch_command(
            CommandContext(
                tenant_id=message.tenant_id,
                platform_user_id=None,
                agent_id=message.agent_id,
                ingress_type="channel",
                channel_type=message.channel_type,
                channel_user_id=message.channel_user_id,
                external_conversation_id=message.conversation_id,
                active_session_id=None,
                request_id=message.request_id,
                trace_id=message.trace_id,
                authenticated=False,
            ),
            parsed.invocation,
        )
        # BindCommand 兑换失败抛 ChannelBindError（边界异常，调用方映射）；
        # 成功回 bound（沿用旧 kind，外部契约不变）；其余拒绝回 unbound 提示。
        if isinstance(outcome, ImmediateReply) and outcome.code == BOUND:
            platform_user_id = outcome.data.get("platform_user_id")
            return ChannelResult(
                kind="bound",
                output=outcome.message,
                platform_user_id=str(platform_user_id) if platform_user_id else None,
                request_id=message.request_id,
                trace_id=message.trace_id,
            )
        if isinstance(outcome, ImmediateReply):
            return ChannelResult(
                kind="unbound",
                output=outcome.message,
                request_id=message.request_id,
                trace_id=message.trace_id,
            )
        return ChannelResult(
            kind="unbound",
            output="请先使用 /bind <code> 完成绑定",
            request_id=message.request_id,
            trace_id=message.trace_id,
        )

    async def _run_bound(self, message: ChannelMessage, platform_user_id: str) -> ChannelResult:
        parsed = parse_command(message.content)
        if parsed.kind == "command" and parsed.invocation is not None:
            session_id = await self._active_session(
                tenant_id=message.tenant_id,
                channel_type=message.channel_type,
                external_conversation_id=message.conversation_id,
                platform_user_id=platform_user_id,
                agent_id=message.agent_id,
            )
            outcome = await self._dispatch_command(
                CommandContext(
                    tenant_id=message.tenant_id,
                    platform_user_id=platform_user_id,
                    agent_id=message.agent_id,
                    ingress_type="channel",
                    channel_type=message.channel_type,
                    channel_user_id=message.channel_user_id,
                    external_conversation_id=message.conversation_id,
                    active_session_id=session_id,
                    request_id=message.request_id,
                    trace_id=message.trace_id,
                    authenticated=True,
                ),
                parsed.invocation,
            )
            if isinstance(outcome, ImmediateReply):
                return _command_result(
                    outcome, platform_user_id, message.request_id, message.trace_id
                )
            input_message = outcome.prompt
            invocation_directive = outcome.directive
        else:
            input_message = parsed.literal_text
            invocation_directive = None
        runtime_result = await self._runtime.run(
            RunRuntimeRequest(
                tenant_id=message.tenant_id,
                user_id=platform_user_id,
                runtime_profile_id=await self._profile_id_for(message.tenant_id, message.agent_id),
                agent_definition_id=message.agent_id,
                session_id=await self._active_session(
                    tenant_id=message.tenant_id,
                    channel_type=message.channel_type,
                    external_conversation_id=message.conversation_id,
                    platform_user_id=platform_user_id,
                    agent_id=message.agent_id,
                ),
                input_message=input_message,
                request_id=message.request_id,
                trace_id=message.trace_id,
                invocation_directive=invocation_directive,
            )
        )
        return ChannelResult(
            kind="message",
            output=runtime_result.output,
            platform_user_id=platform_user_id,
            request_id=runtime_result.request_id,
            trace_id=runtime_result.trace_id,
            execution_id=runtime_result.execution_id,
        )

    async def _profile_id_for(self, tenant_id: str, agent_id: str) -> str:
        """TASK-A104/A105：执行所需 mechanics profile 键，来源为 Agent 的
        runtime_profile_ref；未配置走 ADR-A010 租户默认链（同名回退已废弃）。"""
        from fluxion.agents.definitions import AgentDefinition
        from fluxion.resources import ResourceKind
        from fluxion.services.runtime_profile_resolution import (
            resolve_default_runtime_profile,
        )

        row = await self._store.get(
            ResourceKind.AGENT_DEFINITION, agent_id, tenant_id=tenant_id
        )
        if row is None:
            raise ChannelProfileResolutionError(f"agent_not_found: {agent_id}")
        spec = AgentDefinition.model_validate(row.spec_json)
        if spec.runtime_profile_ref is not None:
            return spec.runtime_profile_ref.id
        default = await resolve_default_runtime_profile(self._store, tenant_id)
        if default is None:
            raise ChannelProfileResolutionError(
                f"no default RuntimeProfile for agent {agent_id} "
                "(tenant default and platform-default both missing, ADR-A010)"
            )
        return default.id

    async def audit_auth_failure(
        self,
        *,
        tenant_id: str,
        method: str,
        reason: str,
        request_id: str = "",
        trace_id: str = "",
    ) -> None:
        """closure TASK-005：验证失败进 AuditLog；token/签名不入审计载荷。"""
        del trace_id  # 审计按 request 关联；token/签名/trace 不落敏感载荷
        await self._store.append_audit(
            AuditRecord(
                audit_id=f"audit_{uuid4().hex}",
                tenant_id=tenant_id,
                actor_id="unknown",
                request_id=request_id,
                action="channel.auth.rejected",
                target_type="channel_identity",
                target_id=method,
                before=None,
                after={"method": method, "reason": reason},
                created_at=self._clock(),
            )
        )

def _hash_access_token(token: str) -> str:
    return hashlib.sha256(token.strip().encode("utf-8")).hexdigest()


def _command_result(
    outcome: ImmediateReply,
    platform_user_id: str | None,
    request_id: str,
    trace_id: str,
) -> ChannelResult:
    """ImmediateReply → ChannelResult（§20 命令返回协议）。"""
    return ChannelResult(
        kind="command",
        output=outcome.message,
        platform_user_id=platform_user_id,
        request_id=request_id,
        trace_id=trace_id,
        command=outcome.command or None,
        code=outcome.code,
        data=dict(outcome.data),
    )


_EXECUTED_CODES = frozenset({"ok", "bound", "stop_requested", "stop_already_requested"})
"""审计动作为 executed 的命令结果码；其余为 rejected。"""


def _skill_directive(outcome: CommandOutcome) -> str | None:
    """RuntimeInvocation 携带的 skill id（审计用，不含 prompt）。"""
    if not isinstance(outcome, RuntimeInvocation):
        return None
    directive = outcome.directive
    if directive is None or getattr(directive, "kind", None) != "skill":
        return None
    capability_id = getattr(directive, "capability_id", "")
    return str(capability_id) if capability_id else None
