"""StopCommand：停止当前会话正在执行的 Execution（设计 §11.8）。

业务级取消（≠ 关闭 SSE）：PG 持久化 CANCELLING + 本实例立即取消。
只停止"当前用户 + 当前 Agent + 当前 active session"的 execution，
不接受任意 execution_id（§25.4）。
"""

from __future__ import annotations

from typing import Protocol

from fluxion.commands.contracts import (
    CommandAuthScope,
    CommandCategory,
    CommandChannelScope,
    CommandContext,
    CommandDescriptor,
    CommandHandler,
    CommandInvocation,
    CommandOutcome,
    ImmediateReply,
)
from fluxion.commands.errors import (
    COMMAND_USAGE_INVALID,
    NOTHING_TO_STOP,
    STOP_ALREADY_REQUESTED,
    STOP_REQUESTED,
)
from fluxion.services.chat_session_service import ChatSessionService


class ControlGateway(Protocol):
    """命令层所需的 Runtime 控制面（§17）：只读状态 + 取消，不碰执行。"""

    async def cancel_active_execution(
        self,
        *,
        tenant_id: str,
        user_id: str,
        agent_definition_id: str,
        session_id: str,
    ) -> object: ...


class StopCommandHandler(CommandHandler):
    """/stop：停止当前会话正在运行的任务。"""

    def __init__(self, sessions: ChatSessionService, gateway: ControlGateway) -> None:
        self._sessions = sessions
        self._gateway = gateway
        self._descriptor = CommandDescriptor(
            name="stop",
            usage="/stop",
            description="停止当前任务",
            auth_scope=CommandAuthScope.AUTHENTICATED,
            channel_scope=CommandChannelScope.ALL,
            category=CommandCategory.EXECUTION,
        )

    @property
    def descriptor(self) -> CommandDescriptor:
        return self._descriptor

    async def execute(
        self,
        context: CommandContext,
        invocation: CommandInvocation,
    ) -> CommandOutcome:
        if invocation.args:
            return ImmediateReply(command="stop", code=COMMAND_USAGE_INVALID, message="用法：/stop")
        if context.platform_user_id is None:
            return ImmediateReply(
                command="stop", code=COMMAND_USAGE_INVALID, message="当前上下文无法停止任务"
            )
        session_id = await self._sessions.resolve_session(
            context.tenant_id,
            context.channel_type,
            context.external_conversation_id,
            context.platform_user_id,
            context.agent_id,
        )
        result = await self._gateway.cancel_active_execution(
            tenant_id=context.tenant_id,
            user_id=context.platform_user_id,
            agent_definition_id=context.agent_id,
            session_id=session_id,
        )
        code = getattr(result, "code", NOTHING_TO_STOP)
        if code == STOP_REQUESTED:
            return ImmediateReply(
                command="stop",
                code=code,
                message="已请求停止当前任务",
                data={"session_id": session_id},
            )
        if code == STOP_ALREADY_REQUESTED:
            return ImmediateReply(command="stop", code=code, message="停止请求已发送，正在取消")
        return ImmediateReply(command="stop", code=NOTHING_TO_STOP, message="当前没有正在运行的任务")
