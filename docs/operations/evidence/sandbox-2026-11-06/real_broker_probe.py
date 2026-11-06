#!/usr/bin/env python3
"""P0.3 canary: real Runtime session broker + bwrap + session-manager + requests.

Run this inside a **runtime image built from the bwrap-enabled base** (or a
canary Pod using that image). It uses the real
`muad_platform_sdk.skill_bridge.skill_platform_environment`, which resolves
Console egress access, Redis session state and the configured platform
credentials. It does not modify Runtime business code.

Required environment:

  TEST_ACTOR_USER_ID   platform user UUID that has the test platform credential
  TEST_PLATFORM_KEY    Console ProjectPlatform key (for example: mssp)
  TEST_URL              test endpoint, absolute URL or path; may include query string
                        (or use TEST_PATH when TEST_URL is empty)

Optional:

  TEST_PATH            path used when TEST_URL is empty
  TEST_PARAMS_JSON     JSON object of query params, e.g. '{"page":1}'
  TEST_BODY_JSON       JSON object sent as request body, e.g. '{"id":"A-1"}'
  TEST_TENANT_ID       defaults to SharedSettings.default_tenant_id
  TEST_SKILL_NAME      defaults to real-broker-probe
  TEST_EXECUTION_ID    defaults to a random UUID
  TEST_METHOD          defaults to GET
  TEST_TIMEOUT_SEC     defaults to 15
  TEST_EXPECT_STATUS   exact expected HTTP status; default accepts 2xx/3xx
  TEST_INSECURE        set to 1 to skip TLS verification (test only)
  TEST_SHOW_BODY       set to 1 to print a short body prefix (redacted)

Example (inside a canary runtime Pod):

  TEST_ACTOR_USER_ID=... \
  TEST_PLATFORM_KEY=mssp \
  TEST_URL=https://test-platform.example/health \
  python /tmp/real_broker_probe.py

Success output ends with:

  RESULT: PASS
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

from muad_common import process_settings
from muad_platform_sdk.skill_access import SkillPlatformContext
from muad_platform_sdk.skill_bridge import skill_platform_environment

CLIENT = r'''
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urljoin

import requests

PLATFORM_KEY = os.environ["TEST_PLATFORM_KEY"]
SKILL_NAME = os.environ["TEST_SKILL_NAME"]
TEST_URL = os.environ.get("TEST_URL", "").strip() or os.environ.get("TEST_PATH", "").strip()
METHOD = os.environ.get("TEST_METHOD", "GET").upper()
TIMEOUT = float(os.environ.get("TEST_TIMEOUT_SEC", "15"))
EXPECT = os.environ.get("TEST_EXPECT_STATUS", "").strip()
INSECURE = os.environ.get("TEST_INSECURE") == "1"
SHOW_BODY = os.environ.get("TEST_SHOW_BODY") == "1"


def json_object(name: str) -> dict:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return {}
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a JSON object")
    return value


PARAMS = json_object("TEST_PARAMS_JSON")
BODY = json_object("TEST_BODY_JSON") or None


def fail(message: str) -> int:
    print(json.dumps({"ok": False, "error": message}, ensure_ascii=False))
    return 1


def main() -> int:
    raw = subprocess.check_output(
        ["session-manager", "get-state", "--skill-name", SKILL_NAME],
        text=True,
        timeout=TIMEOUT,
    )
    response = json.loads(raw)
    state_file = response.get("sessionStateFile")
    if not state_file:
        return fail("sessionStateFile missing")
    if str(state_file).startswith("/proc/"):
        return fail(
            "sessionStateFile points into /proc; sandbox must force file-based state delivery"
        )

    state = json.loads(Path(state_file).read_text(encoding="utf-8"))
    platform = (state.get("platforms") or {}).get(PLATFORM_KEY)
    if not isinstance(platform, dict):
        return fail(f"platform section missing: {PLATFORM_KEY}")

    cookies = {
        item["name"]: item["value"]
        for item in platform.get("cookies", [])
        if isinstance(item, dict) and item.get("name") and item.get("value") is not None
    }
    profile = platform.get("requestProfile") or {}
    headers = dict(profile.get("staticHeaders") or {})
    headers.update(profile.get("hostHeader") or {})
    if profile.get("referer"):
        headers["Referer"] = str(profile["referer"])
    for pair in profile.get("csrf") or []:
        if not isinstance(pair, dict):
            continue
        cookie_name = pair.get("cookie")
        header_name = pair.get("header")
        if cookie_name and header_name and cookie_name in cookies:
            headers[str(header_name)] = str(cookies[cookie_name])

    url = TEST_URL
    if not url.lower().startswith(("http://", "https://")):
        url = urljoin(str(profile.get("baseUrl") or "").rstrip("/") + "/", url.lstrip("/"))

    result = requests.request(
        METHOD,
        url,
        headers=headers,
        cookies=cookies,
        params=PARAMS or None,
        json=BODY,
        timeout=TIMEOUT,
        verify=not INSECURE,
    )
    expected = int(EXPECT) if EXPECT else None
    ok = result.status_code == expected if expected is not None else 200 <= result.status_code < 400
    payload = {
        "ok": ok,
        "status": result.status_code,
        "url": url,
        "cookie_names": sorted(cookies),
        "content_type": result.headers.get("content-type", ""),
        "content_length": len(result.content),
    }
    if SHOW_BODY:
        payload["body_prefix"] = result.text[:200]
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
'''


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"missing required env: {name}")
    return value


def _settings_error(settings: Any) -> str | None:
    missing = [
        key
        for key, value in (
            ("CONSOLE_PLATFORM_URL", settings.console_platform_url),
            ("REDIS_URL", settings.redis_url),
            ("INTERNAL_SERVICE_TOKEN", settings.internal_service_token),
        )
        if not value
    ]
    return f"missing settings: {', '.join(missing)}" if missing else None


def _build_bwrap(
    *,
    env: dict[str, str],
    session_dir: Path,
    work_dir: Path,
    test_env: dict[str, str],
) -> list[str]:
    return [
        "bwrap",
        "--unshare-all",
        "--share-net",
        "--die-with-parent",
        "--new-session",
        "--clearenv",
        "--setenv",
        "PATH",
        f"/opt/python3.12/bin:/usr/local/bin:/usr/bin:/bin:{session_dir}",
        "--setenv",
        "MUAD_SESSION_SOCKET",
        env["MUAD_SESSION_SOCKET"],
        *[
            item
            for key, value in test_env.items()
            for item in ("--setenv", key, value)
        ],
        "--ro-bind",
        "/usr",
        "/usr",
        "--ro-bind",
        "/lib",
        "/lib",
        "--ro-bind-try",
        "/lib64",
        "/lib64",
        "--ro-bind",
        "/bin",
        "/bin",
        "--ro-bind",
        "/etc",
        "/etc",
        "--ro-bind",
        "/opt",
        "/opt",
        "--dev",
        "/dev",
        "--size",
        "67108864",
        "--tmpfs",
        "/tmp",
        "--bind",
        str(session_dir),
        str(session_dir),
        "--bind",
        str(work_dir),
        "/work",
        "--chdir",
        "/work",
        "python",
        "/work/client.py",
    ]


async def main() -> int:
    if shutil.which("bwrap") is None:
        print("RESULT: FAIL bwrap not found in PATH", file=sys.stderr)
        return 1

    settings = process_settings()
    settings_error = _settings_error(settings)
    if settings_error:
        print(f"RESULT: FAIL {settings_error}", file=sys.stderr)
        return 1

    tenant_id = os.environ.get("TEST_TENANT_ID", "").strip() or settings.default_tenant_id
    actor_user_id = _required("TEST_ACTOR_USER_ID")
    platform_key = _required("TEST_PLATFORM_KEY")
    test_url = os.environ.get("TEST_URL", "").strip() or os.environ.get("TEST_PATH", "").strip()
    if not test_url:
        raise SystemExit("missing required env: TEST_URL or TEST_PATH")
    skill_name = os.environ.get("TEST_SKILL_NAME", "real-broker-probe").strip()
    execution_id = os.environ.get("TEST_EXECUTION_ID", "").strip() or str(uuid.uuid4())

    try:
        uuid.UUID(actor_user_id)
        uuid.UUID(execution_id)
    except ValueError as exc:
        print(f"RESULT: FAIL invalid UUID: {exc}", file=sys.stderr)
        return 1

    context = SkillPlatformContext(
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        execution_id=execution_id,
        execution_type="RUN",
        platform_keys=(platform_key,),
        console_url=settings.console_platform_url,
        redis_url=settings.redis_url,
        redis_mode=settings.redis_mode,
        redis_cluster_seeds=settings.redis_cluster_seeds,
        service_token=settings.internal_service_token,
    )

    test_env = {
        "TEST_PLATFORM_KEY": platform_key,
        "TEST_SKILL_NAME": skill_name,
        "TEST_URL": test_url,
        "TEST_PARAMS_JSON": os.environ.get("TEST_PARAMS_JSON", ""),
        "TEST_BODY_JSON": os.environ.get("TEST_BODY_JSON", ""),
        "TEST_METHOD": os.environ.get("TEST_METHOD", "GET"),
        "TEST_TIMEOUT_SEC": os.environ.get("TEST_TIMEOUT_SEC", "15"),
        "TEST_EXPECT_STATUS": os.environ.get("TEST_EXPECT_STATUS", ""),
        "TEST_INSECURE": os.environ.get("TEST_INSECURE", "0"),
        "TEST_SHOW_BODY": os.environ.get("TEST_SHOW_BODY", "0"),
    }

    with tempfile.TemporaryDirectory(prefix="real-broker-probe-") as work:
        work_dir = Path(work)
        (work_dir / "client.py").write_text(CLIENT, encoding="utf-8")

        try:
            async with skill_platform_environment(context, skill_name) as env:
                if not env.get("MUAD_SESSION_SOCKET"):
                    print(
                        "RESULT: FAIL broker returned no MUAD_SESSION_SOCKET",
                        file=sys.stderr,
                    )
                    return 1
                session_dir = Path(env["MUAD_SESSION_SOCKET"]).parent
                command = _build_bwrap(
                    env=env,
                    session_dir=session_dir,
                    work_dir=work_dir,
                    test_env=test_env,
                )
                result = await asyncio.to_thread(
                    subprocess.run,
                    command,
                    capture_output=True,
                    text=True,
                    timeout=float(test_env["TEST_TIMEOUT_SEC"]) + 10,
                )
        except Exception as exc:  # noqa: BLE001 - canary must report the real error
            print(f"RESULT: FAIL {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1

    print("bwrap_rc", result.returncode)
    print("stdout", result.stdout.strip())
    print("stderr", result.stderr.strip())
    ok = result.returncode == 0 and '"ok": true' in result.stdout
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
