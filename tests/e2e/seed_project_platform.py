"""E2E 数据播种：项目平台 + 用户凭据（真实 PostgreSQL）。

`--cleanup` 是**整场拆除**：删该 `--platform-key` 的平台与其凭据引用，**并删该
`--user-code` 的用户行**。播种路径内部只清平台（见 `_cleanup_platform`），
因为同一场景会用同一个 user_code 连续播多个平台（如 S-08），删用户会连带抹掉先播的凭据。
"""

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


async def _cleanup_platform(connection, tenant_id: str, platform_key: str) -> None:
    """删掉该平台及其凭据引用。**不动用户行** —— 播种路径也走这里做幂等预清。"""
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


async def _cleanup_user(connection, tenant_id: str, user_code: str) -> None:
    """删掉该用户及其全部引用行，只由 `--cleanup`（整场拆除）调用。

    先删引用再删用户：下列 6 张表对 `control.platform_user` 有物理外键，漏一张就会直接报错。
    user_code 不存在时是 no-op —— 调用方常拿它清一个从未播过的 code。
    """
    owner = (
        "SELECT id FROM control.platform_user "
        "WHERE tenant_id = :tenant_id AND user_code = :user_code"
    )
    for statement in (
        f"DELETE FROM control.user_credential_ref WHERE user_id IN ({owner})",
        f"DELETE FROM control.agent_access_grant WHERE user_id IN ({owner})",
        f"DELETE FROM control.skill_user_grant WHERE user_id IN ({owner})",
        f"DELETE FROM control.mcp_user_grant WHERE user_id IN ({owner})",
        f"DELETE FROM control.channel_identity WHERE platform_user_id IN ({owner})",
        f"DELETE FROM control.bind_code WHERE platform_user_id IN ({owner})",
        f"DELETE FROM control.platform_user WHERE id IN ({owner})",
    ):
        await connection.execute(
            text(statement),
            {"tenant_id": tenant_id, "user_code": user_code},
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
                # 整场拆除：平台与该用户都要清（用户行是 _ensure_user 建的，不删就永久留在库里）
                await _cleanup_platform(connection, settings.default_tenant_id, args.platform_key)
                await _cleanup_user(connection, settings.default_tenant_id, args.user_code)
            else:
                # 只清平台、**不删用户**：同一场景会用同一 user_code 连播多个平台（如 S-08），
                # 这里若删用户，会把先前已播平台的凭据一并抹掉。
                user_id = await _ensure_user(connection, settings.default_tenant_id, args.user_code)
                await _cleanup_platform(connection, settings.default_tenant_id, args.platform_key)
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
