import shutil
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient, Response
from muad_common import SharedSettings
from muad_console_platform.api.security import CSRF_COOKIE, CSRF_HEADER
from muad_console_platform.application.auth_service import hash_password
from muad_console_platform.infrastructure.db import get_session_factory
from muad_console_platform.infrastructure.models.auth import (
    ROLE_ADMIN,
    ROLE_BUILDER,
    ConsoleAccount,
)
from muad_console_platform.infrastructure.models.control import (
    AgentAccessGrant,
    AgentDefinition,
    AgentSkillBinding,
    ModelDefinition,
    PlatformUser,
    Skill,
    SkillArtifact,
    SkillUserGrant,
)
from muad_console_platform.main import app
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

SCHEMA = "control"
GUARD_TABLES = (
    "console_account",
    "console_session",
    "config_audit_log",
    "agent_definition",
    "model_definition",
    "platform_user",
    "skill",
    "skill_artifact",
    "agent_skill_binding",
    "skill_user_grant",
    "skill_import_idempotency",
)

ADMIN_PASSWORD = "console-admin-password"
BUILDER_PASSWORD = "console-builder-password"
ARTIFACT_VERSION = "1.0.0"
ARTIFACT_CHECKSUM = "sha256:" + "a" * 64


@dataclass(frozen=True)
class SkillContext:
    tenant_id: str
    other_tenant_id: str
    admin_id: uuid.UUID
    admin_username: str
    builder_id: uuid.UUID
    builder_username: str
    model_id: uuid.UUID
    agent_id: uuid.UUID
    actor_user_id: uuid.UUID
    other_user_id: uuid.UUID
    other_tenant_user_id: uuid.UUID
    skill_all_id: uuid.UUID
    skill_selected_granted_id: uuid.UUID
    skill_selected_ungranted_id: uuid.UUID
    skill_disabled_id: uuid.UUID
    skill_unbound_id: uuid.UUID
    skill_grant_revoked_id: uuid.UUID
    skill_binding_removed_id: uuid.UUID
    skill_other_tenant_id: uuid.UUID
    artifact_all_id: uuid.UUID
    artifact_all_storage_key: str


def _unique_key(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4()}"


@pytest.fixture(scope="session")
async def database_guard() -> AsyncIterator[None]:
    settings = SharedSettings()
    if settings.database_url is None:
        pytest.skip("DATABASE_URL not configured")
    engine = create_async_engine(settings.database_url)
    try:
        async with engine.connect() as connection:
            ready = await connection.run_sync(
                lambda sync_connection: all(
                    inspect(sync_connection).has_table(table, schema=SCHEMA)
                    for table in GUARD_TABLES
                )
            )
        if not ready:
            pytest.skip("run: uv run alembic -c migrations/alembic.ini upgrade head")
        yield
    finally:
        await engine.dispose()


async def _seed_skill(
    session: AsyncSession,
    tenant_id: str,
    key: str,
    *,
    user_scope: str,
    enabled: bool = True,
    artifact: bool = True,
) -> tuple[Skill, SkillArtifact | None]:
    skill = Skill(
        tenant_id=tenant_id,
        key=key,
        name=f"Skill {key}",
        description="Seeded skill.",
        user_scope=user_scope,
        enabled=enabled,
    )
    session.add(skill)
    await session.flush()
    created: SkillArtifact | None = None
    if artifact:
        created = SkillArtifact(
            skill_id=skill.id,
            version=ARTIFACT_VERSION,
            checksum=ARTIFACT_CHECKSUM,
            storage_key=f"skills/{skill.id}/{uuid.uuid4()}/skill.zip",
            frontmatter_json={"name": f"Skill {key}", "description": "Seeded skill."},
            manifest_json={"files": [], "file_count": 0, "total_size": 0},
            execution_mode="SYNC",
            package_size=128,
            validation_status="READY",
            created_by=uuid.uuid4(),
        )
        session.add(created)
        await session.flush()
        skill.current_artifact_id = created.id
        await session.flush()
    return skill, created


