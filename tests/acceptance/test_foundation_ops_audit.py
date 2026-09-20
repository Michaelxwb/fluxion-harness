from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from muad_api import write_config_audit
from muad_common import SharedSettings
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

SessionFactory = async_sessionmaker[AsyncSession]


@pytest.fixture()
async def db() -> AsyncIterator[SessionFactory]:
    database_url = SharedSettings().database_url
    if not database_url:
        pytest.skip("DATABASE_URL not configured")
    engine: AsyncEngine = create_async_engine(database_url)
    factory: SessionFactory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


async def _insert_user(factory: SessionFactory, suffix: str) -> tuple[uuid.UUID, str]:
    user_id, tenant_id = uuid.uuid4(), f"t-audit-{suffix}"
    async with factory() as session:
        await session.execute(
            text(
                "INSERT INTO control.platform_user (id, tenant_id, user_code, display_name) "
                "VALUES (:id, :tenant_id, :user_code, :display_name)"
            ),
            {"id": user_id, "tenant_id": tenant_id, "user_code": f"u-{suffix}", "display_name": "before"},
        )
        await session.commit()
    return user_id, tenant_id


async def _cleanup(factory: SessionFactory, user_id: uuid.UUID) -> None:
    async with factory() as session:
        await session.execute(
            text("DELETE FROM control.config_audit_log WHERE resource_id = :rid"), {"rid": user_id}
        )
        await session.execute(text("DELETE FROM control.platform_user WHERE id = :rid"), {"rid": user_id})
        await session.commit()


async def _fetch(factory: SessionFactory, user_id: uuid.UUID) -> tuple[str, int, dict[str, Any] | None]:
    async with factory() as session:
        name = (
            await session.execute(
                text("SELECT display_name FROM control.platform_user WHERE id = :rid"), {"rid": user_id}
            )
        ).scalar_one()
        audit = (
            await session.execute(
                text(
                    "SELECT before_json FROM control.config_audit_log "
                    "WHERE resource_id = :rid ORDER BY create_time DESC LIMIT 1"
                ),
                {"rid": user_id},
            )
        ).first()
        count = (
            await session.execute(
                text("SELECT count(*) FROM control.config_audit_log WHERE resource_id = :rid"),
                {"rid": user_id},
            )
        ).scalar_one()
        return name, count, (audit[0] if audit else None)


async def test_s11_business_change_and_audit_commit_together(db: SessionFactory) -> None:
    user_id, tenant_id = await _insert_user(db, uuid.uuid4().hex[:8])
    actor = uuid.uuid4()
    try:
        async with db() as session:
            await session.execute(
                text("UPDATE control.platform_user SET display_name = :name WHERE id = :rid"),
                {"name": "after", "rid": user_id},
            )
            await write_config_audit(
                session,
                actor_user_id=actor,
                resource_type="PLATFORM_USER",
                resource_id=user_id,
                action="UPDATE",
                before={"display_name": "before", "api_token": "SECRET-TOKEN-VALUE"},
                after={"display_name": "after", "password": "SECRET-PASSWORD-VALUE"},
                tenant_id=tenant_id,
            )
            await session.commit()

        name, count, before_json = await _fetch(db, user_id)
        assert name == "after"
        assert count == 1
        assert before_json is not None
        assert before_json.get("display_name") == "before"
        assert "api_token" not in before_json
        assert "SECRET-TOKEN-VALUE" not in str(before_json)
    finally:
        await _cleanup(db, user_id)


async def test_e06_audit_failure_rolls_back_business_change(db: SessionFactory) -> None:
    user_id, tenant_id = await _insert_user(db, uuid.uuid4().hex[:8])
    try:
        async with db() as session:
            await session.execute(
                text("UPDATE control.platform_user SET display_name = :name WHERE id = :rid"),
                {"name": "must-rollback", "rid": user_id},
            )
            with pytest.raises(DBAPIError):
                await write_config_audit(
                    session,
                    actor_user_id=uuid.uuid4(),
                    resource_type="X" * 200,
                    resource_id=user_id,
                    action="UPDATE",
                    after={"display_name": "must-rollback"},
                    tenant_id=tenant_id,
                )
            await session.rollback()

        name, count, _ = await _fetch(db, user_id)
        assert name == "before"
        assert count == 0
    finally:
        await _cleanup(db, user_id)


async def test_s11_console_audit_uses_api_kit_primitive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RULE-15：Console 的 config 审计写入必须走 api-kit 的 `write_config_audit` 原语。

    改写时不能依赖数据库：把原语替换成记录器，只验证调用与参数透传。
    """
    import uuid as _uuid

    from muad_api import sanitize_audit_payload
    from muad_console_platform.application import audit_service as console_audit

    assert console_audit.sanitize_payload is sanitize_audit_payload, "控制台仍保留第二套脱敏实现"

    recorded: list[dict[str, Any]] = []

    async def _recorder(session: Any, **kwargs: Any) -> None:
        recorded.append(kwargs)

    monkeypatch.setattr(console_audit, "write_config_audit", _recorder)

    actor = console_audit.AuditActor(account_id=_uuid.uuid4(), source_ip="127.0.0.1")
    service = console_audit.AuditService(session=None)  # type: ignore[arg-type]
    resource_id = _uuid.uuid4()
    await service.record_config_change(
        tenant_id="tenant-x",
        actor=actor,
        resource_type="PLATFORM_USER",
        resource_id=resource_id,
        action="CREATE",
        before=None,
        after={"user_code": "u-1"},
    )

    assert len(recorded) == 1
    call = recorded[0]
    assert call["tenant_id"] == "tenant-x"
    assert call["actor_user_id"] == actor.account_id
    assert call["resource_type"] == "PLATFORM_USER"
    assert call["resource_id"] == resource_id
    assert call["action"] == "CREATE"
    assert call["after"] == {"user_code": "u-1"}
    assert call["source_ip"] == "127.0.0.1"
