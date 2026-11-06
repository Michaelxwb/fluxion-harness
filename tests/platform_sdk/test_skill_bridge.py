"""session-manager broker 契约：旧 stdout、state 文件、执行级预算与授权集合。

本文件只验证 broker/CLI 自身的形状：真实 Unix socket + 真实 ``session_cli`` 子进程，
Console 解析与会话存储用记录桩（真实 Redis/adapter 的边界由
``tests/acceptance/user_skill_sandbox/test_session_manager.py`` 覆盖）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from muad_platform_sdk import PlatformConfig, PlatformSession, SecretValue
from muad_platform_sdk.adapter.mss_config import PlatformAuthError
from muad_platform_sdk.session.manager import SessionRequest
from muad_platform_sdk.skill_access import SkillPlatformAccess, SkillPlatformContext
from muad_platform_sdk.skill_bridge import SessionBrokerBudget, skill_platform_environment

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
SESSION_CLI = ROOT / "packages/platform-sdk/src/muad_platform_sdk/session_cli.py"
COOKIE = "session-cookie-" + uuid.uuid4().hex


class _Manager:
    async def acquire(self, request: SessionRequest) -> PlatformSession:
        return PlatformSession(
            cookie=json.dumps([{"name": "csrf_token", "value": COOKIE}]),
            expires_at=datetime.now(UTC) + timedelta(hours=2),
        )


def _context(keys: tuple[str, ...] = ("mssp",)) -> SkillPlatformContext:
    return SkillPlatformContext(
        tenant_id="tenant-session",
        actor_user_id=str(uuid.uuid4()),
        execution_id=str(uuid.uuid4()),
        execution_type="RUN",
        platform_keys=keys,
        console_url="http://127.0.0.1:1",
        redis_url=None,
        service_token="internal-token",
    )


def _patch_resolver(monkeypatch: pytest.MonkeyPatch, *, denied: frozenset[str] = frozenset()) -> None:
    async def resolve(
        self: SkillPlatformAccess, key: str, method: str, path: str
    ) -> tuple[PlatformConfig, SessionRequest, _Manager]:
        if key in denied:
            raise PlatformAuthError("PLATFORM_ACCESS_DENIED")
        platform = PlatformConfig(
            key=key,
            name=key,
            resolver_type="BASE_URL",
            resolver_config={"base_url": "https://mssp.example.internal"},
            adapter_key="mss-session",
            credential_mode="USER_ONLY",
        )
        request = SessionRequest(
            platform,
            "user:" + key,
            "1",
            "credential-" + key,
            SecretValue(value="{}", version="1"),
        )
        return platform, request, _Manager()

    monkeypatch.setattr(SkillPlatformAccess, "resolve", resolve)


async def _run_cli(env: dict[str, str], skill_name: str = "probe-skill") -> subprocess.CompletedProcess[str]:
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(SESSION_CLI),
        "get-state",
        "--skill-name",
        skill_name,
        cwd=str(ROOT),
        env={**os.environ, **env},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
    return subprocess.CompletedProcess(
        args=[str(SESSION_CLI)], returncode=process.returncode, stdout=stdout, stderr=stderr
    )


async def test_get_state_stdout_keeps_legacy_platforms_and_writes_plain_state_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_resolver(monkeypatch)
    state_path: Path | None = None
    async with skill_platform_environment(_context(), "probe-skill") as session:
        completed = await _run_cli(dict(session.env))
        assert completed.returncode == 0, completed.stderr
        payload = json.loads(completed.stdout)

        assert payload["version"] == 1
        assert payload["status"] == "ready"
        assert payload["skillName"] == "probe-skill"
        assert payload["sessionStateFile"]
        assert not str(payload["sessionStateFile"]).startswith("/proc/")

        state_path = Path(payload["sessionStateFile"])
        assert state_path.is_file()
        assert state_path.stat().st_mode & 0o777 == 0o600
        state = json.loads(state_path.read_text(encoding="utf-8"))
        assert state["platforms"]["mssp"]["cookies"] == [{"name": "csrf_token", "value": COOKIE}]
        assert state["platforms"]["mssp"]["requestProfile"]["baseUrl"] == ("https://mssp.example.internal")

        # 旧 Skill 从 stdout 的 list 里读 mssp/expiresAt；state 文件内部仍是 dict。
        assert payload["platforms"] == [
            {
                "platform": "mssp",
                "source": "session",
                "expiresAt": state["platforms"]["mssp"]["expiresAt"],
            }
        ]
        assert payload["platforms"][0]["expiresAt"]

    assert state_path is not None and not state_path.exists()


async def test_sandbox_root_maps_socket_state_and_readonly_mounts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_resolver(monkeypatch)
    async with skill_platform_environment(
        _context(), "probe-skill", sandbox_root="/tmp/fx-session"
    ) as session:
        assert session.env["MUAD_SESSION_SOCKET"] == "/tmp/fx-session/session.sock"
        assert session.env["PATH"].split(":")[0] == "/tmp/fx-session"

        mounts = {mount.target: mount for mount in session.mounts}
        assert mounts["/tmp/fx-session"].readonly is False
        assert mounts["/tmp/fx-session/state"].readonly is True
        run_dir = mounts["/tmp/fx-session"].source
        state_dir = mounts["/tmp/fx-session/state"].source
        assert (run_dir / "session-manager").is_file()
        assert (run_dir / "session_cli.py").is_file()
        assert state_dir.is_dir()


async def test_only_platforms_allowed_by_trusted_context_are_exposed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_resolver(monkeypatch, denied=frozenset({"mssw"}))
    async with skill_platform_environment(_context(("mssp", "mssw")), "probe-skill") as session:
        completed = await _run_cli(dict(session.env))
        assert completed.returncode == 0, completed.stderr
        payload = json.loads(completed.stdout)
        assert [item["platform"] for item in payload["platforms"]] == ["mssp"]
        state = json.loads(Path(payload["sessionStateFile"]).read_text(encoding="utf-8"))
        assert set(state["platforms"]) == {"mssp"}


async def test_unauthorized_platform_fails_with_platform_session_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_resolver(monkeypatch, denied=frozenset({"mssp"}))
    with pytest.raises(PlatformAuthError) as captured:
        async with skill_platform_environment(_context(), "probe-skill"):
            pass
    assert captured.value.code == "PLATFORM_SESSION_REQUIRED"


async def test_request_budget_rejects_extra_get_state_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_resolver(monkeypatch)
    budget = SessionBrokerBudget(connections=8, requests=2, state_files=8)
    async with skill_platform_environment(_context(), "probe-skill", budget=budget) as session:
        assert (await _run_cli(dict(session.env))).returncode == 0
        assert (await _run_cli(dict(session.env))).returncode == 0
        rejected = await _run_cli(dict(session.env))
        assert rejected.returncode == 1
        assert json.loads(rejected.stderr)["error"]["code"] == ("PLATFORM_SESSION_BUDGET_EXCEEDED")


async def test_state_file_budget_bounds_files_per_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_resolver(monkeypatch)
    budget = SessionBrokerBudget(connections=8, requests=8, state_files=1)
    async with skill_platform_environment(_context(), "probe-skill", budget=budget) as session:
        assert (await _run_cli(dict(session.env))).returncode == 0
        rejected = await _run_cli(dict(session.env))
        assert rejected.returncode == 1
        assert json.loads(rejected.stderr)["error"]["code"] == ("PLATFORM_SESSION_BUDGET_EXCEEDED")


async def test_connection_budget_rejects_extra_clients(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_resolver(monkeypatch)
    budget = SessionBrokerBudget(connections=1, requests=8, state_files=8)
    async with skill_platform_environment(_context(), "probe-skill", budget=budget) as session:
        first = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        second = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        first.settimeout(5)
        second.settimeout(5)
        try:
            first.connect(session.env["MUAD_SESSION_SOCKET"])
            second.connect(session.env["MUAD_SESSION_SOCKET"])
            await asyncio.sleep(0.1)
            response = json.loads(await asyncio.to_thread(lambda: second.makefile("rb").readline(65536)))
            assert response["error"] == "PLATFORM_SESSION_BUDGET_EXCEEDED"
        finally:
            first.close()
            second.close()


async def test_cookie_never_appears_in_logs(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _patch_resolver(monkeypatch)
    with caplog.at_level(logging.DEBUG, logger="muad_platform_sdk.skill_bridge"):
        async with skill_platform_environment(_context(), "probe-skill") as session:
            completed = await _run_cli(dict(session.env))
            assert completed.returncode == 0, completed.stderr
            payload = json.loads(completed.stdout)
            assert COOKIE in Path(payload["sessionStateFile"]).read_text(encoding="utf-8")
    assert COOKIE not in caplog.text


async def test_identity_mismatch_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_resolver(monkeypatch)
    async with skill_platform_environment(_context(), "probe-skill") as session:
        completed = await _run_cli(dict(session.env), "other-skill")
        assert completed.returncode == 1
        assert json.loads(completed.stderr)["error"]["code"] == "invalid_context"


async def test_no_platform_context_yields_no_injection() -> None:
    async with skill_platform_environment(None, "") as session:
        assert session.env == {}
        assert session.mounts == ()
