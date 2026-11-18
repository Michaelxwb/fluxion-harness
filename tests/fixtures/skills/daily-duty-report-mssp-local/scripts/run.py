#!/usr/bin/env python3
"""Argv entrypoint for the local MSSP daily-duty-report regression skill.

Usage:
  python scripts/run.py --company-id 1001 --date 2026-11-18 --no-wecom-push

stdout is the report body only; diagnostics go to stderr. The fake MSSP base
URL is taken from ``MSSP_BASE_URL`` (default ``http://127.0.0.1:18083``).
"""

from __future__ import annotations

import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import report  # noqa: E402
import shared  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a local fake-MSSP duty report.")
    parser.add_argument("--company-id", required=True)
    parser.add_argument("--date", default=None, help="YYYY-MM-DD; defaults to yesterday")
    parser.add_argument(
        "--no-wecom-push",
        action="store_true",
        help="Skip the read-only WeCom group query",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = report.generate(
            args.company_id,
            args.date,
            no_wecom_push=args.no_wecom_push,
        )
    except shared.FetchError as exc:
        return report.fatal(str(exc))
    sys.stdout.write(result["body"] + "\n")
    if result["tail"]:
        sys.stdout.write(result["tail"] + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
