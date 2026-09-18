from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from muad_console_platform.infrastructure.db import get_session_factory
from muad_console_platform.infrastructure.models.control import (
    PlatformUser,
    ProjectPlatform,
)
from muad_console_platform.infrastructure.repositories.credential_repository import (
    CredentialRepository,
)
from muad_console_platform.infrastructure.repositories.platform_repository import (
    PlatformRepository,
)
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from console_platform.conftest import TenantContext


def _platform(tenant_id: str, key: str, **overrides: object) -> ProjectPlatform:
    values: dict[str, object] = {
        "key": key,
        "name": f"Platform {key}",
        "resolver_type": "BASE_URL",
        "resolver_config_json": {"base_url": "https://api.example.com"},
        "adapter_key": "generic-http",
        "credential_mode": "USER_ONLY",
        "enabled": True,
    }
    values.update(overrides)
    return ProjectPlatform(tenant_id=tenant_id, **values)


@pytest.fixture
async def seeded_user(tenant: TenantContext) -> AsyncIterator[uuid.UUID]:
    session_factory = get_session_factory()
    async with session_factory() as session:
        user = PlatformUser(
            tenant_id=tenant.tenant_id,
            user_code=f"code-{uuid.uuid4()}",
            display_name="Repo User",
        )
        session.add(user)
        await session.commit()
        user_id = user.id
    try:
        yield user_id
    finally:
        async with session_factory() as session:
            for statement in (
                "DELETE FROM control.user_credential_ref WHERE tenant_id = :tenant_id",
                "DELETE FROM control.shared_credential_ref WHERE tenant_id = :tenant_id",
                "DELETE FROM control.project_platform WHERE tenant_id = :tenant_id",
                "DELETE FROM control.platform_user WHERE tenant_id = :tenant_id",
            ):
                await session.execute(text(statement), {"tenant_id": tenant.tenant_id})
            await session.commit()


async def test_e03_duplicate_key_violates_partial_unique_at_database_level(
    tenant: TenantContext,
) -> None:
    session_factory = get_session_factory()
    key = f"dup-{uuid.uuid4().hex[:8]}"
    async with session_factory() as session:
        repository = PlatformRepository(session)
        await repository.add(_platform(tenant.tenant_id, key))
        await session.commit()
        with pytest.raises(IntegrityError):
            await repository.add(_platform(tenant.tenant_id, key))
        await session.rollback()
    async with session_factory() as session:
        rows, total = await PlatformRepository(session).list(
            tenant.tenant_id, 1, 10, key, None, None, None
        )
    assert total == 1
    assert len(rows) == 1
    assert rows[0].platform.key == key


async def test_list_aggregates_and_user_status(
    tenant: TenantContext, seeded_user: uuid.UUID
) -> None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        repository = PlatformRepository(session)
        first = await repository.add(_platform(tenant.tenant_id, f"a-{uuid.uuid4().hex[:6]}"))
        second = await repository.add(
            _platform(tenant.tenant_id, f"b-{uuid.uuid4().hex[:6]}", enabled=False)
        )
        credentials = CredentialRepository(session)
        await credentials.upsert_user(
            tenant_id=tenant.tenant_id,
            user_id=seeded_user,
            platform_id=first.id,
            credential_json={"token": "plain"},
            schema_version="1",
        )
        await credentials.upsert_shared(
            tenant_id=tenant.tenant_id,
            platform_id=first.id,
            credential_json={"token": "shared"},
            schema_version="1",
        )
        await session.commit()

    async with session_factory() as session:
        repository = PlatformRepository(session)
        rows, total = await repository.list(
            tenant.tenant_id, 1, 10, None, "generic-http", None, seeded_user
        )
        by_id = {row.platform.id: row for row in rows}
        assert total == 2
        assert by_id[first.id].configured_user_credential_count == 1
        assert by_id[first.id].has_shared_credential is True
        assert by_id[first.id].user_credential_status == "ACTIVE"
        assert by_id[second.id].user_credential_status is None
        disabled, disabled_total = await repository.list(
            tenant.tenant_id, 1, 10, None, None, False, None
        )
        assert disabled_total == 1
        assert disabled[0].platform.id == second.id


async def test_invalidate_and_resoft_delete_allows_new_active_row(
    tenant: TenantContext, seeded_user: uuid.UUID
) -> None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        platform = await PlatformRepository(session).add(
            _platform(tenant.tenant_id, f"c-{uuid.uuid4().hex[:6]}")
        )
        credentials = CredentialRepository(session)
        await credentials.upsert_user(
            tenant_id=tenant.tenant_id,
            user_id=seeded_user,
            platform_id=platform.id,
            credential_json={"token": "one"},
            schema_version="1",
        )
        await session.commit()
        platform_id = platform.id

    async with session_factory() as session:
        credentials = CredentialRepository(session)
        changed = await credentials.invalidate_platform(tenant.tenant_id, platform_id)
        await session.commit()
        assert changed == 1
        credential = await credentials.get_user(tenant.tenant_id, seeded_user, platform_id)
        assert credential is not None and credential.status == "INVALID"
        await credentials.soft_delete_user(credential)
        await session.commit()

    async with session_factory() as session:
        credentials = CredentialRepository(session)
        recreated = await credentials.upsert_user(
            tenant_id=tenant.tenant_id,
            user_id=seeded_user,
            platform_id=platform_id,
            credential_json={"token": "two"},
            schema_version="1",
        )
        await session.commit()
        assert recreated.status == "ACTIVE"
        assert recreated.credential_json == {"token": "two"}