@pytest.fixture
async def skill_env(database_guard: None) -> AsyncIterator[SkillContext]:
    tenant_id = f"test-{uuid.uuid4()}"
    other_tenant_id = f"test-{uuid.uuid4()}"
    admin_username = _unique_key("admin")
    builder_username = _unique_key("builder")
    session_factory = get_session_factory()
    async with session_factory() as session:
        admin = ConsoleAccount(
            tenant_id=tenant_id,
            username=admin_username,
            display_name="Admin",
            password_hash=hash_password(ADMIN_PASSWORD),
            role=ROLE_ADMIN,
        )
        builder = ConsoleAccount(
            tenant_id=tenant_id,
            username=builder_username,
            display_name="Builder",
            password_hash=hash_password(BUILDER_PASSWORD),
            role=ROLE_BUILDER,
        )
        model = ModelDefinition(
            tenant_id=tenant_id,
            key=_unique_key("model"),
            name="Skill Model",
            model_id="gpt-4o-mini",
            base_url="https://api.example.com/v1",
        )
        actor = PlatformUser(
            tenant_id=tenant_id,
            user_code=_unique_key("user"),
            display_name="Actor",
        )
        other_user = PlatformUser(
            tenant_id=tenant_id,
            user_code=_unique_key("user"),
            display_name="Other User",
        )
        other_tenant_user = PlatformUser(
            tenant_id=other_tenant_id,
            user_code=_unique_key("user"),
            display_name="Other Tenant User",
        )
        session.add_all([admin, builder, model, actor, other_user, other_tenant_user])
        await session.flush()
        agent = AgentDefinition(
            tenant_id=tenant_id,
            key=_unique_key("agent"),
            name="Skill Agent",
            instructions="You are a skill agent.",
            model_id=model.id,
        )
        session.add(agent)
        await session.flush()
        session.add_all(
            [
                AgentAccessGrant(
                    user_id=actor.id,
                    agent_id=agent.id,
                    granted_by=admin.id,
                ),
                AgentAccessGrant(
                    user_id=other_user.id,
                    agent_id=agent.id,
                    granted_by=admin.id,
                ),
            ]
        )
        skill_all, artifact_all = await _seed_skill(session, tenant_id, "skill-all", user_scope="ALL")
        skill_granted, _ = await _seed_skill(
            session,
            tenant_id,
            "skill-granted",
            user_scope="SELECTED",
        )
        skill_ungranted, _ = await _seed_skill(
            session,
            tenant_id,
            "skill-ungranted",
            user_scope="SELECTED",
        )
        skill_disabled, _ = await _seed_skill(
            session,
            tenant_id,
            "skill-disabled",
            user_scope="ALL",
            enabled=False,
        )
        skill_unbound, _ = await _seed_skill(session, tenant_id, "skill-unbound", user_scope="ALL")
        skill_revoked, _ = await _seed_skill(
            session,
            tenant_id,
            "skill-revoked",
            user_scope="SELECTED",
        )
        skill_removed, _ = await _seed_skill(
            session,
            tenant_id,
            "skill-binding-removed",
            user_scope="ALL",
        )
        skill_other, _ = await _seed_skill(
            session,
            other_tenant_id,
            "skill-other-tenant",
            user_scope="ALL",
        )
        session.add_all(
            [
                AgentSkillBinding(agent_id=agent.id, skill_id=skill_all.id),
                AgentSkillBinding(agent_id=agent.id, skill_id=skill_granted.id),
                AgentSkillBinding(agent_id=agent.id, skill_id=skill_ungranted.id),
                AgentSkillBinding(agent_id=agent.id, skill_id=skill_disabled.id),
                AgentSkillBinding(agent_id=agent.id, skill_id=skill_revoked.id, is_deleted=True),
                AgentSkillBinding(agent_id=agent.id, skill_id=skill_removed.id, is_deleted=True),
                AgentSkillBinding(agent_id=agent.id, skill_id=skill_other.id),
                SkillUserGrant(skill_id=skill_granted.id, user_id=actor.id, granted_by=admin.id),
                SkillUserGrant(
                    skill_id=skill_revoked.id,
                    user_id=actor.id,
                    granted_by=admin.id,
                    is_deleted=True,
                ),
            ]
        )
        await session.commit()
        assert artifact_all is not None
        context = SkillContext(
            tenant_id=tenant_id,
            other_tenant_id=other_tenant_id,
            admin_id=admin.id,
            admin_username=admin_username,
            builder_id=builder.id,
            builder_username=builder_username,
            model_id=model.id,
            agent_id=agent.id,
            actor_user_id=actor.id,
            other_user_id=other_user.id,
            other_tenant_user_id=other_tenant_user.id,
            skill_all_id=skill_all.id,
            skill_selected_granted_id=skill_granted.id,
            skill_selected_ungranted_id=skill_ungranted.id,
            skill_disabled_id=skill_disabled.id,
            skill_unbound_id=skill_unbound.id,
            skill_grant_revoked_id=skill_revoked.id,
            skill_binding_removed_id=skill_removed.id,
            skill_other_tenant_id=skill_other.id,
            artifact_all_id=artifact_all.id,
            artifact_all_storage_key=artifact_all.storage_key,
        )
    try:
        yield context
    finally:
        await _cleanup(session_factory, tenant_id, other_tenant_id)


