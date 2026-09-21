"""E2E Agent 数据播种/清理（真实 PostgreSQL + Artifact 目录）。"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import uuid
from pathlib import Path
from typing import Any

from muad_common import SharedSettings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="seed_agent_management")
    parser.add_argument("--key", required=True)
    parser.add_argument("--skill-key")
    parser.add_argument("--mcp-key")
    parser.add_argument("--ensure-model", action="store_true")
    parser.add_argument("--delete-skill", action="store_true")
    parser.add_argument("--cleanup", action="store_true")
    return parser


async def _ensure_model(connection: Any, tenant_id: str) -> str:
    model_id = await connection.scalar(
        text(
            "SELECT id FROM control.model_definition WHERE tenant_id = :tenant_id "
            "AND is_deleted = false AND enabled = true LIMIT 1"
        ),
        {"tenant_id": tenant_id},
    )
    if model_id is None:
        model_id = uuid.uuid4()
        await connection.execute(
            text(
                "INSERT INTO control.model_definition "
                "(id, tenant_id, key, name, protocol, model_id, base_url, enabled) "
                "VALUES (:id, :tenant_id, :key, 'E2E Agent Model', 'OPENAI', 'gpt-4o-mini', "
                "'https://api.example.com/v1', true)"
            ),
            {"id": model_id, "tenant_id": tenant_id, "key": f"e2e-model-{uuid.uuid4().hex[:8]}"},
        )
    return str(model_id)


async def _agent_id(connection: Any, tenant_id: str, key: str) -> uuid.UUID | None:
    return await connection.scalar(
        text(
            "SELECT id FROM control.agent_definition WHERE tenant_id = :tenant_id "
            "AND key = :key AND is_deleted = false"
        ),
        {"tenant_id": tenant_id, "key": key},
    )


async def _delete_skill(connection: Any, tenant_id: str, skill_key: str) -> None:
    skill_id = await connection.scalar(
        text(
            "SELECT id FROM control.skill WHERE tenant_id = :tenant_id AND key = :key "
            "AND is_deleted = false"
        ),
        {"tenant_id": tenant_id, "key": skill_key},
    )
    if skill_id is not None:
        await connection.execute(
            text("UPDATE control.skill SET is_deleted = true WHERE id = :id"), {"id": skill_id}
        )


async def _cleanup(
    connection: Any,
    tenant_id: str,
    key: str,
    skill_key: str | None,
    mcp_key: str | None,
) -> None:
    agent_id = await _agent_id(connection, tenant_id, key)
    if agent_id is not None:
        for statement in (
            "DELETE FROM control.agent_skill_binding WHERE agent_id = :agent_id",
            "DELETE FROM control.agent_mcp_binding WHERE agent_id = :agent_id",
            "DELETE FROM control.agent_access_grant WHERE agent_id = :agent_id",
            "DELETE FROM control.bot_account WHERE agent_id = :agent_id",
            "DELETE FROM control.agent_definition WHERE id = :agent_id",
        ):
            await connection.execute(text(statement), {"agent_id": agent_id})
    skill_ids: list[uuid.UUID] = []
    if skill_key:
        skill_ids = list(
            await connection.scalars(
                text(
                    "SELECT id FROM control.skill WHERE tenant_id = :tenant_id AND key = :key"
                ),
                {"tenant_id": tenant_id, "key": skill_key},
            )
        )
    if skill_ids:
        for statement in (
            "DELETE FROM control.agent_skill_binding WHERE skill_id = ANY(:ids)",
            "DELETE FROM control.skill_user_grant WHERE skill_id = ANY(:ids)",
            "DELETE FROM control.skill_artifact WHERE skill_id = ANY(:ids)",
            "DELETE FROM control.skill WHERE id = ANY(:ids)",
        ):
            await connection.execute(text(statement), {"ids": skill_ids})
    if mcp_key:
        mcp_id = await connection.scalar(
            text(
                "SELECT id FROM control.mcp_server WHERE tenant_id = :tenant_id AND key = :key"
            ),
            {"tenant_id": tenant_id, "key": mcp_key},
        )
        if mcp_id is not None:
            await connection.execute(
                text("DELETE FROM control.agent_mcp_binding WHERE mcp_server_id = :id"),
                {"id": mcp_id},
            )
            await connection.execute(
                text("DELETE FROM control.mcp_server WHERE id = :id"), {"id": mcp_id}
            )
    artifact_root = Path(SharedSettings().artifact_root)
    for skill_id in skill_ids:
        shutil.rmtree(artifact_root / "skills" / str(skill_id), ignore_errors=True)


async def main() -> int:
    args = _build_parser().parse_args()
    settings = SharedSettings()
    if not settings.database_url:
        raise SystemExit("DATABASE_URL not configured")
    engine = create_async_engine(settings.database_url)
    try:
        async with engine.begin() as connection:
            tenant_id = settings.default_tenant_id
            if args.ensure_model:
                model_id = await _ensure_model(connection, tenant_id)
                print(json.dumps({"model_id": model_id}))
            elif args.delete_skill:
                if not args.skill_key:
                    raise SystemExit("--delete-skill requires --skill-key")
                await _delete_skill(connection, tenant_id, args.skill_key)
            elif args.cleanup:
                await _cleanup(connection, tenant_id, args.key, args.skill_key, args.mcp_key)
            else:
                raise SystemExit("choose one of --ensure-model / --delete-skill / --cleanup")
    finally:
        await engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
