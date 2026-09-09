"""命令帮助文本格式化。`/help` 内容唯一来源：Registry（§15）。"""

from __future__ import annotations

from fluxion.commands.contracts import CommandHandler


def format_help(handlers: list[CommandHandler]) -> str:
    """按注册顺序渲染可用命令列表（Markdown，Chat/IM 均可渲染）。"""
    lines = ["**可用命令**", ""]
    for handler in handlers:
        descriptor = handler.descriptor
        lines.append(f"- `{descriptor.usage}` — {descriptor.description}")
    return "\n".join(lines)
