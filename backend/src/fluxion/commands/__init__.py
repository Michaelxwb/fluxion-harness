"""Agent Command Plane：Harness 控制协议（命令），不是 Prompt。

详见 `docs/adr/ADR-A017-Agent-Command-Plane契约.md`。
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
    RuntimeInvocation,
)
from fluxion.commands.dispatcher import dispatch
from fluxion.commands.errors import (
    ALREADY_BOUND,
    BOUND,
    COMMAND_NOT_AVAILABLE,
    COMMAND_USAGE_INVALID,
    EXECUTION_CONTROL_CONFLICT,
    IDENTITY_BINDING_REQUIRED,
    NEW_SESSION_CONFLICT,
    OK,
    UNKNOWN_COMMAND,
    ChannelBindError,
    CommandRegistryError,
)
from fluxion.commands.parser import (
    MAX_COMMAND_NAME_LEN,
    ParsedInput,
    is_bind_command,
    parse_command,
)
from fluxion.commands.registry import CommandRegistry

__all__ = [
    "ALREADY_BOUND",
    "BOUND",
    "COMMAND_NOT_AVAILABLE",
    "COMMAND_USAGE_INVALID",
    "EXECUTION_CONTROL_CONFLICT",
    "IDENTITY_BINDING_REQUIRED",
    "NEW_SESSION_CONFLICT",
    "MAX_COMMAND_NAME_LEN",
    "OK",
    "UNKNOWN_COMMAND",
    "ChannelBindError",
    "CommandAuthScope",
    "CommandCategory",
    "CommandChannelScope",
    "CommandContext",
    "CommandDescriptor",
    "CommandHandler",
    "CommandInvocation",
    "CommandOutcome",
    "CommandRegistry",
    "CommandRegistryError",
    "ImmediateReply",
    "ParsedInput",
    "RuntimeInvocation",
    "dispatch",
    "is_bind_command",
    "parse_command",
]
