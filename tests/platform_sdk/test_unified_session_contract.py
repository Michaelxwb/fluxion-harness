from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from muad_platform_sdk.skill_access import PlatformScope, SkillPlatformContext

ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / "packages/platform-sdk/src/muad_platform_sdk/session_cli.py"


@pytest.mark.unit
def test_empty_platforms_are_auto_and_explicit_platforms_are_selected() -> None:
    base = dict(
        tenant_id="tenant",
        actor_user_id="user",
        execution_id="run",
        execution_type="RUN",
        console_url="http://console",
        redis_url="redis://localhost",
        service_token="token",
    )
    assert SkillPlatformContext(platform_keys=(), **base).scope is PlatformScope.AUTO
    assert SkillPlatformContext(platform_keys=("mssp",), **base).scope is PlatformScope.SELECTED


@pytest.mark.unit
def test_session_manager_help_exposes_execution_platform_contract() -> None:
    result = subprocess.run(
        [sys.executable, str(CLI), "--help"],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    assert payload["version"] == 2
    assert "session-manager list-platforms" in payload["usage"]
    assert "session-manager get-state --platform <key>" in payload["usage"]
    assert "session-manager refresh --platform <key>" in payload["usage"]
    assert "checks actual login state" in payload["notes"]["list-platforms"]
