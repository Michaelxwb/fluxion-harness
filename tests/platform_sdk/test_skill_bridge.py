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
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from muad_platform_sdk import PlatformConfig, PlatformSession, SecretValue, SessionMode
from muad_platform_sdk.adapter.mss_config import PlatformAuthError
from muad_platform_sdk.request_budget import LocalRateLimitError
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

    async def renew(self, request: SessionRequest) -> PlatformSession:
        return await self.acquire(request)


class _FailingManager:
    async def acquire(self, request: SessionRequest) -> PlatformSession:
        raise PlatformAuthError(
            "PLATFORM_AUTH_REJECTED",
            detail={"http_status": 401, "message": "denied"},
        )


class _BudgetManager:
    async def acquire(self, request: SessionRequest) -> PlatformSession:
        raise LocalRateLimitError("EXECUTION", "run", "rule-budget", "TOTAL", 1, 1)


class _NoAuthAdapter:
    key = "no-auth"
    name = "no-auth"
    version = "1"
    session_mode = SessionMode.NONE
    platform_config_schema: dict[str, object] = {}
    credential_schema: dict[str, object] = {}

    async def authenticate(self, platform, credential):  # noqa: ANN001
        return None

    async def validate(self, platform, session):  # noqa: ANN001
        return True

    async def prepare_request(self, platform, session, request, credential):  # noqa: ANN001
        raise NotImplementedError


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


def _patch_resolver(
    monkeypatch: pytest.MonkeyPatch,
    *,
    denied: frozenset[str] = frozenset(),
    missing: frozenset[str] = frozenset(),
    failing: frozenset[str] = frozenset(),
    rejecting: frozenset[str] = frozenset(),
    budget: frozenset[str] = frozenset(),
    adapter_key: str = "mss-session",
    credential_mode: str = "USER_ONLY",
) -> None:
    async def resolve(
        self: SkillPlatformAccess, key: str, method: str, path: str
    ) -> tuple[PlatformConfig, SessionRequest, _Manager | _FailingManager | _RejectingManager]:
        if key in denied:
            raise PlatformAuthError("PLATFORM_ACCESS_DENIED")
        if key in missing:
            raise PlatformAuthError("CREDENTIAL_MISSING")
        platform = PlatformConfig(
            key=key,
            name=key,
            resolver_type="BASE_URL",
            resolver_config={"base_url": "https://mssp.example.internal"},
            adapter_key=adapter_key,
            credential_mode=credential_mode,
        )
        request = SessionRequest(
            platform,
            "user:" + key,
            "1",
            "credential-" + key,
            SecretValue(value="{}", version="1"),
        )
        if key in rejecting:
            manager: _Manager | _FailingManager | _RejectingManager | _BudgetManager = (
                _RejectingManager()
            )
        elif key in budget:
            manager = _BudgetManager()
        elif key in failing:
            manager = _FailingManager()
        else:
            manager = _Manager()
        return platform, request, manager

    monkeypatch.setattr(SkillPlatformAccess, "resolve", resolve)


def _patch_list(monkeypatch: pytest.MonkeyPatch, keys: tuple[str, ...]) -> None:
    async def list_platforms(self: SkillPlatformAccess) -> list[dict[str, object]]:
        return [
            {"key": key, "name": key, "authentication": "session", "available": True}
            for key in keys
        ]

    monkeypatch.setattr(SkillPlatformAccess, "list_platforms", list_platforms)


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


async def _run_cli_platform(
    env: dict[str, str], platform: str
) -> subprocess.CompletedProcess[str]:
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(SESSION_CLI),
        "get-state",
        "--platform",
        platform,
        cwd=str(ROOT),
        env={**os.environ, **env},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
    return subprocess.CompletedProcess(
        args=[str(SESSION_CLI)], returncode=process.returncode, stdout=stdout, stderr=stderr
    )


