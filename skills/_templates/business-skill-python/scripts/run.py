#!/usr/bin/env python3
"""Business-skill template: session-manager state -> requests call -> bounded output.

Copy this file into a Skill package's ``scripts/`` directory and edit the three
constants.  It uses only the standard library plus ``requests``.

- In a sandbox/Run, ``session-manager`` is injected by the platform and returns
  the current execution's session state file.
- For local debugging without a runtime, set ``MUAD_LOCAL_SESSION_STATE`` to a
  state file with the same shape (see tools/fake-business-platform/README.md).

The contract: platform cookies and ``requestProfile.baseUrl`` come from the
session state; the Skill never hardcodes credentials, never logs them, writes
deliverables to ``SKILL_OUTPUT_DIR`` and durable state to ``SKILL_DATA_DIR``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import requests

# --- edit these three values -------------------------------------------------
PLATFORM = "example_platform"
SKILL_NAME = "business-skill-python-template"
EXAMPLE_PATH = "/api/v1/example"
# -----------------------------------------------------------------------------


def resolve_state_file() -> str:
    local = os.environ.get("MUAD_LOCAL_SESSION_STATE", "").strip()
    if not os.environ.get("MUAD_SESSION_SOCKET") and local and Path(local).is_file():
        return local
    try:
        completed = subprocess.run(
            ["session-manager", "get-state", "--skill-name", SKILL_NAME],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError(f"session-manager could not run: {error}") from error
    if completed.returncode != 0:
        detail = (completed.stderr or "").strip().splitlines()
        raise RuntimeError(
            "session-manager get-state failed: "
            + (detail[0][:200] if detail else f"exit {completed.returncode}")
        )
    try:
        return str(json.loads(completed.stdout)["sessionStateFile"])
    except (ValueError, KeyError, TypeError) as error:
        raise RuntimeError(f"session-manager output is invalid: {error}") from error


def load_platform_state(state_file: str) -> tuple[str, dict[str, str]]:
    try:
        state = json.loads(Path(state_file).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RuntimeError("session state is unreadable") from error
    section = (state.get("platforms") or {}).get(PLATFORM)
    if not isinstance(section, dict):
        raise RuntimeError(f"session state has no platform {PLATFORM}")
    profile = section.get("requestProfile") or {}
    base_url = str(profile.get("baseUrl") or "").rstrip("/")
    if not base_url:
        raise RuntimeError(f"platform {PLATFORM} has no baseUrl in session state")

    cookies = section.get("cookies") or []
    cookie_pairs = [
        f"{item.get('name')}={item.get('value')}"
        for item in cookies
        if isinstance(item, dict) and item.get("name") and item.get("value") is not None
    ]
    headers: dict[str, str] = {}
    if cookie_pairs:
        headers["Cookie"] = "; ".join(cookie_pairs)
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


def main() -> int:
    base_url, headers = load_platform_state(resolve_state_file())
    response = requests.get(base_url + EXAMPLE_PATH, headers=headers, timeout=30)
    if response.status_code >= 400:
        raise RuntimeError(f"{PLATFORM} returned HTTP {response.status_code}")

    result = {"ok": True, "status": response.status_code}
    out_dir = os.environ.get("SKILL_OUTPUT_DIR", "").strip()
    if out_dir:
        target = Path(out_dir)
        target.mkdir(parents=True, exist_ok=True)
        (target / "result.json").write_text(
            json.dumps(result, ensure_ascii=False), encoding="utf-8"
        )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:  # noqa: BLE001 - fail loud: stderr + non-zero exit
        print(json.dumps({"ok": False, "error": "处理失败，请稍后重试"}, ensure_ascii=False), file=sys.stderr)
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        sys.exit(1)
