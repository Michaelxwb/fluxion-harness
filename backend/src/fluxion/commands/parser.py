"""Slash Command 解析（设计 §8）：纯语法层，不做任何语义判定。

- 去除前导空白后首字符 `/` 才进入命令解析；
- `//` 为转义：原文去掉一个 `/` 后作为普通消息；
- 命令名大小写敏感（不归一化，`/NEW` 由 Dispatcher 判 unknown）；
- 未知命令同样解析为 command（fail-closed 在 Dispatcher 执行）；
- command name 超长（§25.7：≤32）视为普通消息——它不可能是合法命令。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from fluxion.commands.contracts import CommandInvocation

MAX_COMMAND_NAME_LEN = 32


@dataclass(frozen=True, slots=True)
class ParsedInput:
    kind: Literal["command", "message"]
    invocation: CommandInvocation | None = None
    # kind == "message" 时：送入 Runtime 的原文（`//new` 转义后为 `/new`）。
    literal_text: str = ""


def parse_command(content: str) -> ParsedInput:
    """解析用户输入。纯函数，无 I/O、无副作用。"""
    text = content.lstrip()
    if not text.startswith("/"):
        return ParsedInput(kind="message", literal_text=content)
    if text.startswith("//"):
        return ParsedInput(kind="message", literal_text=text[1:])
    body = text[1:]
    if not body or body[0].isspace():
        return ParsedInput(kind="message", literal_text=content)
    parts = body.split(None, 1)
    name = parts[0]
    if len(name) > MAX_COMMAND_NAME_LEN:
        return ParsedInput(kind="message", literal_text=content)
    remainder = parts[1] if len(parts) > 1 else ""
    return ParsedInput(
        kind="command",
        invocation=CommandInvocation(
            name=name,
            raw=text,
            args=tuple(remainder.split()),
            remainder=remainder,
        ),
        literal_text=text,
    )


def is_bind_command(content: str) -> bool:
    """匿名 Web 入口前置放行判定：仅 `/bind` 命令允许无凭据进入。

    替代 `services.channel_app.is_bind_command` 旧实现（特判路径已删除，
    语义由 Parser 统一承载）。
    """
    parsed = parse_command(content)
    return parsed.kind == "command" and parsed.invocation is not None and parsed.invocation.name == "bind"