async def _run_cli_refresh(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(SESSION_CLI),
        "refresh",
        "--platform",
        "mssp",
        cwd=str(ROOT),
        env={**os.environ, **env},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
    return subprocess.CompletedProcess(
        args=[str(SESSION_CLI)], returncode=process.returncode, stdout=stdout, stderr=stderr
    )


async def _run_cli_list(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(SESSION_CLI),
        "list-platforms",
        cwd=str(ROOT),
        env={**os.environ, **env},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
    return subprocess.CompletedProcess(
        args=[str(SESSION_CLI)], returncode=process.returncode, stdout=stdout, stderr=stderr
    )


class _RejectingManager:
    """取 state 时暴露平台失败，用于验证原因透传。"""

    async def acquire(self, request: SessionRequest) -> PlatformSession:
        raise PlatformAuthError(
            "PLATFORM_AUTH_REJECTED",
            detail={"http_status": 200, "business_code": 9348, "message": "签名校验失败"},
        )


async def test_failed_session_carries_the_platform_reason_to_the_skill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """平台失败原因（非 2xx / code≠0）要经 broker → CLI stderr 交到 Skill 手里。"""

    async def resolve(
        self: SkillPlatformAccess, key: str, method: str, path: str
    ) -> tuple[PlatformConfig, SessionRequest, _RejectingManager]:
        platform = PlatformConfig(
            key=key,
            name=key,
            resolver_type="BASE_URL",
            resolver_config={"base_url": "https://mssp.example.internal"},
            adapter_key="mss-session",
            credential_mode="USER_ONLY",
        )
        request = SessionRequest(
            platform, "user:" + key, "1", "credential-" + key, SecretValue(value="{}", version="1")
        )
        return platform, request, _RejectingManager()

    monkeypatch.setattr(SkillPlatformAccess, "resolve", resolve)
    async with skill_platform_environment(_context(), "probe-skill") as session:
        completed = await _run_cli(dict(session.env))

    assert completed.returncode == 1
    payload = json.loads(completed.stderr.decode("utf-8"))
    error = payload["error"]
    assert error["code"] == "PLATFORM_AUTH_REJECTED"
    assert error["detail"] == {
        "http_status": 200,
        "business_code": 9348,
        "message": "签名校验失败",
    }
    sessions = payload["platform_sessions"]["platforms"]
    assert [item["key"] for item in sessions] == ["mssp"]
    assert sessions[0]["available"] is False
    assert sessions[0]["error"]["code"] == "PLATFORM_AUTH_REJECTED"


async def test_declared_platforms_are_resolved_lazily(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A declared platform the script never asks for must not fail the run."""
    _patch_resolver(monkeypatch, failing=frozenset({"mssw"}))
    async with skill_platform_environment(
        _context(("mssp", "mssw")), "probe-skill"
    ) as session:
        used = await _run_cli_platform(dict(session.env), "mssp")
        assert used.returncode == 0, used.stderr
        assert json.loads(used.stdout)["platform"] == "mssp"

        unused = await _run_cli_platform(dict(session.env), "mssw")
        assert unused.returncode == 1
        payload = json.loads(unused.stderr)
        assert payload["error"]["code"] == "PLATFORM_AUTH_REJECTED"
        assert [item["key"] for item in payload["platform_sessions"]["platforms"]] == [
            "mssw"
        ]


async def test_unused_declared_platform_denial_does_not_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`get-state --platform` must not resolve the rest of the declared set."""
    _patch_resolver(monkeypatch, denied=frozenset({"mssw"}))
    async with skill_platform_environment(
        _context(("mssp", "mssw")), "probe-skill"
    ) as session:
        completed = await _run_cli_platform(dict(session.env), "mssp")
    assert completed.returncode == 0, completed.stderr


async def test_get_state_partial_reports_without_writing_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_resolver(monkeypatch, rejecting=frozenset({"mssw"}))
    async with skill_platform_environment(_context(("mssp", "mssw")), "probe-skill") as session:
        completed = await _run_cli(dict(session.env))

    assert completed.returncode == 1
    assert completed.stdout.strip() == b""
    payload = json.loads(completed.stderr)
    assert payload["error"]["code"] == "PLATFORM_AUTH_REJECTED"
    sessions = payload["platform_sessions"]["platforms"]
    assert [item["key"] for item in sessions] == ["mssp", "mssw"]
    assert sessions[0]["available"] is True
    assert sessions[1]["available"] is False


async def test_successful_session_has_no_error_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """成功路径不产生原因字段（正常返回不需要告诉用户任何平台细节）。"""
    _patch_resolver(monkeypatch)
    async with skill_platform_environment(_context(), "probe-skill") as session:
        completed = await _run_cli(dict(session.env))
    assert completed.returncode == 0
    stdout = completed.stdout.decode("utf-8")
    stderr = completed.stderr.decode("utf-8")
    assert "detail" not in stdout and "detail" not in stderr


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


async def test_platform_environment_exposes_the_last_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_resolver(monkeypatch)
    async with skill_platform_environment(_context(), "probe-skill") as session:
        assert session.report is not None
        assert session.report.payload()["platforms"] == []
        assert (await _run_cli(dict(session.env))).returncode == 0
        assert session.report.payload()["platforms"][0]["available"] is True


async def test_list_platforms_reports_actual_availability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_list(monkeypatch, ("mssp", "mssw"))
    _patch_resolver(monkeypatch, failing=frozenset({"mssw"}))
    async with skill_platform_environment(_context(()), "probe-skill") as session:
        completed = await _run_cli_list(dict(session.env))

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["version"] == 2
    assert [item["key"] for item in payload["platforms"]] == ["mssp", "mssw"]
    assert payload["platforms"][0]["available"] is True
    assert payload["platforms"][0]["authentication"] == "session"
    assert payload["platforms"][0]["expiresAt"]
    assert payload["platforms"][1]["available"] is False
    assert payload["platforms"][1]["error"] == {
        "code": "PLATFORM_AUTH_REJECTED",
        "detail": {"http_status": 401, "message": "denied"},
    }


async def test_list_platforms_reports_missing_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_list(monkeypatch, ("mssp", "mssw"))
    _patch_resolver(monkeypatch, missing=frozenset({"mssw"}))
    async with skill_platform_environment(_context(()), "probe-skill") as session:
        completed = await _run_cli_list(dict(session.env))

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["platforms"][0]["available"] is True
    assert payload["platforms"][1]["available"] is False
    assert payload["platforms"][1]["error"]["code"] == "CREDENTIAL_MISSING"


async def test_list_platforms_all_fail_still_returns_a_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_list(monkeypatch, ("mssp", "mssw"))
    _patch_resolver(monkeypatch, failing=frozenset({"mssp", "mssw"}))
    async with skill_platform_environment(_context(()), "probe-skill") as session:
        completed = await _run_cli_list(dict(session.env))

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert [item["available"] for item in payload["platforms"]] == [False, False]
    assert [item["error"]["code"] for item in payload["platforms"]] == [
        "PLATFORM_AUTH_REJECTED",
        "PLATFORM_AUTH_REJECTED",
    ]


async def test_list_platforms_empty_catalog_is_an_empty_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_list(monkeypatch, ())
    _patch_resolver(monkeypatch)
    async with skill_platform_environment(_context(()), "probe-skill") as session:
        completed = await _run_cli_list(dict(session.env))

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {"version": 2, "platforms": []}


async def test_list_platforms_marks_no_session_platform_as_not_checked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_list(monkeypatch, ("no-auth",))
    _patch_resolver(monkeypatch, adapter_key="no-auth", credential_mode="NONE")
    original_init = SkillPlatformAccess.__init__

    def init_with_no_auth(self: SkillPlatformAccess, context: SkillPlatformContext) -> None:
        original_init(self, context)
        self.registry.register(_NoAuthAdapter())

    monkeypatch.setattr(SkillPlatformAccess, "__init__", init_with_no_auth)
    async with skill_platform_environment(_context(()), "probe-skill") as session:
        completed = await _run_cli_list(dict(session.env))

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["platforms"][0]["available"] is None
    assert payload["platforms"][0]["reason"] == "session_not_applicable"


async def test_list_platforms_budget_interruption_marks_remaining_not_checked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_list(monkeypatch, ("mssp", "mssw"))
    _patch_resolver(monkeypatch, budget=frozenset({"mssw"}))
    async with skill_platform_environment(_context(()), "probe-skill") as session:
        completed = await _run_cli_list(dict(session.env))

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["platforms"][0]["available"] is True
    assert payload["platforms"][1]["available"] is None
    assert payload["platforms"][1]["reason"] == "budget_exhausted"


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


async def test_explicit_workspace_selection_checks_authorization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicitly selected but unauthorized platform is rejected before the command."""
    _patch_resolver(monkeypatch, denied=frozenset({"mssw"}))
    context = replace(_context(("mssw",)), authorize_selection=True)
    with pytest.raises(PlatformAuthError) as captured:
        async with skill_platform_environment(context, "workspace"):
            pass
    assert captured.value.code == "PLATFORM_SESSION_REQUIRED"


async def test_explicit_workspace_selection_resolves_sessions_lazily(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A selected platform's failed login must not block a command that never uses it."""
    _patch_resolver(monkeypatch, failing=frozenset({"mssw"}))
    context = replace(_context(("mssp", "mssw")), authorize_selection=True)
    async with skill_platform_environment(context, "workspace") as session:
        used = await _run_cli_platform(dict(session.env), "mssp")
        assert used.returncode == 0, used.stderr

        unused = await _run_cli_platform(dict(session.env), "mssw")
        assert unused.returncode == 1
        payload = json.loads(unused.stderr)
        assert payload["error"]["code"] == "PLATFORM_AUTH_REJECTED"
        assert [item["key"] for item in payload["platform_sessions"]["platforms"]] == [
            "mssw"
        ]


async def test_unauthorized_platform_fails_with_platform_session_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_resolver(monkeypatch, denied=frozenset({"mssp"}))
    access = SkillPlatformAccess(_context())
    with pytest.raises(PlatformAuthError) as captured:
        await access.allowed_keys()
    await access.close()
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


async def test_platform_key_cli_does_not_bind_to_skill_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """v2 platform-key calls remain valid when the package name changes after draft writes."""
    _patch_resolver(monkeypatch)
    async with skill_platform_environment(_context(), "probe-skill") as session:
        env = {**session.env, "MUAD_SESSION_NAME": "changed-after-write"}
        completed = await _run_cli(env)
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["platforms"]


async def test_refresh_uses_renew_and_keeps_success_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_resolver(monkeypatch)
    async with skill_platform_environment(_context(), "probe-skill") as session:
        completed = await _run_cli_refresh(dict(session.env))

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["resolution"] == "refreshed"
    assert payload["refresh"] == {"requested": True, "performed": True}


async def test_no_platform_context_yields_no_injection() -> None:
    async with skill_platform_environment(None, "") as session:
        assert session.env == {}
        assert session.mounts == ()
