#!/usr/bin/env python3
"""One-command E2E for ``skills/fake-platform-async-batch``.

Runs against the platform service managed by ``services.sh`` (it does not spawn
its own server): logs in through the real MSSAdapter, runs the Skill in parent
mode to produce a BATCH plan, then runs every child item and writes JSON/CSV
reports under ``.data/fake-business-platform/async-batch-demo`` (kept for
inspection). Exit code 0 means the platform + batch + file chain works.

Start the service first, or use the wrapper command:

    ./tools/fake-business-platform/services.sh start
    ./tools/fake-business-platform/services.sh async-batch

The Worker-level fan-out (real Task rows, child Tasks, fan-in) is covered by
``tests/acceptance/task_schedule/test_fake_platform_async_batch.py``.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
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
SKILL = ROOT / "skills/fake-platform-async-batch"
SCRIPT = SKILL / "scripts/run.py"
OUTPUT_ROOT = ROOT / ".data/fake-business-platform/async-batch-demo"
RUN_DIR = Path(os.environ.get("RUN_DIR", ROOT / ".data/fake-business-platform"))
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


def load_platform_ready() -> dict[str, Any]:
    ready_file = RUN_DIR / "platform.ready.json"
    if not ready_file.is_file():
        raise SystemExit(
            "platform 未启动：先运行 ./tools/fake-business-platform/services.sh start platform，"
            "或直接运行 ./tools/fake-business-platform/services.sh async-batch"
        )
    ready = json.loads(ready_file.read_text(encoding="utf-8"))
    if ready.get("status") != "ready" or not ready.get("baseUrl"):
        raise SystemExit(f"平台就绪信息无效：{ready_file}")
    return ready


def login(base_url: str, ak: str, sk: str) -> dict[str, Any]:
    config = PlatformConfig(
        key=PLATFORM_KEY,
        name="Fake Platform",
        resolver_type="BASE_URL",
        resolver_config={"base_url": base_url},
        adapter_key="mss-session",
        adapter_config=ADAPTER_CONFIG,
        adapter_schema_version="2",
        credential_mode=CredentialMode.SHARED_ONLY,
    )
    credential = SecretValue(value=json.dumps({"ak": ak, "sk": sk}), version="1")
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


def run_skill(
    payload: dict[str, Any],
    *,
    state_file: Path,
    output_dir: Path,
) -> dict[str, Any]:
    env = {**os.environ, "MUAD_LOCAL_SESSION_STATE": str(state_file)}
    env.pop("MUAD_SESSION_SOCKET", None)
    env["SKILL_OUTPUT_DIR"] = str(output_dir)
    completed = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=json.dumps(payload, ensure_ascii=False),
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
    return json.loads(completed.stdout)


def fetch_json(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-._") or "target"


def main() -> int:
    ready = load_platform_ready()
    base_url = str(ready["baseUrl"])
    ak = str(ready.get("ak") or AK)
    sk = str(ready.get("sk") or SK)
    state = login(base_url, ak, sk)
    with tempfile.TemporaryDirectory(prefix="async-batch-state-") as temp:
        state_file = Path(temp) / "session-state.json"
        state_file.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        shutil.rmtree(OUTPUT_ROOT, ignore_errors=True)
        OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)

        parent = run_skill({}, state_file=state_file, output_dir=OUTPUT_ROOT / "_parent")
        plan = parent["batch"]
        files: dict[str, list[str]] = {}
        for item in plan["items"]:
            target = str(item["target"])
            output = OUTPUT_ROOT / safe_name(target)
            child = run_skill(item, state_file=state_file, output_dir=output)
            files[target] = [str(output / entry["relative_path"]) for entry in child["files"]]

        stats = fetch_json(base_url + "/__stats")["counts"]
        summary = {
            "baseUrl": base_url,
            "targets": [item["target"] for item in plan["items"]],
            "max_concurrency": plan["max_concurrency"],
            "aggregate_mode": plan["aggregate_mode"],
            "files": files,
            "request_counts": stats,
            "output_root": str(OUTPUT_ROOT),
        }
        checks = {
            "three_targets": len(plan["items"]) == 3,
            "all_children_files": all(len(paths) == 2 for paths in files.values()),
            "parent_read_targets": stats.get("/api/v1/targets", 0) >= 4,
            "children_hit_ping": stats.get("/api/v1/ping", 0) >= 3,
        }
        print(json.dumps({"checks": checks, **summary}, ensure_ascii=False, indent=2))
        failed = [name for name, ok in checks.items() if not ok]
        if failed:
            print(f"async batch check failed: {failed}", file=sys.stderr)
            return 1
        print("async batch check ok")
        return 0


if __name__ == "__main__":
    sys.exit(main())
