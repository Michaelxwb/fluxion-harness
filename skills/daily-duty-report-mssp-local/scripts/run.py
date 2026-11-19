#!/usr/bin/env python3
"""Argv entrypoint for the local MSSP daily-duty-report regression skill.

Usage:
  python scripts/run.py --company-id 1001 --date 2026-11-18 --no-wecom-push

When the platform omits ``--company-id``, the same business fields are read
from stdin (Fire envelope or direct JSON) — the same shape accepted by
``scripts/cron_entry.py``. stdout is the report body only; diagnostics go to
stderr. The shared fake platform base URL is taken from ``MSSP_BASE_URL`` (default
``http://127.0.0.1:18080``).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import cron_entry  # noqa: E402
import report  # noqa: E402
import shared  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a local fake-MSSP duty report.")
    parser.add_argument("--company-id", default=None)
    parser.add_argument("--date", default=None, help="YYYY-MM-DD; defaults to yesterday")
    parser.add_argument(
        "--no-wecom-push",
        action="store_true",
        help="Skip the read-only WeCom group query",
    )
    return parser.parse_args(argv)


def _stdin_business() -> tuple[str, str | None, bool] | None:
    """平台未给 argv 时，从 stdin 读取与 cron_entry 相同的业务输入。"""
    if sys.stdin.isatty():
        return None
    raw = sys.stdin.read()
    if not raw.strip():
        return None
    try:
        payload: Any = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise shared.FetchError("stdin 不是合法 JSON") from exc
    if not isinstance(payload, dict):
        raise shared.FetchError("stdin JSON 必须是对象")
    return cron_entry.parse_business(payload)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    company_id = args.company_id
    date = args.date
    # 平台调度的外部投递由平台负责；Skill 自己不要在定时任务里再发一次企微。
    # 手工 CLI 没有 MUAD_TASK_ID，仍可显式测试 Skill 自己的查群/推送。
    no_wecom_push = args.no_wecom_push or bool(os.environ.get("MUAD_TASK_ID"))
    try:
        if not company_id:
            from_stdin = _stdin_business()
            if from_stdin is not None:
                company_id, stdin_date, stdin_no_push = from_stdin
                date = date or stdin_date
                no_wecom_push = no_wecom_push or stdin_no_push
        if not company_id:
            raise shared.FetchError("缺少 --company-id 或 stdin 的 company_id")
        result = report.generate(company_id, date, no_wecom_push=no_wecom_push)
    except shared.FetchError as exc:
        return report.fatal(str(exc))
    sys.stdout.write(result["body"] + "\n")
    if result["tail"]:
        sys.stdout.write(result["tail"] + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
