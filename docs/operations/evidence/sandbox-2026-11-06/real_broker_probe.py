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
  TEST_EXPECT_STATUS   exact expected HTTP status; default accepts 2xx; 'any' observes only
  TEST_REQUIRE_JSON    set to 1 to require a JSON response
  TEST_EXPECT_JSON     nonempty JSON object of expected top-level response fields
  TEST_HEADERS_JSON    additional non-credential request headers as a JSON object
  TEST_COOKIE_MODE     valid (default), none, or invalid; controls change only Cookie
  TEST_AUTH_COOKIE     cookie replaced in invalid mode; defaults to soc-token
  TEST_INSECURE        set to 1 to skip TLS verification (test only)
  TEST_SHOW_BODY       set to 1 to print a redacted JSON prefix (up to 200 characters)

Redirects are never followed. PASS applies only to the reported check_scope;
HTTP/JSON success alone does not establish business authorization. 'any' reports
RESULT: OBSERVED and must not be counted as an authentication pass.

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

CLIENT = r"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from muad_logging.redaction import redact_value

PLATFORM_KEY = os.environ["TEST_PLATFORM_KEY"]
SKILL_NAME = os.environ["TEST_SKILL_NAME"]
TEST_URL = os.environ.get("TEST_URL", "").strip() or os.environ.get("TEST_PATH", "").strip()
METHOD = os.environ.get("TEST_METHOD", "GET").upper()
TIMEOUT = float(os.environ.get("TEST_TIMEOUT_SEC", "15"))
EXPECT = os.environ.get("TEST_EXPECT_STATUS", "").strip()
INSECURE = os.environ.get("TEST_INSECURE") == "1"
SHOW_BODY = os.environ.get("TEST_SHOW_BODY") == "1"
REQUIRE_JSON = os.environ.get("TEST_REQUIRE_JSON") == "1"
COOKIE_MODE = os.environ.get("TEST_COOKIE_MODE", "valid")


def json_object(name: str) -> dict[str, Any] | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a JSON object")
    return value


PARAMS = json_object("TEST_PARAMS_JSON")
BODY = json_object("TEST_BODY_JSON")
EXPECTED_JSON = json_object("TEST_EXPECT_JSON")


def fail(message: str) -> int:
    print(json.dumps({"ok": False, "error": message}, ensure_ascii=False))
    return 1


def read_platform() -> dict[str, Any]:
    raw = subprocess.check_output(
        ["session-manager", "get-state", "--skill-name", SKILL_NAME],
        text=True,
        timeout=TIMEOUT,
    )
    response = json.loads(raw)
    state_file = response.get("sessionStateFile")
    if not state_file:
        raise ValueError("sessionStateFile missing")
    if str(state_file).startswith("/proc/"):
        raise ValueError(
            "sessionStateFile points into /proc; sandbox must force file-based state delivery"
        )

    state = json.loads(Path(state_file).read_text(encoding="utf-8"))
    platform = (state.get("platforms") or {}).get(PLATFORM_KEY)
    if not isinstance(platform, dict):
        raise ValueError("platform section missing")
    return platform


def request_headers(profile: dict[str, Any], cookies: dict[str, str]) -> dict[str, str]:
    headers = dict(profile.get("staticHeaders") or {})
    headers.update(profile.get("hostHeader") or {})
    if profile.get("referer"):
        headers["Referer"] = str(profile["referer"])
    extra = json_object("TEST_HEADERS_JSON") or {}
    blocked = {"cookie", "set-cookie", "authorization", "proxy-authorization"}
    if any(key.lower() in blocked or not isinstance(value, str) for key, value in extra.items()):
        raise ValueError("TEST_HEADERS_JSON must contain only non-credential string headers")
    headers.update(extra)
    for pair in profile.get("csrf") or []:
        if not isinstance(pair, dict):
            continue
        cookie_name = pair.get("cookie")
        header_name = pair.get("header")
        if cookie_name and header_name and cookie_name in cookies:
            headers[str(header_name)] = str(cookies[cookie_name])
    return {key: value for key, value in headers.items() if key.lower() not in blocked}


