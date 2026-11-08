#!/usr/bin/env python3
"""End-to-end local smoke for the ``smoke_platform`` session chain.

Starts the fake MSSW-compatible server, logs in through the real
``MSSAdapter`` (SigV4 AK/SK + CSRF), writes an execution-shaped session state
file, runs ``skills/smoke-platform-sample/scripts/run.py`` against it, and
asserts the health + CSRF-protected business calls + Traceid echo.

Exit code 0 means the chain works; any mismatch fails loudly.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Any

from muad_contracts import CredentialMode
from muad_platform_sdk.adapter.mss import MSSAdapter
from muad_platform_sdk.types import PlatformConfig, SecretValue

ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "tools/fake-business-platform/server.py"
SKILL = ROOT / "skills/smoke-platform-sample/scripts/run.py"
PLATFORM_KEY = "smoke_platform"
AK = "test-ak"
SK = "test-sk"
ADAPTER_CONFIG: dict[str, Any] = {
    "auth_mode": "hmac",
    "csrf_enabled": True,
    "csrf_endpoint": "/v1/certification/get_token",
    "session_endpoint": "/gateway/mss-auth-acl-service/v1/certification/login_agent",
    "health_endpoint": "/v1/rtt",
    "login_cookie_name": "fake_session",
    "csrf_header": "X-CSRFToken",
    "branch_tag": "MSSW-ADAPTER",
    "timezone": "+08:00",
    "timeout_seconds": 10,
    "session_ttl_seconds": 900,
}


def start_server() -> tuple[subprocess.Popen[str], str]:
    process = subprocess.Popen(
        [
            sys.executable,
            str(SERVER),
            "--platform",
            "mssw",
            "--host",
            "127.0.0.1",
            "--port",
            "0",
            "--ak",
            AK,
            "--sk",
            SK,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    assert process.stdout is not None
    line = process.stdout.readline()
    if not line:
        process.terminate()
        raise RuntimeError("fake platform did not become ready")
    ready = json.loads(line)
    return process, str(ready["baseUrl"])


def platform_config(base_url: str) -> PlatformConfig:
    return PlatformConfig(
        key=PLATFORM_KEY,
        name="Smoke Platform",
        resolver_type="BASE_URL",
        resolver_config={"base_url": base_url},
        adapter_key="mss-session",
        adapter_config=ADAPTER_CONFIG,
        adapter_schema_version="2",
        credential_mode=CredentialMode.SHARED_ONLY,
    )


def login(base_url: str) -> dict[str, Any]:
    config = platform_config(base_url)
    credential = SecretValue(
        value=json.dumps({"ak": AK, "sk": SK}), version="1"
    )
    session = asyncio.run(MSSAdapter().authenticate(config, credential))
    cookies = json.loads(session.cookie or "[]")
    return {
        "platforms": {
            PLATFORM_KEY: {
                "cookies": cookies,
                "requestProfile": {
                    "baseUrl": base_url,
                    "hostHeader": {},
                    "csrf": [{"cookie": "csrf_token", "header": "X-CSRFToken"}],
                    "staticHeaders": {"Timezone": "+08:00"},
                    "traceHeader": None,
                    "extraAllowed": False,
                },
            }
        }
    }


def run_skill(state: dict[str, Any]) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="smoke-platform-") as temp:
        root = Path(temp)
        state_file = root / "session-state.json"
        state_file.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        output_dir = root / "outputs"
        env = {
            **os.environ,
            "MUAD_LOCAL_SESSION_STATE": str(state_file),
            "SKILL_OUTPUT_DIR": str(output_dir),
        }
        completed = subprocess.run(
            [sys.executable, str(SKILL)],
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
            check=False,
        )
        if completed.returncode != 0:
            raise SystemExit(
                f"skill failed (exit {completed.returncode}):\n{completed.stderr.strip()}"
            )
        payload = json.loads(completed.stdout)
        written = output_dir / "smoke-platform-result.json"
        if not written.is_file():
            raise SystemExit("skill did not write SKILL_OUTPUT_DIR/smoke-platform-result.json")
        payload["_output_file"] = str(written)
        return payload


def fetch_json(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    process, base_url = start_server()
    try:
        state = login(base_url)
        result = run_skill(state)
        stats = fetch_json(base_url + "/__stats")
    finally:
        process.terminate()
        process.wait(timeout=5)

    checks = {
        "health": result.get("health", {}).get("code") == 0,
        "ping": result.get("ping", {}).get("code") == 0,
        "trace_echo": bool(result.get("ping", {}).get("traceId")),
        "targets": len(result.get("targets", {}).get("items", [])) == 3,
        "request_counts": stats.get("counts", {}).get("/api/v1/ping", 0) >= 1
        and stats.get("counts", {}).get("/api/v1/targets", 0) >= 1,
    }
    failed = [name for name, ok in checks.items() if not ok]
    print(json.dumps({"baseUrl": base_url, "checks": checks, "result": result}, ensure_ascii=False, indent=2))
    if failed:
        print(f"smoke failed: {failed}", file=sys.stderr)
        return 1
    print("smoke ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
