from __future__ import annotations

from dataclasses import dataclass

SUCCESS = 0
VALIDATION_FAILED = 30_001
RESOURCE_NOT_FOUND = 31_004
RESOURCE_CONFLICT = 31_009
BINDING_VALIDATION_FAILED = 32_001
BINDING_CONFLICT = 32_002
VERSION_CONFLICT = 33_009
FORBIDDEN = 35_003
INTERNAL_ERROR = 39_001
# RuntimeApplicationError.code 是字符串 slug（如 resource_version_not_found），
# 无独立整数码表；统一映射到此码，slug 保留在 envelope message 中以便追溯。
RUNTIME_APPLICATION_ERROR = 40_001

# Channel API 错误码（集中定义，禁止在 handler 内硬编码——F9）。
# 命名空间漂移待对齐：契约命名空间表规定 34xxx = Identity/Bind/Channel，而
# 此处实际占 36xxx（Workflow/Capability 引用段）；为避免破坏既有 wire 契约
# 与 test_dev_identity_security 的 36_003 断言，暂保留原数字，命名空间对齐
# 作为后续契约重构项。
CHANNEL_BIND_FAILED = 36_001
CHANNEL_VALIDATION_FAILED = 36_002
CHANNEL_ACCESS_DENIED = 36_003
CHANNEL_AUTH_DENIED = 36_004

# Eval API 错误码（集中定义——F9）。37xxx 未在契约命名空间表内定义；命名空间
# 扩展（如 38xxx = Eval/Telemetry）作为后续契约对齐项。
EVAL_INTERNAL_ERROR = 37_000
EVAL_VALIDATION_FAILED = 37_001
EVAL_TRACEABILITY_ERROR = 37_002
EVAL_EXECUTION_ERROR = 37_003
# Phase 5 TASK-005：Release Gate 阻断（38xxx = Eval/Release Gate 段）。
RELEASE_GATE_BLOCKED = 38_001

# User Domain / Identity 段（TASK-007；契约命名空间 34xxx = Identity/Bind/Channel）。
USER_NOT_FOUND = 34_100
USER_NOT_BOUND = 34_101
CHANNEL_AGENT_NOT_FOUND = 34_102
# H1：Console 非 dev 模式身份头缺失（X-Tenant-ID/X-Actor-ID），401 fail-closed。
IDENTITY_HEADER_MISSING = 34_103

# Studio/Product API 错误码（TASK-004；42xxx = Studio/Product API 段）。
# slug 形态保留在 envelope message 前缀（如 agent_definition_invalid），
# 版本冲突不另设 studio 段码——复用 VERSION_CONFLICT/RESOURCE_CONFLICT。
STUDIO_SPEC_INVALID = 42_201


@dataclass(frozen=True, slots=True)
class ConsoleError(Exception):
    code: int
    message: str
    status_code: int

    def __str__(self) -> str:
        return self.message


class StudioSpecValidationError(ConsoleError):
    """Studio/Product API 的 spec 前置校验失败（typed model 字段定位）。"""

    def __init__(self, message: str = "studio spec invalid") -> None:
        super().__init__(STUDIO_SPEC_INVALID, message, 422)


class ConsoleValidationError(ConsoleError):
    def __init__(self, message: str = "validation failed") -> None:
        super().__init__(VALIDATION_FAILED, message, 400)


class ConsoleResourceNotFoundError(ConsoleError):
    def __init__(self, message: str = "resource not found") -> None:
        super().__init__(RESOURCE_NOT_FOUND, message, 404)


class ConsoleResourceConflictError(ConsoleError):
    def __init__(self, message: str = "resource conflict") -> None:
        super().__init__(RESOURCE_CONFLICT, message, 409)


class ConsoleBindingValidationError(ConsoleError):
    def __init__(self, message: str = "binding validation failed") -> None:
        super().__init__(BINDING_VALIDATION_FAILED, message, 400)


class ConsoleBindingConflictError(ConsoleError):
    def __init__(self, message: str = "binding conflict") -> None:
        super().__init__(BINDING_CONFLICT, message, 409)


class ConsoleVersionConflictError(ConsoleError):
    def __init__(self, message: str = "version conflict") -> None:
        super().__init__(VERSION_CONFLICT, message, 409)


class ConsoleForbiddenError(ConsoleError):
    def __init__(self, message: str = "forbidden") -> None:
        super().__init__(FORBIDDEN, message, 403)


class ConsoleInternalError(ConsoleError):
    def __init__(self, message: str = "internal error") -> None:
        super().__init__(INTERNAL_ERROR, message, 500)
