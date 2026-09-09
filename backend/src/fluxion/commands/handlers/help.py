"""HelpCommand：可用命令列表（设计 §15）。

内容必须由 Registry 自动生成，不得维护第二份硬编码帮助文本；
展示范围按 CommandContext 过滤（未绑定仅 bind、Web 不展示 bind）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

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
from fluxion.commands.errors import OK
from fluxion.commands.formatting import format_help

if TYPE_CHECKING:
    from fluxion.commands.registry import CommandRegistry


class HelpCommandHandler(CommandHandler):
    """`/help`：展示当前上下文允许使用的命令。"""

    def __init__(self, registry: CommandRegistry) -> None:
        self._registry = registry
        self._descriptor = CommandDescriptor(
            name="help",
            usage="/help",
            description="查看可用命令",
            auth_scope=CommandAuthScope.AUTHENTICATED,
            channel_scope=CommandChannelScope.ALL,
            category=CommandCategory.HELP,
        )

    @property
    def descriptor(self) -> CommandDescriptor:
        return self._descriptor

    async def execute(
        self,
        context: CommandContext,
        invocation: CommandInvocation,
    ) -> CommandOutcome:
        del invocation
        handlers = self._registry.visible_commands(context)
        return ImmediateReply(command="help", code=OK, message=format_help(handlers))
