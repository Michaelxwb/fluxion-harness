#!/usr/bin/env python3
"""ASYNC batch sample over the fake business platform.

Parent mode (no ``target`` in the input) resolves targets, either from the input
``targets`` list or from the platform's ``/api/v1/targets`` endpoint, and prints
``{"batch": {...}}`` so the Worker fans out one child Task per item.

Child mode (Worker passes one item with ``target``) calls the real MSSW-shaped
endpoints (health, CSRF-protected ping, targets), verifies the ``Traceid`` echo,
and writes a JSON + CSV file into ``SKILL_OUTPUT_DIR``.

``offline: true`` skips platform calls so the same Skill can be exercised by the
Worker batch acceptance without a Console session; the direct fake-platform
smoke keeps the default online path.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

import requests

SKILL_NAME = "fake-platform-async-batch"
PLATFORM = "smoke_platform"
HEALTH_PATH = "/v1/rtt"
PING_PATH = "/api/v1/ping"
TARGETS_PATH = "/api/v1/targets"
DEFAULT_CONCURRENCY = 2
DEFAULT_AGGREGATE = "BEST_EFFORT"


def _read_input() -> dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("skill input must be a JSON object")
    return payload


def _resolve_state_file() -> str:
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


def _platform_state() -> tuple[str, dict[str, str]]:
    state = json.loads(Path(_resolve_state_file()).read_text(encoding="utf-8"))
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


def _get_json(base_url: str, path: str, headers: dict[str, str]) -> dict[str, Any]:
    response = requests.get(base_url + path, headers=headers, timeout=30)
    if response.status_code >= 400:
        raise RuntimeError(f"{path} returned HTTP {response.status_code}")
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError(f"{path} returned a non-object payload")
    return payload


def _targets_from_platform() -> list[dict[str, Any]]:
    base_url, headers = _platform_state()
    headers["Traceid"] = uuid.uuid4().hex
    payload = _get_json(base_url, TARGETS_PATH, headers)
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        raise RuntimeError("/api/v1/targets returned no items")
    targets: list[dict[str, Any]] = []
    for index, value in enumerate(items, start=1):
        if not isinstance(value, dict):
            continue
        name = str(value.get("name") or value.get("id") or f"target-{index}")
        targets.append(
            {
                "target": name,
                "target_id": value.get("id"),
                "item_key": f"{index}-{_safe_name(name)}",
            }
        )
    if not targets:
        raise RuntimeError("/api/v1/targets returned no usable targets")
    return targets


def _explicit_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw = payload.get("targets")
    if not isinstance(raw, list) or not raw:
        raise ValueError("targets must be a non-empty list")
    offline = bool(payload.get("offline"))
    fail_targets = {str(item) for item in (payload.get("fail_targets") or []) if str(item)}
    items: list[dict[str, Any]] = []
    for index, value in enumerate(raw, start=1):
        if isinstance(value, str):
            item: dict[str, Any] = {"target": value}
        elif isinstance(value, dict):
            item = dict(value)
            item.setdefault(
                "target",
                str(item.get("name") or item.get("id") or f"target-{index}"),
            )
        else:
            raise ValueError(f"targets[{index - 1}] must be a string or object")
        if offline:
            item.setdefault("offline", True)
        if str(item["target"]) in fail_targets:
            item["simulate_failure"] = True
        item.setdefault("item_key", f"{index}-{_safe_name(str(item['target']))}")
        items.append(item)
    return items


def _batch_plan(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("targets") is not None:
        items = _explicit_items(payload)
    elif bool(payload.get("offline")):
        raise ValueError("offline parent mode requires an explicit targets list")
    else:
        items = _targets_from_platform()
    concurrency = payload.get("max_concurrency", DEFAULT_CONCURRENCY)
    if isinstance(concurrency, bool) or not isinstance(concurrency, int) or concurrency < 1:
        raise ValueError("max_concurrency must be a positive integer")
    aggregate = str(payload.get("aggregate_mode") or DEFAULT_AGGREGATE)
    if aggregate not in {"ALL", "BEST_EFFORT"}:
        raise ValueError("aggregate_mode must be ALL or BEST_EFFORT")
    return {
        "batch": {
            "items": items,
            "max_concurrency": concurrency,
            "aggregate_mode": aggregate,
        }
    }


def _safe_name(target: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", target).strip("-._")
    return (cleaned or "target")[:80]


def _output_dir() -> Path:
    raw = os.environ.get("SKILL_OUTPUT_DIR", "").strip()
    target = Path(raw) if raw else Path.cwd() / "outputs"
    target.mkdir(parents=True, exist_ok=True)
    return target


def _checksum(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _write_reports(
    target: str, trace: str, platform_data: dict[str, Any]
) -> list[dict[str, Any]]:
    output = _output_dir()
    stem = f"report-{_safe_name(target)}"
    payload = {
        "ok": True,
        "target": target,
        "trace_id": trace,
        "platform": PLATFORM,
        "platform_data": platform_data,
    }
    json_bytes = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    (output / f"{stem}.json").write_bytes(json_bytes)

    ping_code = ""
    if isinstance(platform_data.get("ping"), dict):
        ping_code = str(platform_data["ping"].get("code", ""))
    csv_rows = [
        ["target", "ok", "trace_id", "platform_code"],
        [target, "true", trace, ping_code],
    ]
    csv_buffer = []
    for row in csv_rows:
        csv_buffer.append(",".join(_csv_cell(cell) for cell in row))
    csv_bytes = ("\n".join(csv_buffer) + "\n").encode("utf-8")
    (output / f"{stem}.csv").write_bytes(csv_bytes)

    return [
        {
            "relative_path": f"{stem}.json",
            "size": len(json_bytes),
            "checksum": _checksum(json_bytes),
        },
        {
            "relative_path": f"{stem}.csv",
            "size": len(csv_bytes),
            "checksum": _checksum(csv_bytes),
        },
    ]


def _csv_cell(value: str) -> str:
    escaped = str(value).replace('"', '""')
    return f'"{escaped}"'


def _child_result(payload: dict[str, Any]) -> dict[str, Any]:
    target = str(payload.get("target") or payload.get("id") or "unknown")
    if payload.get("simulate_failure"):
        raise RuntimeError(f"simulated failure for target {target}")
    trace = uuid.uuid4().hex
    platform_data: dict[str, Any] = {"offline": bool(payload.get("offline"))}
    if not bool(payload.get("offline")):
        base_url, headers = _platform_state()
        headers["Traceid"] = trace
        platform_data = {
            "health": _get_json(base_url, HEALTH_PATH, headers),
            "ping": _get_json(base_url, PING_PATH, headers),
            "targets": _get_json(base_url, TARGETS_PATH, headers),
        }
        ping = platform_data.get("ping")
        if not isinstance(ping, dict) or ping.get("traceId") != trace:
            raise RuntimeError("Traceid was not echoed by the platform")
    files = _write_reports(target, trace, platform_data)
    return {
        "ok": True,
        "target": target,
        "trace_id": trace,
        "platform": PLATFORM,
        "offline": bool(payload.get("offline")),
        "files": files,
    }


def main() -> int:
    try:
        payload = _read_input()
        if "target" in payload:
            print(json.dumps(_child_result(payload), ensure_ascii=False))
        else:
            print(json.dumps(_batch_plan(payload), ensure_ascii=False))
        return 0
    except Exception as error:  # noqa: BLE001 - fail loud: stderr + non-zero exit
        print(
            json.dumps({"ok": False, "error": f"{type(error).__name__}: {error}"}, ensure_ascii=False),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
