"""Redis Cluster 命令面/key 契约探针（11 项，只读判定 + 临时键清理）。

与 `scripts/probe_redis_cluster.py`（部署前置：节点可达/版本/槽位/内存）互补：本脚本验证
业务实际用到的命令面 —— 同 slot `XREADGROUP`/`XAUTOCLAIM`/双 key `EVAL`、跨节点 `PUBLISH`、
`SCAN` 全 primary、跨 slot 拒绝，以及三族 key 的 hash tag 布局。

用法：
    python scripts/probe_redis_cluster_commands.py \
        --url 'redis://:pwd@10.74.145.45:6393/0' \
        --seeds 10.74.145.45:6393,10.74.145.46:6393,10.74.145.47:6393

退出码：0 全部通过；1 有失败项；2 参数错误。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import cast

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from muad_common.redis_client import create_redis_client  # noqa: E402
from redis.asyncio import RedisCluster  # noqa: E402
from redis.exceptions import RedisClusterException, RedisError  # noqa: E402

from tests.acceptance.redis_cluster.probe_contract import run_command_probe  # noqa: E402

EXPECTED_CHECKS = 11


def _parse_seeds(raw: str) -> list[str]:
    seeds = [item.strip() for item in raw.split(",") if item.strip()]
    if not seeds:
        raise ValueError("at least one seed is required")
    return seeds


async def _run(url: str, seeds: list[str]) -> int:
    client = cast(
        RedisCluster,
        create_redis_client(url, mode="cluster", seeds=seeds, decode_responses=True),
    )
    try:
        results = await run_command_probe(client)
    except (RedisError, RedisClusterException, OSError) as exc:
        print(f"[FAIL] cluster unreachable ({url}; seeds={seeds}): {exc}")
        return 1
    finally:
        await client.aclose()

    for result in results:
        print(f"[{'PASS' if result.ok else 'FAIL'}] {result.name} - {result.detail}")
    failed = [result.name for result in results if not result.ok]
    if failed:
        print(f"\n{len(failed)}/{len(results)} check(s) failed: {', '.join(failed)}")
        return 1
    if len(results) != EXPECTED_CHECKS:
        print(f"\nprobe returned {len(results)} checks, expected {EXPECTED_CHECKS}")
        return 1
    print(f"\nall {len(results)} checks passed")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Redis Cluster command/key contract probe")
    parser.add_argument("--url", required=True, help="DSN (credentials/TLS 来源)")
    parser.add_argument(
        "--seeds", required=True, help="comma separated host:port discovery seeds"
    )
    args = parser.parse_args(argv)
    try:
        seeds = _parse_seeds(args.seeds)
    except ValueError as exc:
        print(f"invalid --seeds: {exc}", file=sys.stderr)
        return 2
    return asyncio.run(_run(args.url, seeds))


if __name__ == "__main__":
    raise SystemExit(main())
