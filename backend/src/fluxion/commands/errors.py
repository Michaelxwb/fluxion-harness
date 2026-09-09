"""Agent Command Plane 错误码与边界异常（设计 §23）。

命令 Handler 一律返回 `ImmediateReply`（成功与业务拒绝同形），只有需要跨越
服务边界保持旧调用契约的失败才抛类型化异常（bind 兑换失败 → ChannelBindError，
`api/channel.py` 与既有测试依赖该异常映射）。
"""

from __future__ import annotations

OK = "ok"
UNKNOWN_COMMAND = "unknown_command"
COMMAND_USAGE_INVALID = "command_usage_invalid"
COMMAND_NOT_AVAILABLE = "command_not_available"
IDENTITY_BINDING_REQUIRED = "identity_binding_required"
ALREADY_BOUND = "already_bound"
BOUND = "bound"
SESSION_BUSY = "session_busy"
NEW_SESSION_CONFLICT = "new_session_conflict"
NOTHING_TO_STOP = "nothing_to_stop"
STOP_REQUESTED = "stop_requested"
STOP_ALREADY_REQUESTED = "stop_already_requested"
SKILL_NOT_FOUND = "skill_not_found"
SKILL_NOT_AVAILABLE = "skill_not_available"
SKILL_PROMPT_REQUIRED = "skill_prompt_required"
EXECUTION_CONTROL_CONFLICT = "execution_control_conflict"


class CommandRegistryError(ValueError):
    """命令注册期校验失败（fail-fast，只在 composition root 抛出）。"""

    code = "command_registry_invalid"


class ChannelBindError(RuntimeError):
    """绑定码兑换失败（边界异常：保持 `services.channel_app` 旧契约）。

    由 BindCommand 抛出，`api/channel.py` 映射为绑定失败响应。
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__("绑定码无效或已过期")
