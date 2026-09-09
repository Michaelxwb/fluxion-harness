"""CommandRegistry：composition root 启动期一次性构建（设计 §7.6）。

禁止运行中经普通用户配置动态新增系统命令；`/help` 必须从本 Registry
自动生成，不得维护第二份硬编码帮助文本。
"""

from __future__ import annotations

from fluxion.commands.contracts import CommandContext, CommandHandler
from fluxion.commands.errors import CommandRegistryError


class CommandRegistry:
    """系统命令注册表（启动期构建，运行期只读）。"""

    def __init__(self) -> None:
        self._handlers: dict[str, CommandHandler] = {}

    def register(self, handler: CommandHandler) -> None:
        """注册命令。违反命名/唯一/usage 约束即 fail-fast。"""
        descriptor = handler.descriptor
        if not descriptor.name or not descriptor.name.isascii() or not descriptor.name.islower():
            raise CommandRegistryError(
                f"command name 必须为非空小写 ASCII：{descriptor.name!r}"
            )
        if not descriptor.usage.strip():
            raise CommandRegistryError(f"command usage 不允许为空：{descriptor.name!r}")
        if descriptor.name in self._handlers:
            raise CommandRegistryError(f"command name 重复注册：{descriptor.name!r}")
        self._handlers[descriptor.name] = handler

    def get(self, name: str) -> CommandHandler | None:
        """精确匹配（大小写敏感，`/NEW` 不命中 `new`）。"""
        return self._handlers.get(name)

    def visible_commands(self, context: CommandContext) -> list[CommandHandler]:
        """当前上下文允许使用的命令（`/help` 内容来源，§15）。"""
        from fluxion.commands.contracts import CommandAuthScope, CommandChannelScope

        visible: list[CommandHandler] = []
        for handler in self._handlers.values():
            descriptor = handler.descriptor
            if not context.authenticated:
                if descriptor.auth_scope is not CommandAuthScope.ANONYMOUS:
                    continue
            elif descriptor.auth_scope is CommandAuthScope.ANONYMOUS:
                # 已绑定用户不再需要 bind（调用则回 already_bound，不展示）。
                continue
            if (
                descriptor.channel_scope is CommandChannelScope.IM_ONLY
                and context.channel_type == "web"
            ):
                continue
            visible.append(handler)
        return visible
