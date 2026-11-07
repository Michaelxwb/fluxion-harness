#!/usr/bin/env python3
"""smoke_platform smoke: health + CSRF-protected business calls through session state."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import requests

PLATFORM = "smoke_platform"
SKILL_NAME = "smoke-platform-sample"
HEALTH_PATH = "/v1/rtt"
PING_PATH = "/api/v1/ping"
TARGETS_PATH = "/api/v1/targets"


def resolve_state_file() -> str:
    local = os.environ.get("MUAD_LOCAL_SESSION_STATE", "").strip()
    if not os.environ.get("MUAD_SESSION_SOCKET") and local and Path(local).is_file():
        return local
    completed = subprocess.run(
        ["session-manager", "get-state", "--skill-name", SKILL_NAME],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or "").strip().splitlines()
        raise RuntimeError(
            "session-manager get-state failed: "
            + (detail[0][:200] if detail else f"exit {completed.returncode}")
        )
    return str(json.loads(completed.stdout)["sessionStateFile"])


def load_platform_state(state_file: str) -> tuple[str, dict[str, str]]:
    state = json.loads(Path(state_file).read_text(encoding="utf-8"))
    section = (state.get("platforms") or {}).get(PLATFORM)
    if not isinstance(section, dict):
        raise RuntimeError(f"session state has no platform {PLATFORM}")
    profile = section.get("requestProfile") or {}
    base_url = str(profile.get("baseUrl") or "").rstrip("/")
    if not base_url:
        raise RuntimeError(f"platform {PLATFORM} has no baseUrl")
    cookies = section.get("cookies") or []
    pairs = [
        f"{item.get('name')}={item.get('value')}"
        for item in cookies
        if isinstance(item, dict) and item.get("name") and item.get("value") is not None
    ]
    headers: dict[str, str] = {}
    if pairs:
        headers["Cookie"] = "; ".join(pairs)
    for mapping in profile.get("csrf") or []:
        if not isinstance(mapping, dict):
            continue
        cookie_name, header_name = mapping.get("cookie"), mapping.get("header")
        if not cookie_name or not header_name:
            continue
        value = next(
            (
                item.get("value")
                for item in cookies
                if isinstance(item, dict) and item.get("name") == cookie_name
            ),
            None,
        )
        if value:
            headers[str(header_name)] = str(value)
    for key, value in (profile.get("staticHeaders") or {}).items():
        headers[str(key)] = str(value)
    return base_url, headers


def get_json(base_url: str, path: str, headers: dict[str, str]) -> dict:
    response = requests.get(base_url + path, headers=headers, timeout=30)
    if response.status_code >= 400:
        raise RuntimeError(f"{path} returned HTTP {response.status_code}")
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError(f"{path} returned a non-object payload")
    return payload


def main() -> int:
    base_url, headers = load_platform_state(resolve_state_file())
    trace = uuid.uuid4().hex
    headers["Traceid"] = trace
    result = {
        "ok": True,
        "platform": PLATFORM,
        "health": get_json(base_url, HEALTH_PATH, headers),
        "ping": get_json(base_url, PING_PATH, headers),
        "targets": get_json(base_url, TARGETS_PATH, headers),
    }
    if result["ping"].get("traceId") != trace:
        raise RuntimeError("Traceid was not echoed by the platform")
    out_dir = os.environ.get("SKILL_OUTPUT_DIR", "").strip()
    if out_dir:
        target = Path(out_dir)
        target.mkdir(parents=True, exist_ok=True)
        (target / "smoke-platform-result.json").write_text(
            json.dumps(result, ensure_ascii=False), encoding="utf-8"
        )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:  # noqa: BLE001 - fail loud: stderr + non-zero exit
        print(
            json.dumps({"ok": False, "error": "smoke_platform check failed"}),
            file=sys.stderr,
        )
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        sys.exit(1)
