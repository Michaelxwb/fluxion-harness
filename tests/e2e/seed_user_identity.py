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
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help="删除该 user_code 的 E2E 数据（含 platform_user 本身）；不存在时视为已清理",
    )
    parser.add_argument(
        "--corrupt-memory",
        action="store_true",
        help="额外写入一条 content_json 非对象的记忆，用于制造真实 500（E-06 故障隔离）",
    )
    return parser


async def _cleanup(connection, tenant_id: str, user_id: uuid.UUID) -> None:
    # 顺序按外键依赖：引用方先删。
    # 注意：不删 platform_user —— 非清理模式下紧接着还要播种，播种依赖该行存在。
    for statement in (
        "DELETE FROM runtime.user_memory WHERE tenant_id = :tenant_id AND user_id = :user_id",
        "DELETE FROM control.bind_code WHERE tenant_id = :tenant_id AND platform_user_id = :user_id",
        "DELETE FROM control.channel_identity WHERE tenant_id = :tenant_id AND platform_user_id = :user_id",
        "DELETE FROM control.channel_identity WHERE tenant_id = :tenant_id AND bot_account_id IN "
        "(SELECT id FROM control.bot_account WHERE tenant_id = :tenant_id AND name = 'E2E Bot')",
        "DELETE FROM control.bot_account WHERE tenant_id = :tenant_id AND name = 'E2E Bot'",
        "DELETE FROM control.user_credential_ref WHERE tenant_id = :tenant_id AND user_id = :user_id",
        "DELETE FROM control.project_platform WHERE tenant_id = :tenant_id AND key = 'e2e-platform'",
        "DELETE FROM control.agent_access_grant WHERE user_id = :user_id",
        "DELETE FROM control.agent_definition WHERE tenant_id = :tenant_id AND key = 'e2e-agent'",
        "DELETE FROM control.model_definition WHERE tenant_id = :tenant_id AND key = 'e2e-model'",
    ):
        await connection.execute(text(statement), {"tenant_id": tenant_id, "user_id": user_id})


async def _delete_user(connection, tenant_id: str, user_id: uuid.UUID) -> None:
    await connection.execute(
        text("DELETE FROM control.platform_user WHERE tenant_id = :tenant_id AND id = :user_id"),
        {"tenant_id": tenant_id, "user_id": user_id},
    )


async def _seed(connection, tenant_id: str, user_id: uuid.UUID, *, corrupt_memory: bool) -> None:
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
            "VALUES (:id, :tenant_id, 'e2e-platform', 'E2E Platform', 'BASE_URL', "
            "'{\"base_url\": \"http://127.0.0.1:4190\"}'::jsonb, 'generic-http', 'NONE')"
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
    if corrupt_memory:
        # content_json 是 JSON 数组而非对象：真实触发 MemoryItem 校验失败 → 真实 500
        await connection.execute(
            text(
                "INSERT INTO runtime.user_memory "
                "(id, tenant_id, user_id, memory_key, category, content_json, source_type) "
                "VALUES (:id, :tenant_id, :user_id, :memory_key, 'PREFERENCE', '[]'::jsonb, 'USER')"
            ),
            {
                "id": uuid.uuid4(),
                "tenant_id": tenant_id,
                "user_id": user_id,
                "memory_key": f"e2e-corrupt-{uuid.uuid4().hex[:8]}",
            },
        )


async def main() -> int:
    args = _build_parser().parse_args()
    settings = SharedSettings()
    database_url = settings.database_url
    if not database_url:
        raise SystemExit("DATABASE_URL not configured")
    tenant_id = settings.default_tenant_id
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            user_id = await connection.scalar(
                text(
                    "SELECT id FROM control.platform_user WHERE tenant_id = :tenant_id "
                    "AND user_code = :user_code AND is_deleted = false"
                ),
                {"tenant_id": tenant_id, "user_code": args.user_code},
            )
            if user_id is None:
                if args.cleanup:
                    return 0
                raise SystemExit(f"platform_user not found: {args.user_code}")
            await _cleanup(connection, tenant_id, user_id)
            if args.cleanup:
                await _delete_user(connection, tenant_id, user_id)
            else:
                await _seed(connection, tenant_id, user_id, corrupt_memory=args.corrupt_memory)
    finally:
        await engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
