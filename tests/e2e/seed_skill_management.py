"""E2E Skill 数据播种/校验/清理（真实 PostgreSQL + Artifact 目录）。"""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from pathlib import Path
from typing import Any

from muad_common import SharedSettings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="seed_skill_management")
    parser.add_argument("--key", required=True)
    parser.add_argument("--user-code")
    parser.add_argument("--ensure-user", action="store_true")
    parser.add_argument("--dump", action="store_true")
    parser.add_argument("--cleanup", action="store_true")
    return parser


async def _skill_id(connection: Any, tenant_id: str, key: str) -> uuid.UUID | None:
    value = await connection.scalar(
        text(
            "SELECT id FROM control.skill WHERE tenant_id = :tenant_id AND key = :key "
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
                "VALUES (:id, :tenant_id, :user_code, 'Skill E2E User', 'ACTIVE')"
            ),
            {"id": user_id, "tenant_id": tenant_id, "user_code": user_code},
        )
    return user_id


async def _dump(connection: Any, tenant_id: str, key: str) -> dict[str, Any]:
    skill_id = await _skill_id(connection, tenant_id, key)
    if skill_id is None:
        return {"skill": None, "artifacts": []}
    skill = (
        await connection.execute(
            text(
                "SELECT id, key, user_scope, enabled, current_artifact_id "
                "FROM control.skill WHERE id = :skill_id"
            ),
            {"skill_id": skill_id},
        )
    ).one()
    artifacts = (
        await connection.execute(
            text(
                "SELECT id, version, checksum, storage_key, validation_status, instructions "
                "FROM control.skill_artifact WHERE skill_id = :skill_id AND is_deleted = false "
                "ORDER BY create_time DESC"
            ),
            {"skill_id": skill_id},
        )
    ).all()
    root = Path(SharedSettings().artifact_root)
    payload: dict[str, Any] = {
        "skill": {
            "id": str(skill[0]),
            "key": skill[1],
            "user_scope": skill[2],
            "enabled": skill[3],
            "current_artifact_id": str(skill[4]) if skill[4] else None,
        },
        "artifacts": [
            {
                "artifact_id": str(row[0]),
                "version": row[1],
                "checksum": row[2],
                "storage_key": row[3],
                "validation_status": row[4],
                "instructions_length": len(row[5] or ""),
                "file_exists": (root / row[3]).is_file(),
                "file_size": (root / row[3]).stat().st_size if (root / row[3]).is_file() else None,
            }
            for row in artifacts
        ],
    }
    return payload


async def _cleanup(connection: Any, tenant_id: str, key: str, user_code: str | None) -> None:
    skill_id = await _skill_id(connection, tenant_id, key)
    storage_keys: list[str] = []
    if skill_id is not None:
        storage_keys = list(
            await connection.scalars(
                text(
                    "SELECT storage_key FROM control.skill_artifact WHERE skill_id = :skill_id"
                ),
                {"skill_id": skill_id},
            )
        )
        for statement in (
            "DELETE FROM control.skill_user_grant WHERE skill_id = :skill_id",
            "DELETE FROM control.agent_skill_binding WHERE skill_id = :skill_id",
            "DELETE FROM control.skill_artifact WHERE skill_id = :skill_id",
            "DELETE FROM control.skill WHERE id = :skill_id",
        ):
            await connection.execute(text(statement), {"skill_id": skill_id})
    if user_code:
        await connection.execute(
            text(
                "DELETE FROM control.platform_user WHERE tenant_id = :tenant_id "
                "AND user_code = :user_code"
            ),
            {"tenant_id": tenant_id, "user_code": user_code},
        )
    root = Path(SharedSettings().artifact_root)
    for storage_key in storage_keys:
        target = root / storage_key
        target.unlink(missing_ok=True)
        try:
            target.parent.rmdir()
        except OSError:
            continue


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
