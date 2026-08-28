#!/usr/bin/env python3
"""TASK-001（phase1-closure）存量检查脚本：报告 agent_definition spec 中的 legacy 键。

P1C-01 SoT 收口的只读巡检：扫描 Registry 中 agent_definition 资源的 spec_json，
报告仍含 legacy ``lifecycle``/``visibility`` 键的条目（只报告，不改写）。收口后
这类键由 typed model 读取时剥离，不影响运行语义；本报告用于评估存量清理时机。

用法（RegistryStore 契约按租户枚举，租户需显式给出）：
    python3 scripts/audit_legacy_spec_keys.py --sqlite path/to/fluxion.db \
        --tenant tenant-a --tenant tenant-b
    python3 scripts/audit_legacy_spec_keys.py --postgres "$DSN" --tenant tenant-a

输出：逐条 `tenant/id@version status=... legacy=[...]`；末行汇总；退出码 0。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend" / "src"))

from fluxion.registry import PostgreSQLRegistryStore, SQLiteRegistryStore
from fluxion.resources import ResourceKind

_LEGACY_KEYS = ("lifecycle", "visibility")
_PAGE_SIZE = 200


async def _audit_tenant(store: object, tenant_id: str) -> int:
    findings = 0
    offset = 0
    total: int | None = None
    while total is None or offset < total:
        page, total = await store.list_resources(  # type: ignore[attr-defined]
            ResourceKind.AGENT_DEFINITION,
            tenant_id=tenant_id,
            offset=offset,
            limit=_PAGE_SIZE,
        )
        for item in page:
            legacy = [key for key in _LEGACY_KEYS if key in item.spec_json]
            # TASK-002 巡检：type=tool 的 capability 携带 plugin: 前缀 ref。
            plugin_tools = [
                cap.get("capability_ref")
                for cap in item.spec_json.get("capabilities", [])
                if isinstance(cap, dict)
                and cap.get("type") == "tool"
                and str(cap.get("capability_ref", "")).startswith("plugin:")
            ]
            if legacy or plugin_tools:
                findings += 1
                detail = []
                if legacy:
                    detail.append(f"legacy={legacy}")
                if plugin_tools:
                    detail.append(f"plugin_tool_refs={plugin_tools}")
                print(
                    f"{tenant_id}/{item.id}@{item.version} "
                    f"status={item.status.value} {' '.join(detail)}"
                )
        offset += len(page)
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--sqlite", help="SQLite Registry 数据库文件路径")
    source.add_argument("--postgres", help="PostgreSQL DSN（生产巡检）")
    parser.add_argument(
        "--tenant", action="append", required=True, help="要巡检的租户（可重复）"
    )
    args = parser.parse_args()

    async def run() -> int:
        if args.sqlite:
            store = SQLiteRegistryStore(f"sqlite+aiosqlite:///{args.sqlite}")
        else:
            store = PostgreSQLRegistryStore(args.postgres or "")
        await store.initialize()
        try:
            findings = 0
            for tenant_id in args.tenant:
                findings += await _audit_tenant(store, tenant_id)
            print(f"audit done: {findings} 条含 legacy 键（tenants={args.tenant}）")
            return 0
        finally:
            await store.close()

    return asyncio.run(run())


if __name__ == "__main__":
    raise SystemExit(main())
