"""E2E MCP 数据播种/校验/清理（真实 PostgreSQL）。"""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from typing import Any

from muad_common import SharedSettings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="seed_mcp_management")
    parser.add_argument("--key", required=True)
    parser.add_argument("--user-code")
    parser.add_argument("--ensure-user", action="store_true")
    parser.add_argument("--dump", action="store_true")
    parser.add_argument("--cleanup", action="store_true")
    return parser


async def _server_id(connection: Any, tenant_id: str, key: str) -> uuid.UUID | None:
    value = await connection.scalar(
        text(
            "SELECT id FROM control.mcp_server WHERE tenant_id = :tenant_id AND key = :key "
            "AND is_deleted = false"
        ),
        {"tenant_id": tenant_id, "key": key},
    )
    return value


async def _ensure_user(connection: Any, tenant_id: str, user_code: str) -> uuid.UUID:
    user_id = await connection.scalar(
        text(
            "SELECT id FROM control.platform_user WHERE tenant_id = :tenant_id "
            "AND user_code = :user_code AND is_deleted = false"
        ),
        {"tenant_id": tenant_id, "user_code": user_code},
    )
    if user_id is None:
        user_id = uuid.uuid4()
        await connection.execute(
            text(
                "INSERT INTO control.platform_user "
                "(id, tenant_id, user_code, display_name, status) "
                "VALUES (:id, :tenant_id, :user_code, 'MCP E2E User', 'ACTIVE')"
            ),
            {"id": user_id, "tenant_id": tenant_id, "user_code": user_code},
        )
    return user_id


async def _dump(connection: Any, tenant_id: str, key: str) -> dict[str, Any]:
    server_id = await _server_id(connection, tenant_id, key)
    if server_id is None:
        return {"server": None}
    row = (
        await connection.execute(
            text(
                "SELECT key, connection_status, tool_catalog_revision, tool_catalog_hash, "
                "jsonb_array_length(tool_catalog_json) AS tool_count, last_discovered_at, "
                "last_discovery_error, user_scope "
                "FROM control.mcp_server WHERE id = :server_id"
            ),
            {"server_id": server_id},
        )
    ).one()
    grants = (
        await connection.execute(
            text(
                "SELECT u.user_code FROM control.mcp_user_grant g "
                "JOIN control.platform_user u ON u.id = g.user_id "
                "WHERE g.mcp_server_id = :server_id AND g.is_deleted = false"
            ),
            {"server_id": server_id},
        )
    ).scalars().all()
    return {
        "server": {
            "key": row[0],
            "connection_status": row[1],
            "tool_catalog_revision": row[2],
            "tool_catalog_hash": row[3],
            "tool_count": row[4],
            "last_discovered_at": row[5].isoformat() if row[5] else None,
            "last_discovery_error": row[6],
            "user_scope": row[7],
        },
        "granted_users": list(grants),
    }


async def _cleanup(connection: Any, tenant_id: str, key: str, user_code: str | None) -> None:
    server_id = await _server_id(connection, tenant_id, key)
    if server_id is not None:
        for statement in (
            "DELETE FROM control.mcp_user_grant WHERE mcp_server_id = :server_id",
            "DELETE FROM control.agent_mcp_binding WHERE mcp_server_id = :server_id",
            "DELETE FROM control.mcp_server WHERE id = :server_id",
        ):
            await connection.execute(text(statement), {"server_id": server_id})
    if user_code:
        await connection.execute(
            text(
                "DELETE FROM control.platform_user WHERE tenant_id = :tenant_id "
                "AND user_code = :user_code"
            ),
            {"tenant_id": tenant_id, "user_code": user_code},
        )


async def main() -> int:
    args = _build_parser().parse_args()
    settings = SharedSettings()
    if not settings.database_url:
        raise SystemExit("DATABASE_URL not configured")
    engine = create_async_engine(settings.database_url)
    try:
        async with engine.begin() as connection:
            tenant_id = settings.default_tenant_id
            if args.ensure_user:
                if not args.user_code:
                    raise SystemExit("--ensure-user requires --user-code")
                user_id = await _ensure_user(connection, tenant_id, args.user_code)
                print(json.dumps({"user_id": str(user_id)}))
            elif args.dump:
                print(json.dumps(await _dump(connection, tenant_id, args.key), ensure_ascii=False))
            elif args.cleanup:
                await _cleanup(connection, tenant_id, args.key, args.user_code)
            else:
                raise SystemExit("choose one of --ensure-user / --dump / --cleanup")
    finally:
        await engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
