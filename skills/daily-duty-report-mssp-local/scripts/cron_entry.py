#!/usr/bin/env python3
"""stdin entrypoint for the local MSSP regression skill.

Two shapes are accepted:

* Fire envelope: ``{"mode": "SKILL_PLAN", "skill_id": "...", "input": {...},
  "context": {...}}`` — business fields are read from ``input`` (``context`` is
  only a fallback);
* direct business input: ``{"company_id": "1001", "date": "2026-11-18"}``.

The report itself is produced by the same ``report.generate`` used by
``scripts/run.py``.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import report  # noqa: E402
import shared  # noqa: E402


def _read_stdin_json() -> dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        raise shared.FetchError("stdin 为空，需要 Fire envelope 或业务 JSON")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise shared.FetchError("stdin 不是合法 JSON") from exc
    if not isinstance(payload, dict):
        raise shared.FetchError("stdin JSON 必须是对象")
    return payload


def _input_layer(payload: dict[str, Any]) -> dict[str, Any]:
    raw = payload.get("input")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise shared.FetchError("Fire envelope 的 input 不是合法 JSON") from exc
    return raw if isinstance(raw, dict) else {}


def business_input(payload: dict[str, Any]) -> dict[str, Any]:
    """Merge top-level, context and input; ``input`` has the final word."""
    merged: dict[str, Any] = {}
    for layer in (payload, payload.get("context"), _input_layer(payload)):
        if isinstance(layer, dict):
            merged.update(layer)
    return merged


def parse_business(payload: dict[str, Any]) -> tuple[str, str | None, bool]:
    data = business_input(payload)
    company_id = str(
        data.get("company_id") or data.get("companyId") or data.get("cid") or ""
    ).strip()
    if not company_id:
        raise shared.FetchError("缺少 company_id（Fire input 或顶层字段）")
    date = data.get("date") or data.get("report_date")
    date = str(date).strip() if date not in (None, "") else None
    if data.get("no_wecom_push") or data.get("no_push"):
        no_push = True
    elif data.get("wecom_push") is False:
        no_push = True
    else:
        no_push = False
    return company_id, date, no_push


def main() -> int:
    try:
        payload = _read_stdin_json()
        company_id, date, no_push = parse_business(payload)
        result = report.generate(company_id, date, no_wecom_push=no_push)
    except shared.FetchError as exc:
        return report.fatal(str(exc))
    sys.stdout.write(result["body"] + "\n")
    if result["tail"]:
        sys.stdout.write(result["tail"] + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