def request_cookies(cookies: dict[str, str]) -> dict[str, str]:
    if COOKIE_MODE == "none":
        return {}
    if COOKIE_MODE == "valid":
        return dict(cookies)
    if COOKIE_MODE != "invalid":
        raise ValueError("TEST_COOKIE_MODE must be valid, none, or invalid")
    auth_cookie = os.environ.get("TEST_AUTH_COOKIE", "soc-token")
    if auth_cookie not in cookies:
        raise ValueError("TEST_AUTH_COOKIE is absent from the broker session")
    return {**cookies, auth_cookie: "invalid-sandbox-auth-control"}


def request_url(profile: dict[str, Any]) -> str:
    url = TEST_URL
    if not url.lower().startswith(("http://", "https://")):
        url = urljoin(str(profile.get("baseUrl") or "").rstrip("/") + "/", url)
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        raise ValueError("TEST_URL must resolve to an HTTP(S) URL without embedded credentials")
    return url


def response_summary(
    result: requests.Response, cookies: dict[str, str], headers: dict[str, str]
) -> dict[str, Any]:
    try:
        body = result.json()
        is_json = True
    except ValueError:
        body = None
        is_json = False
    if EXPECTED_JSON == {}:
        raise ValueError("TEST_EXPECT_JSON must not be empty")
    matches = EXPECTED_JSON is None or (
        isinstance(body, dict)
        and all(key in body and body[key] == value for key, value in EXPECTED_JSON.items())
    )
    status_ok = (
        result.status_code == int(EXPECT) if EXPECT and EXPECT != "any"
        else 200 <= result.status_code < 300
    )
    scope = "business_json" if EXPECTED_JSON is not None else "http_json" if REQUIRE_JSON else "http_status"
    parts = urlsplit(result.url)
    payload = {
        "ok": None if EXPECT == "any" else status_ok and (not REQUIRE_JSON or is_json) and matches,
        "check_scope": "observation" if EXPECT == "any" else scope,
        "cookie_mode": COOKIE_MODE,
        "status": result.status_code,
        "url": urlunsplit((parts.scheme, parts.netloc, parts.path, "", "")),
        "cookie_names": sorted(request_cookies(cookies)),
        "content_type": result.headers.get("content-type", ""),
        "content_length": len(result.content),
        "is_json": is_json,
    }
    if SHOW_BODY:
        preview = json.dumps(redact_value(body), ensure_ascii=False) if is_json else "[non-JSON body omitted]"
        for value in sorted({*cookies.values(), *headers.values()}, key=len, reverse=True):
            if value:
                preview = preview.replace(value, "***")
        payload["body_prefix"] = preview[:200]
    return payload


def main() -> int:
    platform = read_platform()
    cookies = {
        str(item["name"]): str(item["value"])
        for item in platform.get("cookies", [])
        if isinstance(item, dict) and item.get("name") and item.get("value") is not None
    }
    headers = request_headers(platform.get("requestProfile") or {}, cookies)

    result = requests.request(
        METHOD,
        request_url(platform.get("requestProfile") or {}),
        headers=headers,
        cookies=request_cookies(cookies),
        params=PARAMS or None,
        json=BODY,
        timeout=TIMEOUT,
        verify=not INSECURE,
        allow_redirects=False,
    )
    payload = response_summary(result, cookies, headers)
    print(json.dumps(payload, ensure_ascii=False))
    return 1 if payload["ok"] is False else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, requests.RequestException, subprocess.SubprocessError) as exc:
        sys.exit(fail(type(exc).__name__))