async def _cleanup(
    session_factory: async_sessionmaker[AsyncSession],
    tenant_id: str,
    other_tenant_id: str,
) -> None:
    params = {"tenant_id": tenant_id, "other_tenant_id": other_tenant_id}
    async with session_factory() as session:
        skill_ids = list(
            await session.scalars(
                sa.select(Skill.id).where(
                    Skill.tenant_id.in_([tenant_id, other_tenant_id])
                )
            )
        )
        await _delete(
            session,
            "DELETE FROM control.skill_user_grant WHERE skill_id IN "
            "(SELECT id FROM control.skill WHERE tenant_id IN (:tenant_id, :other_tenant_id))",
            params,
        )
        await _delete(
            session,
            "DELETE FROM control.agent_skill_binding WHERE agent_id IN "
            "(SELECT id FROM control.agent_definition WHERE tenant_id IN "
            "(:tenant_id, :other_tenant_id))",
            params,
        )
        await _delete(
            session,
            "DELETE FROM control.skill_artifact WHERE skill_id IN "
            "(SELECT id FROM control.skill WHERE tenant_id IN (:tenant_id, :other_tenant_id))",
            params,
        )
        for statement in (
            "DELETE FROM control.skill WHERE tenant_id IN (:tenant_id, :other_tenant_id)",
            "DELETE FROM control.config_audit_log WHERE tenant_id IN (:tenant_id, :other_tenant_id)",
            "DELETE FROM control.console_session WHERE account_id IN "
            "(SELECT id FROM control.console_account WHERE tenant_id IN "
            "(:tenant_id, :other_tenant_id))",
            "DELETE FROM control.console_account WHERE tenant_id IN (:tenant_id, :other_tenant_id)",
            "DELETE FROM control.agent_access_grant WHERE agent_id IN "
            "(SELECT id FROM control.agent_definition WHERE tenant_id IN "
            "(:tenant_id, :other_tenant_id))",
            "DELETE FROM control.agent_definition WHERE tenant_id IN (:tenant_id, :other_tenant_id)",
            "DELETE FROM control.model_definition WHERE tenant_id IN (:tenant_id, :other_tenant_id)",
            "DELETE FROM control.platform_user WHERE tenant_id IN (:tenant_id, :other_tenant_id)",
        ):
            await _delete(session, statement, params)
        await session.commit()
    artifact_root = Path(SharedSettings().artifact_root)
    for skill_id in skill_ids:
        skill_dir = artifact_root / "skills" / str(skill_id)
        if skill_dir.exists():
            shutil.rmtree(skill_dir)


async def _delete(
    session: AsyncSession,
    statement: str,
    params: dict[str, str],
) -> None:
    await session.execute(text(statement), params)


async def _login(client: AsyncClient, context: SkillContext, username: str, password: str) -> Response:
    return await client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
        headers=tenant_headers(context),
    )


@asynccontextmanager
async def authenticated_client(
    context: SkillContext,
    username: str,
    password: str,
) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        response = await _login(http_client, context, username, password)
        assert response.status_code == 200
        csrf_token = http_client.cookies.get(CSRF_COOKIE)
        assert csrf_token
        http_client.headers[CSRF_HEADER] = csrf_token
        yield http_client


@pytest.fixture
async def client(skill_env: SkillContext) -> AsyncIterator[AsyncClient]:
    async with authenticated_client(
        skill_env,
        skill_env.admin_username,
        ADMIN_PASSWORD,
    ) as http_client:
        yield http_client


@pytest.fixture
async def builder_client(skill_env: SkillContext) -> AsyncIterator[AsyncClient]:
    async with authenticated_client(
        skill_env,
        skill_env.builder_username,
        BUILDER_PASSWORD,
    ) as http_client:
        yield http_client


def tenant_headers(context: SkillContext) -> dict[str, str]:
    return {"X-Tenant-Id": context.tenant_id}


def csrf_headers(client: AsyncClient) -> dict[str, str]:
    return {CSRF_HEADER: client.cookies.get(CSRF_COOKIE) or ""}


async def import_skill(
    client: AsyncClient,
    context: SkillContext,
    package: bytes,
    *,
    version: str = "1.0.0",
    key: str | None = None,
    default_script: str | None = None,
    user_scope: str | None = None,
) -> Response:
    data: dict[str, str] = {"version": version}
    if key is not None:
        data["key"] = key
    if default_script is not None:
        data["default_script"] = default_script
    if user_scope is not None:
        data["user_scope"] = user_scope
    return await client.post(
        "/api/v1/skills/import",
        files={"file": ("skill.zip", package, "application/zip")},
        data=data,
        headers=tenant_headers(context),
    )
