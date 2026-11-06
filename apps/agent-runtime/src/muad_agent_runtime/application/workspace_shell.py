"""Runtime 侧 ``workspace_shell`` 装配：Settings → WorkspaceStore → 沙箱 runner。

组合根只做三件事：把 ``WORKSPACE_*`` 投影成路径预算、把 PG 元数据适配器接到
``WorkspaceStore``、把沙箱模式投影成 policy。真实 bwrap 探测延迟到首次工具调用，
避免启动时在 macOS/dev 环境直接失败；不可用时按 ``SANDBOX_UNAVAILABLE`` fail closed。
"""

from __future__ import annotations

from muad_agent_core.sandbox import (
    WORKSPACE_MOUNT,
    SandboxPolicy,
    SandboxProfile,
    SandboxRunner,
    WorkspaceShellRunner,
    WorkspaceStore,
    build_sandbox_runner_from_settings,
    workspace_layout_from_settings,
)
from muad_common import SharedSettings, process_settings

from ..infrastructure.db import get_session_factory
from ..infrastructure.workspace_store import SessionFactoryProvider, SqlWorkspaceMetadataStore


def build_workspace_shell_runner(
    settings: SharedSettings | None = None,
    *,
    session_factory_provider: SessionFactoryProvider = get_session_factory,
) -> WorkspaceShellRunner:
    resolved = settings or process_settings()
    layout = workspace_layout_from_settings(resolved)
    store = WorkspaceStore(
        layout=layout,
        metadata=SqlWorkspaceMetadataStore(session_factory_provider),
    )
    # dev-unsafe 是显式开发模式：runner 直接跑宿主进程，因此注入宿主可见的真实路径；
    # 生产 bwrap 一律只暴露 /workspace，绝不把 workspace 根或宿主路径带进沙箱。
    dev_unsafe = resolved.env == "dev" and resolved.sandbox_mode == "dev-unsafe"
    policy = SandboxPolicy(
        profile=SandboxProfile.UNTRUSTED,
        network=True,
        dev_unsafe=dev_unsafe,
    )
    cached: list[SandboxRunner] = []

    def runner_factory() -> SandboxRunner:
        if not cached:
            cached.append(build_sandbox_runner_from_settings(resolved))
        return cached[0]

    return WorkspaceShellRunner(
        store=store,
        runner_factory=runner_factory,
        policy=policy,
        sandbox_mount=None if dev_unsafe else WORKSPACE_MOUNT,
        output_limit_bytes=resolved.sandbox_output_limit_bytes,
    )


__all__ = ["build_workspace_shell_runner"]