"""


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


def _runtime_mounts() -> list[str]:
    return [
        value
        for option, path in (
            ("--ro-bind", "/usr"),
            ("--ro-bind", "/lib"),
            ("--ro-bind-try", "/lib64"),
            ("--ro-bind", "/bin"),
            ("--ro-bind", "/etc"),
            ("--ro-bind", "/opt"),
        )
        for value in (option, path, path)
    ]


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
        *[item for key, value in test_env.items() for item in ("--setenv", key, value)],
        *_runtime_mounts(),
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


def _test_environment(platform_key: str, skill_name: str, test_url: str) -> dict[str, str]:
    defaults = {
        "TEST_PARAMS_JSON": "",
        "TEST_BODY_JSON": "",
        "TEST_METHOD": "GET",
        "TEST_TIMEOUT_SEC": "15",
        "TEST_EXPECT_STATUS": "",
        "TEST_INSECURE": "0",
        "TEST_SHOW_BODY": "0",
        "TEST_REQUIRE_JSON": "0",
        "TEST_EXPECT_JSON": "",
        "TEST_HEADERS_JSON": "",
        "TEST_COOKIE_MODE": "valid",
        "TEST_AUTH_COOKIE": "soc-token",
    }
    return {
        "TEST_PLATFORM_KEY": platform_key,
        "TEST_SKILL_NAME": skill_name,
        "TEST_URL": test_url,
        **{key: os.environ.get(key, default) for key, default in defaults.items()},
    }


async def _execute_probe(
    context: SkillPlatformContext, test_env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory(prefix="real-broker-probe-") as work:
        work_dir = Path(work)
        (work_dir / "client.py").write_text(CLIENT, encoding="utf-8")

        try:
            async with skill_platform_environment(context, test_env["TEST_SKILL_NAME"]) as env:
                if not env.get("MUAD_SESSION_SOCKET"):
                    raise RuntimeError("broker returned no MUAD_SESSION_SOCKET")
                session_dir = Path(env["MUAD_SESSION_SOCKET"]).parent
                command = _build_bwrap(
                    env=env,
                    session_dir=session_dir,
                    work_dir=work_dir,
                    test_env=test_env,
                )
                return await asyncio.to_thread(
                    subprocess.run,
                    command,
                    capture_output=True,
                    text=True,
                    timeout=float(test_env["TEST_TIMEOUT_SEC"]) + 10,
                )
        except subprocess.TimeoutExpired:
            raise RuntimeError("probe subprocess exceeded its deadline") from None


def _report_result(result: subprocess.CompletedProcess[str]) -> int:
    print("bwrap_rc", result.returncode)
    print("stdout", result.stdout.strip())
    print("stderr", result.stderr.strip())
    try:
        payload = json.loads(result.stdout)
    except ValueError:
        print("RESULT: FAIL invalid probe output")
        return 1
    if result.returncode != 0 or not isinstance(payload, dict):
        print("RESULT: FAIL")
        return 1
    if payload.get("check_scope") == "observation" and payload.get("ok") is None:
        print("RESULT: OBSERVED (authentication not asserted)")
        return 0
    ok = payload.get("ok") is True
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


async def main() -> int:
    if shutil.which("bwrap") is None:
        print("RESULT: FAIL bwrap not found in PATH", file=sys.stderr)
        return 1
    settings = process_settings()
    settings_error = _settings_error(settings)
    if settings_error:
        print(f"RESULT: FAIL {settings_error}", file=sys.stderr)
        return 1
    actor_user_id = _required("TEST_ACTOR_USER_ID")
    platform_key = _required("TEST_PLATFORM_KEY")
    test_url = os.environ.get("TEST_URL", "").strip() or _required("TEST_PATH")
    execution_id = os.environ.get("TEST_EXECUTION_ID", "").strip() or str(uuid.uuid4())
    uuid.UUID(actor_user_id)
    uuid.UUID(execution_id)
    context = SkillPlatformContext(
        tenant_id=os.environ.get("TEST_TENANT_ID", "").strip() or settings.default_tenant_id,
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
    test_env = _test_environment(
        platform_key, os.environ.get("TEST_SKILL_NAME", "real-broker-probe").strip(), test_url
    )
    try:
        return _report_result(await _execute_probe(context, test_env))
    except Exception as exc:  # noqa: BLE001 - report failure without credential-bearing details
        print(f"RESULT: FAIL {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
