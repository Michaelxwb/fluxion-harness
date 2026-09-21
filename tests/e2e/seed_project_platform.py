"""E2E 数据播种：项目平台 + 用户凭据（真实 PostgreSQL）。"""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid

from muad_common import SharedSettings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="seed_project_platform")
    parser.add_argument("--user-code", required=True)
    parser.add_argument("--platform-key", required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:4190")
    parser.add_argument("--platform-name", default="E2E Platform")
    parser.add_argument("--cleanup", action="store_true")
    parser.add_argument("--no-credential", action="store_true")
    parser.add_argument("--dump-credential", action="store_true")
    return parser


async def _cleanup(connection, tenant_id: str, platform_key: str) -> None:
    for statement in (
        "DELETE FROM control.user_credential_ref WHERE tenant_id = :tenant_id AND platform_id IN "
        "(SELECT id FROM control.project_platform WHERE tenant_id = :tenant_id AND key = :platform_key)",
        "DELETE FROM control.shared_credential_ref WHERE tenant_id = :tenant_id AND platform_id IN "
        "(SELECT id FROM control.project_platform WHERE tenant_id = :tenant_id AND key = :platform_key)",
        "DELETE FROM control.project_platform WHERE tenant_id = :tenant_id AND key = :platform_key",
    ):
        await connection.execute(
            text(statement),
            {"tenant_id": tenant_id, "platform_key": platform_key},
        )


async def _seed(
    connection,
    tenant_id: str,
    user_id: uuid.UUID,
    platform_key: str,
    platform_name: str,
    base_url: str,
    with_credential: bool,
) -> None:
    platform_id = uuid.uuid4()
    await connection.execute(
        text(
            "INSERT INTO control.project_platform "
            "(id, tenant_id, key, name, resolver_type, resolver_config_json, adapter_key, "
            " adapter_config_json, credential_mode, enabled) "
            "VALUES (:id, :tenant_id, :key, :name, 'BASE_URL', "
            " jsonb_build_object('base_url', (:base_url)::text), 'generic-http', "
            " '{\"auth_scheme\": \"bearer\"}'::jsonb, 'USER_ONLY', true)"
        ),
        {
            "id": platform_id,
            "tenant_id": tenant_id,
            "key": platform_key,
            "name": platform_name,
            "base_url": base_url,
        },
    )
    if with_credential:
        await connection.execute(
            text(
                "INSERT INTO control.user_credential_ref "
                "(id, tenant_id, user_id, platform_id, credential_json, credential_schema_version, status) "
                "VALUES (:id, :tenant_id, :user_id, :platform_id, "
                "'{\"token\": \"e2e-token\"}'::jsonb, '1', 'ACTIVE')"
            ),
            {"id": uuid.uuid4(), "tenant_id": tenant_id, "user_id": user_id, "platform_id": platform_id},
        )


async def _ensure_user(connection, tenant_id: str, user_code: str) -> uuid.UUID:
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
                "VALUES (:id, :tenant_id, :user_code, 'Platform E2E User', 'ACTIVE')"
            ),
            {"id": user_id, "tenant_id": tenant_id, "user_code": user_code},
        )
    return user_id


async def _dump_credential(connection, tenant_id: str, platform_key: str) -> dict[str, object]:
    row = (
        await connection.execute(
            text(
                "SELECT credential_json, status FROM control.user_credential_ref "
                "WHERE tenant_id = :tenant_id AND is_deleted = false AND platform_id IN "
                "(SELECT id FROM control.project_platform "
                "WHERE tenant_id = :tenant_id AND key = :platform_key)"
            ),
            {"tenant_id": tenant_id, "platform_key": platform_key},
        )
    ).one_or_none()
    if row is None:
        return {"credential": None, "status": None}
    return {"credential": row[0], "status": row[1]}


async def main() -> int:
    args = _build_parser().parse_args()
    settings = SharedSettings()
    if not settings.database_url:
        raise SystemExit("DATABASE_URL not configured")
    engine = create_async_engine(settings.database_url)
    try:
        async with engine.begin() as connection:
            if args.dump_credential:
                payload = await _dump_credential(
                    connection, settings.default_tenant_id, args.platform_key
                )
                print(json.dumps(payload, ensure_ascii=False))
            elif args.cleanup:
                await _cleanup(connection, settings.default_tenant_id, args.platform_key)
            else:
                user_id = await _ensure_user(connection, settings.default_tenant_id, args.user_code)
                await _cleanup(connection, settings.default_tenant_id, args.platform_key)
                await _seed(
                    connection,
                    settings.default_tenant_id,
                    user_id,
                    args.platform_key,
                    args.platform_name,
                    args.base_url,
                    with_credential=not args.no_credential,
                )
    finally:
        await engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
