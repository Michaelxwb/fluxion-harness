"""把单个 tenant 的 legacy RuntimeProfile 一次性迁移为 AgentDefinition。"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict

from fluxion.agents.migration import migrate_runtime_profiles
from fluxion.registry import PostgreSQLRegistryStore, RegistryStore, SQLiteRegistryStore


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--owner", default="migration:system")
    return parser.parse_args()


def _store(database_url: str) -> RegistryStore:
    if database_url.startswith("sqlite"):
        return SQLiteRegistryStore(database_url)
    if database_url.startswith("postgresql"):
        return PostgreSQLRegistryStore(database_url)
    raise ValueError("database-url must use sqlite or postgresql")


async def _run(args: argparse.Namespace) -> None:
    store = _store(args.database_url)
    await store.initialize()
    try:
        report = await migrate_runtime_profiles(
            store,
            tenant_id=args.tenant_id,
            owner=args.owner,
        )
    finally:
        await store.close()
    print(json.dumps(asdict(report), ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(_run(_arguments()))
