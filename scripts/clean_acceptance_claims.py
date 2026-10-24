#!/usr/bin/env python3
"""Clear stale acceptance Redis claims when no acceptance run is active.

The acceptance datastore mechanism claims Redis DBs 10-15 with `SET NX EX 7200`
and releases its own claim on normal teardown. A SIGKILL'd run leaves the claim
until TTL expiry. This helper only clears claims when no pytest/Playwright
acceptance process is running, so it cannot steal a live run's slot.

Usage:
  uv run python scripts/clean_acceptance_claims.py          # safe: skip if active
  uv run python scripts/clean_acceptance_claims.py --force  # explicit manual clean
"""

from __future__ import annotations

import argparse
import subprocess
import sys

import redis

CLAIM_KEY = "muad:acceptance:db-claim"
DB_RANGE = range(10, 16)
ACTIVE_PATTERNS = (
    "pytest.*tests/acceptance",
    "playwright test",
    "playwright.*webchat",
)


def _active_runs() -> list[str]:
    found: list[str] = []
    for pattern in ACTIVE_PATTERNS:
        result = subprocess.run(
            ["pgrep", "-fl", pattern], capture_output=True, text=True
        )
        for line in result.stdout.splitlines():
            if line.strip():
                found.append(line.strip())
    return found


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="即使检测到活跃验收进程也清理")
    args = parser.parse_args()

    active = _active_runs()
    if active and not args.force:
        print("检测到活跃验收进程，跳过清理（避免抢走正在使用的 Redis 号位）：")
        for line in active:
            print(f"  {line}")
        return 0

    cleared: list[int] = []
    for index in DB_RANGE:
        client = redis.Redis(host="127.0.0.1", port=6379, db=index)
        try:
            if client.delete(CLAIM_KEY):
                cleared.append(index)
        finally:
            client.close()
    print(f"已清理 Redis 10–15 号 claim：{cleared or '无残留'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
