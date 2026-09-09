"""StatusCommand：查看当前 Agent/Session/Execution 状态（设计 §12）。

只读 Head + Control Store，不建 ExecutionSnapshot、不执行模型。
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
from fluxion.commands.errors import COMMAND_USAGE_INVALID, OK
from fluxion.services.chat_session_service import ChatSessionService


class StatusGateway(Protocol):
    """命令层所需的状态查询面（§17）。"""

    async def get_session_status(
        self,
        *,
        tenant_id: str,
        user_id: str,
        agent_definition_id: str,
        session_id: str,
    ) -> object: ...


class StatusCommandHandler(CommandHandler):
    """/status：查看当前状态。"""

    def __init__(self, sessions: ChatSessionService, gateway: StatusGateway) -> None:
        self._sessions = sessions
        self._gateway = gateway
        self._descriptor = CommandDescriptor(
            name="status",
            usage="/status",
            description="查看当前状态",
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
            return ImmediateReply(
                command="status", code=COMMAND_USAGE_INVALID, message="用法：/status"
            )
        if context.platform_user_id is None:
            return ImmediateReply(
                command="status", code=COMMAND_USAGE_INVALID, message="当前上下文无法查询状态"
            )
        session_id = await self._sessions.resolve_session(
            context.tenant_id,
            context.channel_type,
            context.external_conversation_id,
            context.platform_user_id,
            context.agent_id,
        )
        status = await self._gateway.get_session_status(
            tenant_id=context.tenant_id,
            user_id=context.platform_user_id,
            agent_definition_id=context.agent_id,
            session_id=session_id,
        )
        return ImmediateReply(
            command="status", code=OK, message=_format_status(context.agent_id, session_id, status)
        )


def _format_status(agent_id: str, session_id: str, status: object) -> str:
    """§12.1 输出格式：Markdown 排版（Chat/IM 均可渲染），状态汉化。"""
    state = str(getattr(status, "state", "idle") or "idle")
    lines = ["**当前状态**", "", f"- Agent：`{agent_id}`", f"- 会话：`{session_id}`"]
    if state == "idle":
        lines.append("- 状态：空闲")
        return "\n".join(lines)
    lines.append(f"- 状态：{_STATE_LABELS.get(state, state)}")
    execution_id = getattr(status, "execution_id", None)
    if execution_id:
        lines.append(f"- 任务：`{execution_id}`")
    started_at = getattr(status, "started_at", None)
    if started_at:
        lines.append(f"- 开始：{started_at}")
    skill_id = getattr(status, "requested_skill_id", None)
    if skill_id:
        lines.append(f"- Skill：`{skill_id}`")
    return "\n".join(lines)


_STATE_LABELS = {
    "idle": "空闲",
    "created": "创建中",
    "running": "运行中",
    "cancelling": "取消中",
    "completed": "已完成",
    "failed": "失败",
    "cancelled": "已取消",
    "timed_out": "超时",
}
