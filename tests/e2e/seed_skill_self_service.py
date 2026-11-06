"""E2E 用户 Skill 自助审批/解绑播种、快照与清理（真实 PostgreSQL + Artifact 目录）。

只负责准备**测试前置事实**：PENDING/APPROVED artifact、owner、上传 Agent、AgentAccessGrant
以及审批通过后的 SkillUserGrant/AgentSkillBinding。审批和删除动作本身必须由真实浏览器经
Console API 完成，本脚本不改写这两步的业务结果。

Skill 包用真实 `validated_package` 解析后落库；artifact 文件写入真实 ARTIFACT_ROOT，不使用
内存替身。`--dump` 返回 UI 断言所需的 DB 事实（含审计与 Effective 查询结果）。
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import uuid
import zipfile
from dataclasses import dataclass
from typing import Any

from muad_common import SharedSettings
from muad_console_platform.application.channel_skills_service import ChannelSkillsService
from muad_console_platform.infrastructure.skill_artifact_store import (
    artifact_storage_key,
    remove_artifact,
    write_artifact,
)
from muad_console_platform.infrastructure.skill_validator import (
    checksum_of,
    validated_package,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

VALIDATION_READY = "READY"
REVIEW_PENDING = "PENDING"
REVIEW_APPROVED = "APPROVED"
SUBMITTED_CHANNEL = "WEBCHAT"


@dataclass(frozen=True)
class SeedIds:
    tenant_id: str
    skill_id: uuid.UUID
    artifact_id: uuid.UUID
    owner_user_id: uuid.UUID
    agent_id: uuid.UUID
    agent_key: str
    version: str
    checksum: str


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="seed_skill_self_service")
    parser.add_argument("--key", required=True, help="Skill key；同时派生 owner/agent 唯一键")
    parser.add_argument("--seed", choices=["pending", "approved"])
    parser.add_argument("--version", default="1.0.0")
    parser.add_argument("--admin-user", default=os.getenv("E2E_ADMIN_USER", "admin"))
    parser.add_argument("--dump", action="store_true")
    parser.add_argument("--cleanup", action="store_true")
    return parser


def _owner_code(key: str) -> str:
    return f"{key}-owner"


def _agent_key(key: str) -> str:
    return f"{key}-agent"


def _skill_package(key: str) -> bytes:
    skill_md = (
        "---\n"
        f"name: E2E Self Service {key}\n"
        "description: E2E 用户自助审批与解绑旅程。\n"
        "---\n\n"
        f"# E2E Self Service {key}\n\n"
        "该包由 E2E 种子脚本生成，用于真实审批/解绑旅程。\n"
    )
    manifest = json.dumps(
        {
            "runtime": "script",
            "entrypoint": "scripts/run.py",
            "author": "E2E Submitter",
        },
        ensure_ascii=False,
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("SKILL.md", skill_md)
        archive.writestr("muad.skill.json", manifest)
        archive.writestr("scripts/run.py", "print('self-service-e2e')\n")
    return buffer.getvalue()


async def _ensure_user(session: AsyncSession, tenant_id: str, code: str) -> uuid.UUID:
    user_id = await session.scalar(
        text(
            "SELECT id FROM control.platform_user WHERE tenant_id = :tenant_id "
            "AND user_code = :user_code AND is_deleted = false"
        ),
        {"tenant_id": tenant_id, "user_code": code},
    )
    if user_id is not None:
        return user_id
    user_id = uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO control.platform_user "
            "(id, tenant_id, user_code, display_name, status) "
            "VALUES (:id, :tenant_id, :user_code, :display_name, 'ACTIVE')"
        ),
        {"id": user_id, "tenant_id": tenant_id, "user_code": code, "display_name": f"E2E {code}"},
    )
    return user_id


async def _ensure_model(session: AsyncSession, tenant_id: str) -> uuid.UUID:
    model_id = await session.scalar(
        text(
            "SELECT id FROM control.model_definition WHERE tenant_id = :tenant_id "
            "AND is_deleted = false AND enabled = true LIMIT 1"
        ),
        {"tenant_id": tenant_id},
    )
    if model_id is not None:
        return model_id
    model_id = uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO control.model_definition "
            "(id, tenant_id, key, name, protocol, model_id, enabled) "
            "VALUES (:id, :tenant_id, :key, 'E2E Self Service Model', 'OPENAI', 'gpt-4o-mini', true)"
        ),
        {"id": model_id, "tenant_id": tenant_id, "key": f"e2e-self-service-model-{uuid.uuid4().hex[:8]}"},
    )
    return model_id


async def _ensure_agent(
    session: AsyncSession, tenant_id: str, key: str, model_id: uuid.UUID
) -> uuid.UUID:
    agent_id = await session.scalar(
        text(
            "SELECT id FROM control.agent_definition WHERE tenant_id = :tenant_id "
            "AND key = :key AND is_deleted = false"
        ),
        {"tenant_id": tenant_id, "key": key},
    )
    if agent_id is not None:
        return agent_id
    agent_id = uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO control.agent_definition "
            "(id, tenant_id, key, name, instructions, model_id, enabled) "
            "VALUES (:id, :tenant_id, :key, :name, 'E2E self-service agent', :model_id, true)"
        ),
        {
            "id": agent_id,
            "tenant_id": tenant_id,
            "key": key,
            "name": f"E2E Agent {key}",
            "model_id": model_id,
        },
    )
    return agent_id


async def _ensure_agent_access(
    session: AsyncSession,
    tenant_id: str,
    user_id: uuid.UUID,
    agent_id: uuid.UUID,
    granted_by: uuid.UUID,
) -> None:
    exists = await session.scalar(
        text(
            "SELECT 1 FROM control.agent_access_grant WHERE user_id = :user_id "
            "AND agent_id = :agent_id AND is_deleted = false"
        ),
        {"user_id": user_id, "agent_id": agent_id},
    )
    if exists:
        return
    await session.execute(
        text(
            "INSERT INTO control.agent_access_grant "
            "(id, user_id, agent_id, granted_by) VALUES (:id, :user_id, :agent_id, :granted_by)"
        ),
        {"id": uuid.uuid4(), "user_id": user_id, "agent_id": agent_id, "granted_by": granted_by},
    )


async def _admin_id(session: AsyncSession, tenant_id: str, username: str) -> uuid.UUID | None:
    return await session.scalar(
        text(
            "SELECT id FROM control.console_account WHERE tenant_id = :tenant_id "
            "AND username = :username AND is_deleted = false"
        ),
        {"tenant_id": tenant_id, "username": username},
    )


async def _create_skill(
    session: AsyncSession,
    *,
    tenant_id: str,
    key: str,
    version: str,
    mode: str,
    owner_user_id: uuid.UUID,
    agent_id: uuid.UUID,
    reviewer_id: uuid.UUID | None,
) -> SeedIds:
    data = _skill_package(key)
    with validated_package(data) as package:
        manual = package.manifest
        frontmatter = package.frontmatter
        instructions = package.instructions
        manifest = {
            "files": list(package.files),
            "file_count": len(package.files),
            "total_size": package.total_size,
        }
        execution_mode = manual.execution.value
    skill_id = uuid.uuid4()
    artifact_id = uuid.uuid4()
    storage_key = artifact_storage_key(skill_id, artifact_id)
    approved = mode == "approved"
    await session.execute(
        text(
            "INSERT INTO control.skill "
            "(id, tenant_id, key, name, description, platform_label, user_scope, source, "
            " author, owner_user_id, current_artifact_id, enabled) "
            "VALUES (:id, :tenant_id, :key, :name, :description, :platform_label, 'SELECTED', 'USER', "
            "        'E2E Submitter', :owner_user_id, :current_artifact_id, true)"
        ),
        {
            "id": skill_id,
            "tenant_id": tenant_id,
            "key": key,
            "name": manual.name,
            "description": manual.description,
            "platform_label": manual.platform_label,
            "owner_user_id": owner_user_id,
            "current_artifact_id": artifact_id if approved else None,
        },
    )
    await session.execute(
        text(
            "INSERT INTO control.skill_artifact "
            "(id, skill_id, version, checksum, storage_key, frontmatter_json, manifest_json, "
            " execution_mode, instructions, default_script, package_size, validation_status, "
            " created_by, review_status, reviewed_by, reviewed_at, submitted_by_user_id, "
            " submitted_agent_id, submitted_channel, source) "
            "VALUES (:id, :skill_id, :version, :checksum, :storage_key, CAST(:frontmatter AS jsonb), "
            "        CAST(:manifest AS jsonb), :execution_mode, :instructions, NULL, :package_size, "
            "        :validation_status, :created_by, :review_status, :reviewed_by, "
            "        CASE WHEN :reviewed THEN now() ELSE NULL END, :submitted_by_user_id, "
            "        :submitted_agent_id, :submitted_channel, 'USER')"
        ),
        {
            "id": artifact_id,
            "skill_id": skill_id,
            "version": version,
            "checksum": checksum_of(data),
            "storage_key": storage_key,
            "frontmatter": json.dumps(frontmatter, ensure_ascii=False),
            "manifest": json.dumps(manifest, ensure_ascii=False),
            "execution_mode": execution_mode,
            "instructions": instructions,
            "package_size": len(data),
            "validation_status": VALIDATION_READY,
            "created_by": owner_user_id,
            "review_status": REVIEW_APPROVED if approved else REVIEW_PENDING,
            "reviewed_by": reviewer_id if approved else None,
            "reviewed": approved,
            "submitted_by_user_id": owner_user_id,
            "submitted_agent_id": agent_id,
            "submitted_channel": SUBMITTED_CHANNEL,
        },
    )
    write_artifact(storage_key, data)
    if approved:
        await _insert_active_relations(
            session, skill_id=skill_id, owner_user_id=owner_user_id, agent_id=agent_id,
            granted_by=reviewer_id or owner_user_id,
        )
    return SeedIds(
        tenant_id=tenant_id,
        skill_id=skill_id,
        artifact_id=artifact_id,
        owner_user_id=owner_user_id,
        agent_id=agent_id,
        agent_key=_agent_key(key),
        version=version,
        checksum=checksum_of(data),
    )


async def _insert_active_relations(
    session: AsyncSession,
    *,
    skill_id: uuid.UUID,
    owner_user_id: uuid.UUID,
    agent_id: uuid.UUID,
    granted_by: uuid.UUID,
) -> None:
    await session.execute(
        text(
            "INSERT INTO control.skill_user_grant (id, skill_id, user_id, granted_by) "
            "VALUES (:id, :skill_id, :user_id, :granted_by)"
        ),
        {"id": uuid.uuid4(), "skill_id": skill_id, "user_id": owner_user_id, "granted_by": granted_by},
    )
    await session.execute(
        text(
            "INSERT INTO control.agent_skill_binding (id, agent_id, skill_id, sort_order) "
            "VALUES (:id, :agent_id, :skill_id, 0)"
        ),
        {"id": uuid.uuid4(), "agent_id": agent_id, "skill_id": skill_id},
    )


async def _seed(session: AsyncSession, args: argparse.Namespace) -> dict[str, Any]:
    settings = SharedSettings()
    tenant_id = settings.default_tenant_id
    await _cleanup(session, tenant_id, args.key)
    owner_id = await _ensure_user(session, tenant_id, _owner_code(args.key))
    model_id = await _ensure_model(session, tenant_id)
    agent_id = await _ensure_agent(session, tenant_id, _agent_key(args.key), model_id)
    reviewer_id = await _admin_id(session, tenant_id, args.admin_user)
    await _ensure_agent_access(
        session, tenant_id, owner_id, agent_id, reviewer_id or owner_id
    )
    ids = await _create_skill(
        session,
        tenant_id=tenant_id,
        key=args.key,
        version=args.version,
        mode=args.seed,
        owner_user_id=owner_id,
        agent_id=agent_id,
        reviewer_id=reviewer_id,
    )
    return {
        "skill_id": str(ids.skill_id),
        "artifact_id": str(ids.artifact_id),
        "owner_user_id": str(ids.owner_user_id),
        "agent_id": str(ids.agent_id),
        "agent_key": ids.agent_key,
        "version": ids.version,
        "checksum": ids.checksum,
    }


async def _dump(session: AsyncSession, tenant_id: str, key: str) -> dict[str, Any]:
    skill = (
        await session.execute(
            text(
                "SELECT id, key, name, source, owner_user_id, current_artifact_id, is_deleted "
                "FROM control.skill WHERE tenant_id = :tenant_id AND key = :key"
            ),
            {"tenant_id": tenant_id, "key": key},
        )
    ).first()
    if skill is None:
        return {"skill": None, "artifacts": [], "bindings": [], "grants": [], "audit": [],
                "effective_skill_keys": []}
    skill_id = skill[0]
    artifacts = (
        await session.execute(
            text(
                "SELECT id, version, checksum, review_status, is_deleted, reviewed_by, "
                "submitted_by_user_id, submitted_agent_id, storage_key "
                "FROM control.skill_artifact WHERE skill_id = :skill_id ORDER BY create_time DESC"
            ),
            {"skill_id": skill_id},
        )
    ).all()
    bindings = (
        await session.execute(
            text(
                "SELECT b.agent_id, a.key, a.name, b.is_deleted FROM control.agent_skill_binding b "
                "JOIN control.agent_definition a ON a.id = b.agent_id "
                "WHERE b.skill_id = :skill_id ORDER BY b.create_time"
            ),
            {"skill_id": skill_id},
        )
    ).all()
    grants = (
        await session.execute(
            text(
                "SELECT g.user_id, u.user_code, g.is_deleted FROM control.skill_user_grant g "
                "JOIN control.platform_user u ON u.id = g.user_id "
                "WHERE g.skill_id = :skill_id ORDER BY g.create_time"
            ),
            {"skill_id": skill_id},
        )
    ).all()
    audit = (
        await session.execute(
            text(
                "SELECT action, after_json FROM control.config_audit_log "
                "WHERE tenant_id = :tenant_id AND resource_type = 'SKILL' AND resource_id = :skill_id "
                "ORDER BY create_time"
            ),
            {"tenant_id": tenant_id, "skill_id": skill_id},
        )
    ).all()
    owner_user_id = skill[4]
    agent_id = bindings[0][0] if bindings else None
    effective: list[str] = []
    if owner_user_id is not None and agent_id is not None:
        response = await ChannelSkillsService(session).list_skills(
            tenant_id, agent_id, owner_user_id, page_size=100
        )
        effective = [item.key for item in response.items]
    return {
        "skill": {
            "id": str(skill[0]),
            "key": skill[1],
            "name": skill[2],
            "source": skill[3],
            "owner_user_id": str(skill[4]) if skill[4] else None,
            "current_artifact_id": str(skill[5]) if skill[5] else None,
            "is_deleted": skill[6],
        },
        "artifacts": [
            {
                "artifact_id": str(row[0]),
                "version": row[1],
                "checksum": row[2],
                "review_status": row[3],
                "is_deleted": row[4],
                "reviewed_by": str(row[5]) if row[5] else None,
                "submitted_by_user_id": str(row[6]) if row[6] else None,
                "submitted_agent_id": str(row[7]) if row[7] else None,
                "storage_key": row[8],
            }
            for row in artifacts
        ],
        "bindings": [
            {"agent_id": str(row[0]), "key": row[1], "name": row[2], "is_deleted": row[3]}
            for row in bindings
        ],
        "grants": [
            {"user_id": str(row[0]), "user_code": row[1], "is_deleted": row[2]}
            for row in grants
        ],
        "audit": [
            {"action": row[0], "after": row[1]} for row in audit
        ],
        "effective_skill_keys": effective,
    }


async def _cleanup(session: AsyncSession, tenant_id: str, key: str) -> None:
    skill_ids = list(
        await session.scalars(
            text("SELECT id FROM control.skill WHERE tenant_id = :tenant_id AND key = :key"),
            {"tenant_id": tenant_id, "key": key},
        )
    )
    storage_keys: list[str] = []
    for skill_id in skill_ids:
        storage_keys.extend(
            await session.scalars(
                text("SELECT storage_key FROM control.skill_artifact WHERE skill_id = :skill_id"),
                {"skill_id": skill_id},
            )
        )
        await session.execute(
            text("DELETE FROM control.skill_user_grant WHERE skill_id = :skill_id"),
            {"skill_id": skill_id},
        )
        await session.execute(
            text("DELETE FROM control.agent_skill_binding WHERE skill_id = :skill_id"),
            {"skill_id": skill_id},
        )
        await session.execute(
            text("DELETE FROM control.skill_artifact WHERE skill_id = :skill_id"),
            {"skill_id": skill_id},
        )
        await session.execute(
            text("DELETE FROM control.config_audit_log WHERE resource_id = :skill_id"),
            {"skill_id": skill_id},
        )
        await session.execute(
            text("DELETE FROM control.skill WHERE id = :skill_id"), {"skill_id": skill_id}
        )
    for storage_key in storage_keys:
        remove_artifact(storage_key)
    agent_id = await session.scalar(
        text(
            "SELECT id FROM control.agent_definition WHERE tenant_id = :tenant_id AND key = :key"
        ),
        {"tenant_id": tenant_id, "key": _agent_key(key)},
    )
    if agent_id is not None:
        await session.execute(
            text("DELETE FROM control.agent_access_grant WHERE agent_id = :agent_id"),
            {"agent_id": agent_id},
        )
        await session.execute(
            text("DELETE FROM control.agent_skill_binding WHERE agent_id = :agent_id"),
            {"agent_id": agent_id},
        )
        await session.execute(
            text("DELETE FROM control.agent_definition WHERE id = :agent_id"),
            {"agent_id": agent_id},
        )
    await session.execute(
        text(
            "DELETE FROM control.platform_user WHERE tenant_id = :tenant_id AND user_code = :code"
        ),
        {"tenant_id": tenant_id, "code": _owner_code(key)},
    )
    await session.execute(
        text("DELETE FROM control.config_audit_log WHERE after_json->>'agent_key' = :agent_key"),
        {"agent_key": _agent_key(key)},
    )


async def main() -> int:
    args = _build_parser().parse_args()
    settings = SharedSettings()
    if not settings.database_url:
        raise SystemExit("DATABASE_URL not configured")
    engine = create_async_engine(settings.database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            tenant_id = settings.default_tenant_id
            payload: dict[str, Any]
            if args.seed:
                payload = await _seed(session, args)
                await session.commit()
            elif args.dump:
                payload = await _dump(session, tenant_id, args.key)
            elif args.cleanup:
                await _cleanup(session, tenant_id, args.key)
                await session.commit()
                payload = {"cleaned": True}
            else:
                raise SystemExit("choose one of --seed / --dump / --cleanup")
            print(json.dumps(payload, ensure_ascii=False))
    finally:
        await engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
