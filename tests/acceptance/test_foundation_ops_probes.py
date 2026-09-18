from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from muad_api import install_health_probes

ROOT = Path(__file__).resolve().parents[2]

FAIL_FAST_SCRIPT = """
import asyncio
from types import SimpleNamespace

from muad_api import StartupValidationError, validate_startup


class UnusedProvider:
    async def get(self, secret_ref):
        raise LookupError(secret_ref)


async def main():
    settings = SimpleNamespace(database_url="postgresql+asyncpg://probe", artifact_root="/missing-mount")
    store = SimpleNamespace(root="/missing-mount")
    try:
        await validate_startup(settings, None, store, UnusedProvider(), migrations_dir=None)
    except StartupValidationError as exc:
        print(f"STARTUP_ABORTED: {exc}")
        raise SystemExit(3)
    raise SystemExit(0)


asyncio.run(main())
"""


def test_s07_probes_reflect_dependency_state(tmp_path: Path) -> None:
    mounted = tmp_path / "artifacts"
    mounted.mkdir()
    failed: set[str] = set()

    def storage() -> bool:
        return "storage" not in failed and mounted.is_dir()

    async def async_probe() -> bool:
        return "async_probe" not in failed and mounted.is_dir()

    def broken_dep() -> bool:
        if "broken_dep" in failed:
            raise RuntimeError("connection refused")
        return True

    app = FastAPI()
    install_health_probes(
        app,
        {"storage": storage, "async_probe": async_probe, "broken_dep": broken_dep},
    )
    client = TestClient(app)

    assert client.get("/healthz").status_code == 200

    ready = client.get("/readyz")
    assert ready.status_code == 200
    assert ready.json() == {"status": "ready"}

    failed.update({"storage", "async_probe", "broken_dep"})
    mounted.rmdir()
    degraded = client.get("/readyz")
    assert degraded.status_code == 503
    assert set(degraded.json()["failed"]) == {"storage", "async_probe", "broken_dep"}


def test_s07_startup_fails_fast_on_missing_dependency() -> None:
    result = subprocess.run(
        [sys.executable, "-c", FAIL_FAST_SCRIPT],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 3, f"expected fail-fast exit, got {result.returncode}: {result.stderr[-400:]}"
    assert "artifact storage is not mounted" in result.stdout
