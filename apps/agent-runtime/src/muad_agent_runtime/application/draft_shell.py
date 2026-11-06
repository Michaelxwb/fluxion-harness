"""Runtime 侧 ``skill_draft_shell`` 装配：Settings → Console 客户端 → 沙箱草稿 Shell。"""

from __future__ import annotations

from muad_agent_core.sandbox import SandboxRunner, build_sandbox_runner_from_settings
from muad_agent_core.skill.draft_shell import DraftShellRequest, DraftShellRunner
from muad_common import SharedSettings, process_settings
from muad_platform_sdk.skill_access import SkillPlatformContext

from .skill_draft_client import ConsoleDraftShellClient

DRAFT_SHELL_EXECUTION_TYPE = "DRAFT_SHELL"


def build_draft_shell_runner(
    settings: SharedSettings | None = None,
    *,
    client: ConsoleDraftShellClient | None = None,
) -> DraftShellRunner:
    resolved = settings or process_settings()
    console = client or ConsoleDraftShellClient(
        resolved.console_platform_url,
        service_token=resolved.internal_service_token,
    )

    def runner_factory() -> SandboxRunner:
        return build_sandbox_runner_from_settings(resolved)

    def platform_context(
        request: DraftShellRequest, platform_keys: tuple[str, ...]
    ) -> SkillPlatformContext | None:
        if not platform_keys:
            return None
        return SkillPlatformContext(
            tenant_id=request.tenant_id,
            actor_user_id=request.owner_user_id,
            execution_id=request.execution_id,
            execution_type=DRAFT_SHELL_EXECUTION_TYPE,
            platform_keys=tuple(platform_keys),
            console_url=resolved.console_platform_url,
            redis_url=resolved.redis_url,
            redis_mode=resolved.redis_mode,
            redis_cluster_seeds=resolved.redis_cluster_seeds,
            service_token=resolved.internal_service_token,
        )

    return DraftShellRunner(
        store=console,
        runner_factory=runner_factory,
        max_file_bytes=resolved.skill_draft_file_max_bytes,
        max_total_bytes=resolved.skill_draft_quota_mib * 1024 * 1024,
        max_files=resolved.skill_draft_file_limit,
        output_limit_bytes=resolved.sandbox_output_limit_bytes,
        platform_context_factory=platform_context,
    )


__all__ = ["build_draft_shell_runner"]
