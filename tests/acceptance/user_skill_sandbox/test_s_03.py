"""[S-03] 草稿 Shell/受限执行通过 session-manager 获取 mssp state，执行结束删除。

真实边界：Runtime broker → Console 凭据解析响应（记录桩校验服务身份/租户/actor）→
真实 Redis Session → 真实 mssp adapter 登录 → Unix socket → 真实 `session-manager`
CLI → 执行专属 state 文件。未授权平台必须返回 PLATFORM_SESSION_REQUIRED。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from muad_agent_core.skill import ScriptSkillExecutor, SkillExecutionError, SkillExecutionRequest
from muad_agent_runtime.application.skill_tools import DraftShellContext, SkillToolSet

from tests.acceptance.user_skill_sandbox.draft_shell_support import (
    MemoryDraftStore,
    build_runner,
    draft_zip,
)
from tests.acceptance.user_skill_sandbox.session_support import (
    SessionChain,
    legacy_probe_script,
    open_session_chain,
    write_skill_package,
)

pytestmark = pytest.mark.integration
SKILL_NAME = "mssp-session-probe"


@pytest.fixture()
async def chain(request: pytest.FixtureRequest):
    try:
        async with open_session_chain() as value:
            yield value
    except RuntimeError as exc:  # pragma: no cover - 环境前提
        pytest.skip(str(exc))


def _package(tmp_path: Path, chain: SessionChain, platform: str | None = None) -> Path:
    key = platform or chain.platform_key
    return write_skill_package(
        tmp_path / ("skill-" + key),
        name=SKILL_NAME,
        platform=key,
        main=legacy_probe_script(SKILL_NAME, key),
    )


async def test_s03_session_state_file_and_legacy_stdout(tmp_path: Path, chain: SessionChain) -> None:
    """[S-03] sessionStateFile + platforms list；cookies/requestProfile 可读；结束删除。"""
    root = _package(tmp_path, chain)
    result = await ScriptSkillExecutor().execute(
        SkillExecutionRequest(ready_dir=root, input={}, timeout_sec=30.0, platform_context=chain.context)
    )
    assert result.exit_code == 0, result.stderr
    payload = json.loads(result.stdout)

    assert payload["path"] and not payload["path"].startswith("/proc/")
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

    assert not Path(payload["path"]).exists(), "state file must be deleted after execution"


async def test_s03_unauthorized_platform_returns_platform_session_required(
    tmp_path: Path,
) -> None:
    """[S-03] 未授权平台：Console 拒绝，执行返回 PLATFORM_SESSION_REQUIRED。"""
    async with open_session_chain() as chain:
        root = _package(tmp_path, chain, platform="mssw")
        context = replace(chain.context, platform_keys=("mssw",))
        with pytest.raises(SkillExecutionError) as captured:
            await ScriptSkillExecutor().execute(
                SkillExecutionRequest(ready_dir=root, input={}, timeout_sec=30.0, platform_context=context)
            )
    assert captured.value.code == "PLATFORM_SESSION_REQUIRED"


def _dev_unsafe_runner_factory():
    from muad_agent_core.sandbox import DevUnsafeRunner

    return DevUnsafeRunner()


def _draft_toolset(
    chain: SessionChain,
    *,
    store: MemoryDraftStore,
    actor_user_id: str,
) -> SkillToolSet:
    def platform_context_factory(request, keys):
        return replace(
            chain.context,
            execution_id=request.execution_id,
            execution_type="DRAFT_SHELL",
            platform_keys=tuple(keys),
        )

    runner = build_runner(
        store,
        runner_factory=_dev_unsafe_runner_factory,
        platform_context_factory=platform_context_factory,
    )
    return SkillToolSet(
        cache=object(),  # type: ignore[arg-type] - 草稿 Shell 不读 artifact cache
        skills=(),
        timeout_sec=60.0,
        draft_shell=runner,
        draft_shell_context=DraftShellContext(
            tenant_id=chain.tenant_id,
            owner_user_id=actor_user_id,
            agent_id=str(uuid.uuid4()),
            run_id=str(uuid.uuid4()),
        ),
    )


async def _call_draft_shell(toolset: SkillToolSet, *, command: str) -> dict[str, Any]:
    return json.loads(
        await toolset.execute(
            {"target": "draft", "draft_id": str(uuid.uuid4()), "command": command},
            call_id="call-s03",
        )
    )


async def test_s03_draft_shell_gets_session_state_and_deletes_it() -> None:
    """[S-03] 草稿 Shell 通过真实 broker 读取 state 文件；执行结束文件删除。"""
    async with open_session_chain() as chain:
        skill_name = "draft-session-probe"
        store = MemoryDraftStore(
            draft_zip(
                {
                    "SKILL.md": (
                        "---\n"
                        f"name: {skill_name}\n"
                        "description: S-03 draft shell probe\n"
                        f"platforms: [{chain.platform_key}]\n"
                        "---\n"
                    ),
                    "scripts/probe.py": legacy_probe_script(skill_name, chain.platform_key),
                }
            )
        )
        toolset = _draft_toolset(chain, store=store, actor_user_id=chain.actor_user_id)
        payload = await _call_draft_shell(toolset, command="python scripts/probe.py")

        assert "error" not in payload, payload
        assert payload["exit_code"] == 0, payload
        probe = json.loads(payload["stdout"].strip().splitlines()[-1])
        assert probe["path"] and not probe["path"].startswith("/proc/")
        assert probe["platforms"] == [
            {
                "platform": chain.platform_key,
                "source": "session",
                "expiresAt": probe["expires_at"],
            }
        ]
        assert {"csrf_token", "soc-token"} <= set(probe["cookie_names"])
        assert probe["base_url"].startswith("http://127.0.0.1:")
        assert probe["socket"] is True
        assert not Path(probe["path"]).exists(), "state file must be deleted after execution"


async def test_s03_draft_shell_unauthorized_platform_returns_required() -> None:
    """[S-03] 草稿声明未授权平台时返回 PLATFORM_SESSION_REQUIRED。"""
    async with open_session_chain() as chain:
        skill_name = "draft-session-denied"
        store = MemoryDraftStore(
            draft_zip(
                {
                    "SKILL.md": (
                        "---\n"
                        f"name: {skill_name}\n"
                        "description: S-03 denied probe\n"
                        "platforms: [mssw]\n"
                        "---\n"
                    ),
                    "scripts/probe.py": "print('should not run')\n",
                }
            )
        )
        toolset = _draft_toolset(chain, store=store, actor_user_id=chain.actor_user_id)
        payload = await _call_draft_shell(toolset, command="python scripts/probe.py")

    assert payload["error"]["code"] == "PLATFORM_SESSION_REQUIRED", payload
