"""Command Plane 契约（设计 §7）：命令是 Harness 控制协议，不是 Prompt。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from fluxion.services.runtime_contracts import InvocationDirective


class CommandAuthScope(StrEnum):
    """命令认证域。"""

    ANONYMOUS = "anonymous"  # 未绑定也可执行（仅 /bind）
    AUTHENTICATED = "authenticated"


class CommandChannelScope(StrEnum):
    """命令通道域。"""

    ALL = "all"
    IM_ONLY = "im_only"  # 仅 IM 通道（如 /bind）；Web Chat 不展示


class CommandCategory(StrEnum):
    """命令分组（/help 分区展示用）。"""

    IDENTITY = "identity"
    SESSION = "session"
    EXECUTION = "execution"
    CAPABILITY = "capability"
    HELP = "help"


@dataclass(frozen=True, slots=True)
class CommandDescriptor:
    name: str
    usage: str
    description: str
    auth_scope: CommandAuthScope
    channel_scope: CommandChannelScope
    category: CommandCategory


@dataclass(frozen=True, slots=True)
class CommandContext:
    """Handler 可见的全部上下文：只含已解析身份，不接触外部原始身份。"""

    tenant_id: str
    platform_user_id: str | None
    agent_id: str
    ingress_type: str  # "web_chat" | "channel"
    channel_type: str  # adapter 类型，如 web / stub-im / mattermost
    channel_user_id: str | None  # IM 通道原始用户（bind 兑换用）
    external_conversation_id: str
    active_session_id: str | None  # TASK-003 前为 None（沿用 conversation 直传）
    request_id: str
    trace_id: str
    authenticated: bool


@dataclass(frozen=True, slots=True)
class CommandInvocation:
    name: str
    raw: str
    args: tuple[str, ...]
    remainder: str  # 命令名之后原始文本（去前导分隔空白，内部 spacing 保留）


@dataclass(frozen=True, slots=True)
class ImmediateReply:
    """控制层即时完成（/help、/bind、拒绝等），不进 Runtime。"""

    code: str
    message: str
    data: Mapping[str, object] = field(default_factory=dict)
    command: str = ""  # 由 Dispatcher 回填命令名（§20 返回协议）


@dataclass(frozen=True, slots=True)
class RuntimeInvocation:
    """命令最终仍需执行 Agent（/skill、普通消息），复用 Runtime 路径。"""

    prompt: str
    directive: InvocationDirective | None = None  # /skill 显式意图（版本由 Snapshot 固化）  # TASK-004 接 InvocationDirective


CommandOutcome = ImmediateReply | RuntimeInvocation


class CommandHandler(Protocol):
    """命令处理器契约：纯意图解析 + 控制决策，最终安全决策仍在领域层。"""

    @property
    def descriptor(self) -> CommandDescriptor: ...

    async def execute(
        self,
        context: CommandContext,
        invocation: CommandInvocation,
    ) -> CommandOutcome: ...
