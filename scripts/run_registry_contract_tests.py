#!/usr/bin/env python3
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time


def docker_available() -> bool:
    if not shutil.which("docker"):
        return False
    return subprocess.run(
        ["docker", "info"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode == 0


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, check=False, text=True)


def _start_postgres() -> tuple[str, str]:
    name = f"fluxion-registry-contract-{os.getpid()}"
    args = [
        "docker",
        "run",
        "-d",
        "--name",
        name,
        "-e",
        "POSTGRES_PASSWORD=postgres",
        "-e",
        "POSTGRES_DB=fluxion_test",
        "-p",
        "127.0.0.1::5432",
        "postgres:16-alpine",
    ]
    started = _run(args)
    if started.returncode != 0:
        raise RuntimeError(started.stderr.strip())
    port = _published_port(name)
    dsn = f"postgresql+asyncpg://postgres:postgres@127.0.0.1:{port}/fluxion_test"
    _wait_for_postgres(name, dsn)
    return name, dsn


def _published_port(name: str) -> str:
    for _ in range(20):
        inspected = _run(["docker", "port", name, "5432/tcp"])
        if inspected.returncode == 0 and inspected.stdout.strip():
            return inspected.stdout.rsplit(":", 1)[-1].strip()
        time.sleep(0.25)
    raise RuntimeError("PostgreSQL container did not publish a port")


def _wait_for_postgres(name: str, dsn: str) -> None:
    for _ in range(60):
        ready = _run(["docker", "exec", name, "pg_isready", "-U", "postgres", "-d", "fluxion_test"])
        if ready.returncode == 0 and _host_can_connect(dsn):
            return
        time.sleep(0.5)
    raise RuntimeError("PostgreSQL container did not become ready")


def _host_can_connect(dsn: str) -> bool:
    raw_dsn = dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
    code = (
        "import asyncio, sys, asyncpg\n"
        "async def main():\n"
        "    conn = await asyncpg.connect(sys.argv[1], ssl=False, command_timeout=2)\n"
        "    await conn.execute('SELECT 1')\n"
        "    await conn.close()\n"
        "asyncio.run(main())\n"
    )
    return _run([sys.executable, "-c", code, raw_dsn]).returncode == 0


def _cleanup_container(name: str | None) -> None:
    if name is not None:
        subprocess.run(["docker", "rm", "-f", name], stdout=subprocess.DEVNULL, check=False)


def main() -> int:
    require_pg = os.getenv("FLUXION_REQUIRE_POSTGRES_CONTRACT") == "1"
    cmd = [sys.executable, "-m", "pytest", "backend/tests/contract/test_registry_store.py", "-q"]
    env = os.environ.copy()
    container_name: str | None = None
    try:
        if docker_available():
            container_name, env["FLUXION_POSTGRES_DSN"] = _start_postgres()
            env["FLUXION_REQUIRE_POSTGRES_CONTRACT"] = "1"
            print("[registry-contract] Docker available: running SQLite + PostgreSQL suite")
        elif require_pg:
            print(
                "[registry-contract] PostgreSQL required but Docker is unavailable",
                file=sys.stderr,
            )
            return 2
        else:
            env["FLUXION_REQUIRE_POSTGRES_CONTRACT"] = "0"
            print("[registry-contract] Docker unavailable: running SQLite contract suite only")
        return subprocess.run(cmd, env=env, check=False).returncode
    finally:
        _cleanup_container(container_name)


if __name__ == "__main__":
    raise SystemExit(main())
