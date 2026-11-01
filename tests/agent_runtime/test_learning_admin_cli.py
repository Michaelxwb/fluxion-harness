"""S-06: learning_admin CLI process contract (TASK-004)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import sqlalchemy as sa
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import UserMemory

from tests.agent_runtime.learning_wiring_support import (
    cleanup,
    seed_experience,
    seed_report,
)

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/learning_admin.py"


def _cli(*args: str) -> tuple[int, str, str]:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
        timeout=60,
    )
    return result.returncode, result.stdout, result.stderr


async def _state(tenant: str, key: str) -> str:
    async with get_session_factory()() as session:
        row = await session.scalar(
            sa.select(UserMemory).where(
                UserMemory.tenant_id == tenant, UserMemory.memory_key == key
            )
        )
    return "" if row is None else row.activation_state


async def test_s06_cli_lifecycle_over_real_process(database_guard: None) -> None:
    tenant, user = f"cli-{uuid.uuid4()}", uuid.uuid4()
    key = "exp.cli"
    await seed_experience(tenant, user, key, state="PROPOSED")
    await seed_report(tenant, key, "PASSED", base="cli", user_id=user)
    base = ["--tenant-id", tenant, "--user-id", str(user)]
    try:
        code, out, err = _cli("list", *base)
        assert code == 0, err
        listed = json.loads(out)
        assert [item["memory_key"] for item in listed] == [key]
        assert listed[0]["activation_state"] == "PROPOSED"

        code, out, err = _cli("replay", *base, "--experience-key", key, "--base-config-hash", "replay-test")
        assert code == 0, err
        assert json.loads(out)["status"] == "INCOMPLETE"

        code, out, err = _cli(
            "activate", *base, "--experience-key", key, "--base-config-hash", "cli",
            "--operator", "alice",
        )
        assert code == 0, err
        assert json.loads(out)["activation_state"] == "ACTIVE"
        assert await _state(tenant, key) == "ACTIVE"

        code, out, err = _cli("explain", *base, "--experience-key", key)
        assert code == 0, err
        detail = json.loads(out)
        assert detail["memory_key"] == key
        assert any(report["decision"]["status"] == "PASSED" for report in detail["reports"])

        code, out, err = _cli(
            "retire", *base, "--experience-key", key, "--operator", "bob", "--reason", "degraded"
        )
        assert code == 0, err
        assert json.loads(out)["activation_state"] == "RETIRED"
        assert await _state(tenant, key) == "RETIRED"

        # 失败路径：未知经验退出码 1；缺 operator 退出码 2。
        code, out, _ = _cli("explain", *base, "--experience-key", "missing.key")
        assert code == 1
        code, _, _ = _cli("activate", *base, "--experience-key", key)
        assert code == 2
    finally:
        await cleanup(tenant)
