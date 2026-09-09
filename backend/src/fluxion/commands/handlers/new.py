"""NewCommand：同一外部对话窗口开始全新的短期会话（设计 §10.6）。

只改变"当前外部会话 → active runtime session"指针；不删除任何长期数据。
旧 Session 仍在运行时拒绝（不隐式 Stop，避免 /new 承担取消副作用）。
"""

from __future__ import annotations

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
    EXECUTION_CONTROL_CONFLICT,
    NEW_SESSION_CONFLICT,
    OK,
)
from fluxion.services.chat_session_service import (
    ChatSessionService,
    SessionActivityProbe,
    SessionBusyError,
    SessionConflictError,
)


class NewCommandHandler(CommandHandler):
    """/new：原子轮换 active session。"""

    def __init__(
        self,
        sessions: ChatSessionService,
        has_active_execution: SessionActivityProbe | None = None,
    ) -> None:
        self._sessions = sessions
        self._has_active_execution = has_active_execution
        self._descriptor = CommandDescriptor(
            name="new",
            usage="/new",
            description="开始新的会话",
            auth_scope=CommandAuthScope.AUTHENTICATED,
            channel_scope=CommandChannelScope.ALL,
            category=CommandCategory.SESSION,
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
            return ImmediateReply(command="new", code=COMMAND_USAGE_INVALID, message="用法：/new")
        if context.platform_user_id is None:
            return ImmediateReply(
                command="new", code=COMMAND_USAGE_INVALID, message="当前上下文无法开启新会话"
            )
        try:
            session_id = await self._sessions.rotate_session(
                context.tenant_id,
                context.channel_type,
                context.external_conversation_id,
                context.platform_user_id,
                context.agent_id,
                request_id=context.request_id,
                has_active_execution=self._has_active_execution,
            )
        except SessionBusyError as exc:
            return ImmediateReply(command="new", code=NEW_SESSION_CONFLICT, message=str(exc))
        except SessionConflictError as exc:
            return ImmediateReply(
                command="new", code=EXECUTION_CONTROL_CONFLICT, message=str(exc)
            )
        return ImmediateReply(
            command="new",
            code=OK,
            message="已开始新会话",
            data={"session_id": session_id},
        )
