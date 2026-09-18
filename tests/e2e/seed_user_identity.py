"""E2E 数据播种：为指定 user_code 写入四类来源数据（真实 PostgreSQL）。"""

from __future__ import annotations

import argparse
import asyncio
import uuid
from datetime import UTC, datetime

from muad_common import SharedSettings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="seed_user_identity")
    parser.add_argument("--user-code", required=True)
    parser.add_argument("--cleanup", action="store_true")
    return parser


async def _cleanup(connection, tenant_id: str, user_id: uuid.UUID) -> None:
    for statement in (
        "DELETE FROM runtime.user_memory WHERE tenant_id = :tenant_id AND user_id = :user_id",
        "DELETE FROM control.channel_identity WHERE tenant_id = :tenant_id AND platform_user_id = :user_id",
        "DELETE FROM control.bot_account WHERE tenant_id = :tenant_id AND name = 'E2E Bot'",
        "DELETE FROM control.user_credential_ref WHERE tenant_id = :tenant_id AND user_id = :user_id",
        "DELETE FROM control.project_platform WHERE tenant_id = :tenant_id AND key = 'e2e-platform'",
        "DELETE FROM control.agent_access_grant WHERE user_id = :user_id",
        "DELETE FROM control.agent_definition WHERE tenant_id = :tenant_id AND key = 'e2e-agent'",
        "DELETE FROM control.model_definition WHERE tenant_id = :tenant_id AND key = 'e2e-model'",
    ):
        await connection.execute(text(statement), {"tenant_id": tenant_id, "user_id": user_id})


async def _seed(connection, tenant_id: str, user_id: uuid.UUID) -> None:
    model_id, agent_id, bot_id, platform_id, identity_id = (
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
    )
    now = datetime.now(UTC)
    await connection.execute(
        text(
            "INSERT INTO control.model_definition (id, tenant_id, key, name, model_id, base_url, enabled) "
            "VALUES (:id, :tenant_id, 'e2e-model', 'E2E Model', 'gpt-4o-mini', "
            "'https://api.example.com/v1', true)"
        ),
        {"id": model_id, "tenant_id": tenant_id},
    )
    await connection.execute(
        text(
            "INSERT INTO control.agent_definition "
            "(id, tenant_id, key, name, instructions, model_id, runtime_config_json, enabled, revision) "
            "VALUES (:id, :tenant_id, 'e2e-agent', 'E2E Agent', 'help', :model_id, '{}'::jsonb, true, 1)"
        ),
        {"id": agent_id, "tenant_id": tenant_id, "model_id": model_id},
    )
    await connection.execute(
        text(
            "INSERT INTO control.agent_access_grant (id, user_id, agent_id, granted_by, granted_at) "
            "VALUES (:id, :user_id, :agent_id, :granted_by, :now)"
        ),
        {
            "id": uuid.uuid4(),
            "user_id": user_id,
            "agent_id": agent_id,
            "granted_by": uuid.uuid4(),
            "now": now,
        },
    )
    await connection.execute(
        text(
            "INSERT INTO control.project_platform "
            "(id, tenant_id, key, name, resolver_type, resolver_config_json, adapter_key, credential_mode) "
            "VALUES (:id, :tenant_id, 'e2e-platform', 'E2E Platform', 'STATIC', '{}'::jsonb, 'e2e', 'NONE')"
        ),
        {"id": platform_id, "tenant_id": tenant_id},
    )
    await connection.execute(
        text(
            "INSERT INTO control.user_credential_ref (id, tenant_id, user_id, platform_id, credential_json) "
            "VALUES (:id, :tenant_id, :user_id, :platform_id, '{\"token\": \"e2e\"}'::jsonb)"
        ),
        {"id": uuid.uuid4(), "tenant_id": tenant_id, "user_id": user_id, "platform_id": platform_id},
    )
    await connection.execute(
        text(
            "INSERT INTO control.bot_account (id, tenant_id, channel, name, bot_id, secret, agent_id) "
            "VALUES (:id, :tenant_id, 'WECOM', 'E2E Bot', :bot_id, 'e2e-bot-secret', :agent_id)"
        ),
        {"id": bot_id, "tenant_id": tenant_id, "bot_id": f"e2e-{uuid.uuid4().hex[:8]}", "agent_id": agent_id},
    )
    await connection.execute(
        text(
            "INSERT INTO control.channel_identity "
            "(id, tenant_id, channel, identity_key, external_user_id, bot_account_id, platform_user_id) "
            "VALUES (:id, :tenant_id, 'WECOM', :identity_key, :external_user_id, :bot_id, :user_id)"
        ),
        {
            "id": identity_id,
            "tenant_id": tenant_id,
            "identity_key": f"WECOM:e2e:{uuid.uuid4().hex[:8]}",
            "external_user_id": f"ext-{uuid.uuid4().hex[:8]}",
            "bot_id": bot_id,
            "user_id": user_id,
        },
    )
    await connection.execute(
        text(
            "INSERT INTO runtime.user_memory "
            "(id, tenant_id, user_id, memory_key, category, content_json, source_type) "
            "VALUES (:id, :tenant_id, :user_id, :memory_key, 'PREFERENCE', "
            "'{\"note\": \"e2e\"}'::jsonb, 'USER')"
        ),
        {
            "id": uuid.uuid4(),
            "tenant_id": tenant_id,
            "user_id": user_id,
            "memory_key": f"e2e-{uuid.uuid4().hex[:8]}",
        },
    )


async def main() -> int:
    args = _build_parser().parse_args()
    settings = SharedSettings()
    database_url = settings.database_url
    if not database_url:
        raise SystemExit("DATABASE_URL not configured")
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            user_id = await connection.scalar(
                text(
                    "SELECT id FROM control.platform_user WHERE tenant_id = :tenant_id "
                    "AND user_code = :user_code AND is_deleted = false"
                ),
                {"tenant_id": settings.default_tenant_id, "user_code": args.user_code},
            )
            if user_id is None:
                raise SystemExit(f"platform_user not found: {args.user_code}")
            await _cleanup(connection, settings.default_tenant_id, user_id)
            if not args.cleanup:
                await _seed(connection, settings.default_tenant_id, user_id)
    finally:
        await engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
