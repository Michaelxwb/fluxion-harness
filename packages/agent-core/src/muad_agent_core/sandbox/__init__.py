"""Skill / Shell 的统一沙箱边界。

对外只暴露受校验的 ``SandboxSpec``、``SandboxPolicy`` 和 ``SandboxRunner``；
平台选择、bwrap 探测与 dev-unsafe 开关都收口在 ``build_sandbox_runner``。
"""

from __future__ import annotations

from .bwrap import (
    MIN_BWRAP_VERSION,
    BwrapProbe,
    BwrapRunner,
    build_bwrap_argv,
    probe_bwrap,
)
from .factory import (
    SandboxEvent,
    SandboxMode,
    SandboxObserver,
    build_sandbox_runner,
    build_sandbox_runner_from_settings,
)
from .runner import (
    DevUnsafeRunner,
    DisabledRunner,
    SandboxResult,
    SandboxRunner,
)
from .spec import (
    SANDBOX_DEFAULT_PATH,
    SANDBOX_ENV_ALLOWLIST,
    SANDBOX_ENV_PREFIXES,
    SANDBOX_OUTPUT_LIMIT_BYTES,
    SANDBOX_TMPFS_BYTES,
    TERMINATE_GRACE_SEC,
    SandboxCancelled,
    SandboxError,
    SandboxLimits,
    SandboxMount,
    SandboxOutputLimit,
    SandboxPolicy,
    SandboxProfile,
    SandboxSpec,
    SandboxTimeout,
    SandboxUnavailable,
    sanitize_env,
)
from .workspace import WorkspaceLayout, workspace_layout_from_settings

__all__ = [
    "MIN_BWRAP_VERSION",
    "SANDBOX_DEFAULT_PATH",
    "SANDBOX_ENV_ALLOWLIST",
    "SANDBOX_ENV_PREFIXES",
    "SANDBOX_OUTPUT_LIMIT_BYTES",
    "SANDBOX_TMPFS_BYTES",
    "TERMINATE_GRACE_SEC",
    "BwrapProbe",
    "BwrapRunner",
    "DevUnsafeRunner",
    "DisabledRunner",
    "SandboxCancelled",
    "SandboxError",
    "SandboxEvent",
    "SandboxLimits",
    "SandboxMode",
    "SandboxMount",
    "SandboxObserver",
    "SandboxOutputLimit",
    "SandboxPolicy",
    "SandboxProfile",
    "SandboxResult",
    "SandboxRunner",
    "SandboxSpec",
    "SandboxTimeout",
    "SandboxUnavailable",
    "WorkspaceLayout",
    "build_bwrap_argv",
    "build_sandbox_runner",
    "build_sandbox_runner_from_settings",
    "probe_bwrap",
    "sanitize_env",
    "workspace_layout_from_settings",
]
