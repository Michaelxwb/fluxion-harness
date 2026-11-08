"""[TASK-004] session-manager 获取登录态：真实 Redis、平台 adapter、Unix socket 与 bwrap。

真实边界：Runtime 执行器 → `skill_platform_environment` broker → Console 凭据解析响应
（记录桩校验服务身份/租户/actor）→ 真实 Redis Session → 真实平台 adapter 登录 →
真实 Unix socket → 真实 `session-manager` CLI → state 文件；bwrap 场景由
`muad-skill-executor-probe` 镜像中的真实 `BwrapRunner` 执行。
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from muad_agent_core.sandbox import (
    SandboxPolicy,
    SandboxResult,
    WorkspaceLayout,
    WorkspaceLeaseRecord,
    WorkspaceShellRequest,
    WorkspaceShellRunner,
    WorkspaceStore,
)
from muad_agent_core.skill import (
    ScriptSkillExecutor,
    SkillExecutionError,
    SkillExecutionRequest,
)

from tests.acceptance.user_skill_sandbox.sandboxed_support import run_probe
from tests.acceptance.user_skill_sandbox.session_support import (
    SessionChain,
    legacy_probe_script,
    open_session_chain,
    write_skill_package,
)

pytestmark = pytest.mark.integration
SKILL_NAME = "mssp-session-probe"


@pytest.fixture()
async def chain() -> AsyncIterator[SessionChain]:
    try:
        async with open_session_chain() as value:
            yield value
    except RuntimeError as exc:  # pragma: no cover - 环境前提
        pytest.skip(str(exc))


async def _execute_legacy_skill(tmp_path: Path, chain: SessionChain) -> Any:
    root = write_skill_package(
        tmp_path / "skill",
        name=SKILL_NAME,
        platform=chain.platform_key,
        main=legacy_probe_script(SKILL_NAME, chain.platform_key),
    )
    return await ScriptSkillExecutor().execute(
        SkillExecutionRequest(
            ready_dir=root,
            input={},
            timeout_sec=30.0,
            platform_context=chain.context,
        )
    )


async def test_session_manager_state_is_bound_to_execution_and_deleted(
    tmp_path: Path, chain: SessionChain
) -> None:
    """get-state 返回旧 stdout 契约；state 文件真实可读，执行结束删除。"""
    result = await _execute_legacy_skill(tmp_path, chain)
    assert result.exit_code == 0, result.stderr
    payload = json.loads(result.stdout)

    state_path = Path(payload["path"])
    assert not str(state_path).startswith("/proc/")
    assert state_path.parent.name == "state"
    assert payload["platforms"] == [
        {
            "platform": chain.platform_key,
            "source": "session",
            "expiresAt": payload["expires_at"],
        }
    ]
    assert payload["expires_at"]
    assert {"csrf_token", "soc-token"} <= set(payload["cookie_names"])
    assert payload["base_url"].startswith("http://127.0.0.1:")
    assert payload["socket"] is True

    # 执行结束（broker context 退出）后执行专属 state 文件删除。
    assert not state_path.exists()

    # RULE-platform-001：真实 Redis 会话语义不变（键/tag/TTL），凭据不落缓存。
    keys = await chain.redis.smembers(chain.manager.index)
    assert len(keys) == 1
    key = next(iter(keys)).decode()
    assert key.startswith(f"muad:platform_session:{{{chain.tenant_id}:{chain.platform_id}}}:")
    assert 7198 <= await chain.redis.ttl(key) <= 7200
    stored = await chain.redis.get(key)
    cached = json.loads(stored)
    assert cached.get("credential") is None and "credential_json" not in stored.decode()


async def test_unauthorized_platform_yields_platform_session_required(tmp_path: Path) -> None:
    """受信上下文之外的平台 key：Console 拒绝 → PLATFORM_SESSION_REQUIRED。"""
    async with open_session_chain() as chain:
        root = write_skill_package(
            tmp_path / "skill",
            name=SKILL_NAME,
            platform="mssw",
            main=legacy_probe_script(SKILL_NAME, "mssw"),
        )
        context = replace(chain.context, platform_keys=("mssw",))
        with pytest.raises(SkillExecutionError) as captured:
            await ScriptSkillExecutor().execute(
                SkillExecutionRequest(
                    ready_dir=root,
                    input={},
                    timeout_sec=30.0,
                    platform_context=context,
                )
            )
    assert captured.value.code == "PLATFORM_SESSION_REQUIRED"


async def test_workspace_shell_never_receives_session_manager(tmp_path: Path) -> None:
    """workspace_shell 的 spec 不带 socket/session PATH（与 E-05 的真实 bwrap 断言一致）。"""
    recorded: list[Any] = []

    class _Metadata:
        async def acquire(self, **kwargs: Any) -> WorkspaceLeaseRecord:
            return WorkspaceLeaseRecord(
                id=uuid.uuid4(),
                workspace_id=uuid.uuid4(),
                execution_id=kwargs["execution_id"],
                lease_until=datetime.now(UTC) + timedelta(minutes=5),
            )

        async def release(self, lease_id: uuid.UUID) -> None:
            return None

    class _Runner:
        name = "recording"

        async def run(self, spec: Any) -> SandboxResult:
            recorded.append(spec)
            return SandboxResult(exit_code=0, stdout="ok", stderr="", duration_ms=1)

    store = WorkspaceStore(
        layout=WorkspaceLayout(
            root=tmp_path / "workspaces",
            quota_bytes=1024**3,
            user_quota_bytes=1024**3,
            file_limit=100,
            data_ttl_seconds=86400,
        ),
        metadata=_Metadata(),
    )
    runner = WorkspaceShellRunner(
        store=store,
        runner_factory=lambda: _Runner(),
        policy=SandboxPolicy(),
    )
    await runner.run(
        WorkspaceShellRequest(
            tenant_id="tenant",
            user_id="user",
            agent_id="agent",
            execution_id="shell-exec",
            command=("true",),
        )
    )
    spec = recorded[0]
    assert "MUAD_SESSION_SOCKET" not in spec.env
    assert not any("fx-session" in value for value in spec.env.values())
    assert spec.mounts == ()
    assert "session-manager" not in spec.env.get("PATH", "")


def test_sandboxed_executor_exposes_session_manager_inside_bwrap(chain: SessionChain) -> None:
    """真实 bwrap：PATH/socket/只读 state 文件可读，state 路径不在 /proc。"""
    payload = run_probe(
        "s03",
        env={
            "FX_SESSION_TENANT": chain.tenant_id,
            "FX_SESSION_ACTOR": chain.actor_user_id,
            "FX_SESSION_CONSOLE_URL": chain.container_console_url,
            "FX_SESSION_REDIS_URL": chain.container_redis_url,
            "FX_SESSION_SERVICE_TOKEN": chain.service_token,
            "FX_SESSION_PLATFORM": chain.platform_key,
            "FX_SESSION_SKILL": SKILL_NAME,
        },
        docker_args=("--add-host", "host.docker.internal:host-gateway"),
    )
    assert payload["ok"] is True, payload
    result = payload["result"]
    assert result["status"] == "SUCCEEDED", result
    assert result["exit_code"] == 0
    probe = result["result"]
    assert probe["socket"] is True
    assert probe["path_has_session"] is True
    assert probe["state_path"].startswith("/tmp/fx-session/")
    assert probe["proc_path"] is False
    assert probe["bin_sh"] is True
    assert probe["platforms"] == [
        {
            "platform": chain.platform_key,
            "source": "session",
            "expiresAt": probe["expires_at"],
        }
    ]
    assert {"csrf_token", "soc-token"} <= set(probe["cookie_names"])
    assert payload["leftover_session_dirs"] == []
