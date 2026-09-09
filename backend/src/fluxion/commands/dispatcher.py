"""CommandDispatcher：命令解析与命令执行解耦后的执行入口（设计 §4.2/§7）。

只做路由与 scope 门禁；最终安全决策（如 /skill 授权）在领域层完成，
本层不得构造"已授权"结果。
"""

from __future__ import annotations

from dataclasses import replace

from fluxion.commands.contracts import (
    CommandAuthScope,
    CommandChannelScope,
    CommandContext,
    CommandInvocation,
    CommandOutcome,
    ImmediateReply,
)
from fluxion.commands.errors import (
    COMMAND_NOT_AVAILABLE,
    IDENTITY_BINDING_REQUIRED,
    UNKNOWN_COMMAND,
)
from fluxion.commands.registry import CommandRegistry


async def dispatch(
    registry: CommandRegistry,
    context: CommandContext,
    invocation: CommandInvocation,
) -> CommandOutcome:
    """分发命令。未知命令 fail-closed，永不 fallback 为普通 Prompt。"""
    handler = registry.get(invocation.name)
    if handler is None:
        return ImmediateReply(
            command=invocation.name,
            code=UNKNOWN_COMMAND,
            message=f"未知命令 /{invocation.name}，发送 /help 查看可用命令",
        )
    descriptor = handler.descriptor
    if (
        descriptor.auth_scope is not CommandAuthScope.ANONYMOUS
        and not context.authenticated
    ):
        return ImmediateReply(
            command=invocation.name,
            code=IDENTITY_BINDING_REQUIRED,
            message="请先使用 /bind <code> 完成绑定",
        )
    if (
        descriptor.channel_scope is CommandChannelScope.IM_ONLY
        and context.channel_type == "web"
    ):
        return ImmediateReply(
            command=invocation.name,
            code=COMMAND_NOT_AVAILABLE,
            message=f"当前通道不支持 /{invocation.name}",
        )
    outcome = await handler.execute(context, invocation)
    if isinstance(outcome, ImmediateReply) and not outcome.command:
        return replace(outcome, command=invocation.name)
    return outcome
