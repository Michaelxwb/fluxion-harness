"""[S-03] 草稿 Shell/受限执行通过 session-manager 获取 mssp state，执行结束删除。

真实边界：Runtime broker → Console 凭据解析响应（记录桩校验服务身份/租户/actor）→
真实 Redis Session → 真实 mssp adapter 登录 → Unix socket → 真实 `session-manager`
CLI → 执行专属 state 文件。未授权平台必须返回 PLATFORM_SESSION_REQUIRED。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from muad_agent_core.skill import ScriptSkillExecutor, SkillExecutionError, SkillExecutionRequest

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
        from dataclasses import replace

        context = replace(chain.context, platform_keys=("mssw",))
        with pytest.raises(SkillExecutionError) as captured:
            await ScriptSkillExecutor().execute(
                SkillExecutionRequest(ready_dir=root, input={}, timeout_sec=30.0, platform_context=context)
            )
    assert captured.value.code == "PLATFORM_SESSION_REQUIRED"
