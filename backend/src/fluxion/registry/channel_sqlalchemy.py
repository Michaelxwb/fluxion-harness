from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

from sqlalchemy import func, insert, select, update
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from fluxion.registry.channel_store import (
    BindCodeRecord,
    BindCodeRejected,
    BindRedemption,
    ChannelIdentityRecord,
    ChatAccessRecord,
    PlatformUserRecord,
)
from fluxion.registry.schema import (
    audit_logs,
    bind_codes,
    channel_identities,
    chat_access_tokens,
    platform_users,
)
from fluxion.registry.store import NotFoundError, VersionConflictError


async def create_platform_user(
    engine: AsyncEngine, record: PlatformUserRecord
) -> PlatformUserRecord:
    values = {
        "tenant_id": record.tenant_id,
        "platform_user_id": record.platform_user_id,
        "display_name": record.display_name,
        "created_at": record.created_at,
    }
    try:
        async with engine.begin() as connection:
            await connection.execute(insert(platform_users).values(**values))
    except IntegrityError as exc:
        raise VersionConflictError(f"platform user {record.platform_user_id} exists") from exc
    return record


async def get_platform_user(
    engine: AsyncEngine,
    *,
    tenant_id: str,
    platform_user_id: str,
) -> PlatformUserRecord | None:
    statement = (
        select(platform_users)
        .where(platform_users.c.tenant_id == tenant_id)
        .where(platform_users.c.platform_user_id == platform_user_id)
    )
    async with engine.connect() as connection:
        row = (await connection.execute(statement)).mappings().first()
    return None if row is None else _platform_user_from_row(row)


async def list_platform_users(
    engine: AsyncEngine,
    *,
    tenant_id: str,
    offset: int,
    limit: int,
) -> tuple[list[PlatformUserRecord], int]:
    scope = platform_users.c.tenant_id == tenant_id
    statement = (
        select(platform_users)
        .where(scope)
        .order_by(platform_users.c.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    async with engine.connect() as connection:
        rows = (await connection.execute(statement)).mappings().all()
        total = int(
            (await connection.execute(select(func.count()).select_from(platform_users).where(scope)))
            .scalar_one()
        )
    return [_platform_user_from_row(row) for row in rows], total


async def create_chat_access(
    engine: AsyncEngine,
    record: ChatAccessRecord,
) -> ChatAccessRecord:
    try:
        async with engine.begin() as connection:
            await connection.execute(insert(chat_access_tokens).values(**_chat_access_values(record)))
    except IntegrityError as exc:
        raise VersionConflictError(f"chat access {record.access_id} exists") from exc
    return record


async def resolve_chat_access(
    engine: AsyncEngine,
    *,
    token_hash: str,
) -> ChatAccessRecord | None:
    statement = (
        select(chat_access_tokens)
        .where(chat_access_tokens.c.token_hash == token_hash)
        .where(chat_access_tokens.c.revoked_at.is_(None))
    )
    async with engine.connect() as connection:
        row = (await connection.execute(statement)).mappings().first()
    return None if row is None else _chat_access_from_row(row)


async def revoke_chat_access(
    engine: AsyncEngine,
    *,
    tenant_id: str,
    access_id: str,
    revoked_at: datetime,
) -> ChatAccessRecord:
    statement = (
        update(chat_access_tokens)
        .where(chat_access_tokens.c.tenant_id == tenant_id)
        .where(chat_access_tokens.c.access_id == access_id)
        .where(chat_access_tokens.c.revoked_at.is_(None))
        .values(revoked_at=revoked_at)
    )
    async with engine.begin() as connection:
        result = await connection.execute(statement)
    if result.rowcount != 1:
        raise NotFoundError(f"chat access {access_id} not found")
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                select(chat_access_tokens)
                .where(chat_access_tokens.c.tenant_id == tenant_id)
                .where(chat_access_tokens.c.access_id == access_id)
            )
        ).mappings().one()
    return _chat_access_from_row(row)


async def create_bind_code(engine: AsyncEngine, record: BindCodeRecord) -> BindCodeRecord:
    values = {
        "bind_code_id": record.bind_code_id,
        "tenant_id": record.tenant_id,
        "platform_user_id": record.platform_user_id,
        "code_hash": record.code_hash,
        "expires_at": record.expires_at,
        "failed_attempts": 0,
        "frozen_at": None,
        "consumed_at": None,
        "created_at": record.created_at,
    }
    try:
        async with engine.begin() as connection:
            await connection.execute(insert(bind_codes).values(**values))
    except IntegrityError as exc:
        raise VersionConflictError("bind code already exists") from exc
    return record


async def resolve_channel_identity(
    engine: AsyncEngine, *, tenant_id: str, channel_type: str, channel_user_id: str
) -> ChannelIdentityRecord | None:
    statement = (
        select(channel_identities)
        .where(channel_identities.c.tenant_id == tenant_id)
        .where(channel_identities.c.channel_type == channel_type)
        .where(channel_identities.c.channel_user_id == channel_user_id)
    )
    async with engine.connect() as connection:
        row = (await connection.execute(statement)).mappings().first()
    return None if row is None else _identity_from_row(row)


async def resolve_platform_user_by_channel_id(
    engine: AsyncEngine, *, tenant_id: str, channel_user_id: str
) -> str | None:
    """channel_user_id → platform_user_id（channel_type 无关，ContextResolver 身份回退）。"""
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                select(channel_identities.c.platform_user_id).where(
                    channel_identities.c.tenant_id == tenant_id,
                    channel_identities.c.channel_user_id == channel_user_id,
                )
            )
        ).first()
    return str(row[0]) if row is not None else None


def _is_lock_contention(exc: OperationalError) -> bool:
    # SQLite 并发写以 "database is locked" (SQLITE_BUSY=5) 抛 OperationalError；
    # PG 行锁在 with_for_update 下表现为阻塞后正常读到 consumed_at，不抛
    # OperationalError。故仅识别 SQLite BUSY；其余 OperationalError（磁盘满/
    # 连接中断/约束等）不属锁竞争，由调用方原样向上抛（F7：避免 code 未消费
    # 却误报 used，令用户无法重试）。
    orig = getattr(exc, "orig", exc)
    if getattr(orig, "sqlite_errorcode", None) == 5:  # SQLITE_BUSY
        return True
    return "database is locked" in str(exc).lower()


async def redeem_bind_code(
    engine: AsyncEngine, redemption: BindRedemption
) -> ChannelIdentityRecord:
    rejection: str | None = None
    identity: ChannelIdentityRecord | None = None
    try:
        async with engine.begin() as connection:
            row = (
                await connection.execute(
                    select(bind_codes)
                    .where(bind_codes.c.code_hash == redemption.code_hash)
                    .with_for_update()
                )
            ).mappings().first()
            rejection = _rejection_reason(row, redemption)
            if rejection is not None:
                await _record_failed_attempt(connection, row, redemption.now)
                await connection.execute(
                    insert(audit_logs).values(**_rejection_audit_values(redemption, rejection))
                )
            else:
                assert row is not None
                identity = await _consume_and_bind(connection, row, redemption)
    except OperationalError as exc:
        # SQLite 并发写以 "database is locked" (SQLITE_BUSY) 视作「另一个请求正在
        # 消耗该 code」→ 干净的 used 拒绝。但 OperationalError 还覆盖磁盘满/连接
        # 中断等——这些 code 未消费却误报 used 会让用户无法重试。故仅锁竞争转
        # used，其余原样向上抛（F7）。
        if _is_lock_contention(exc):
            raise BindCodeRejected("used") from exc
        raise
    if rejection is not None:
        raise BindCodeRejected(rejection)
    if identity is None:
        raise BindCodeRejected("used")
    return identity


def _rejection_reason(row: RowMapping | None, redemption: BindRedemption) -> str | None:
    if row is None:
        return "invalid"
    if row["consumed_at"] is not None:
        return "used"
    if row["frozen_at"] is not None or cast(int, row["failed_attempts"]) >= 5:
        return "frozen"
    if str(row["tenant_id"]) != redemption.tenant_id:
        return "tenant"
    expires_at = _aware(cast(datetime, row["expires_at"]))
    if expires_at <= redemption.now:
        return "expired"
    return None


async def _record_failed_attempt(
    connection: AsyncConnection, row: RowMapping | None, now: datetime
) -> None:
    if row is None:
        return
    if row["consumed_at"] is not None or row["frozen_at"] is not None:
        return
    attempts = cast(int, row["failed_attempts"]) + 1
    await connection.execute(
        update(bind_codes)
        .where(bind_codes.c.bind_code_id == str(row["bind_code_id"]))
        .values(failed_attempts=attempts, frozen_at=now if attempts >= 5 else None)
    )


async def _consume_and_bind(
    connection: AsyncConnection, row: RowMapping, redemption: BindRedemption
) -> ChannelIdentityRecord | None:
    result = await connection.execute(
        update(bind_codes)
        .where(bind_codes.c.bind_code_id == str(row["bind_code_id"]))
        .where(bind_codes.c.consumed_at.is_(None))
        .where(bind_codes.c.frozen_at.is_(None))
        .values(consumed_at=redemption.now)
    )
    if result.rowcount != 1:
        return None
    identity = ChannelIdentityRecord(
        tenant_id=redemption.tenant_id,
        channel_type=redemption.channel_type,
        channel_user_id=redemption.channel_user_id,
        platform_user_id=str(row["platform_user_id"]),
        created_at=redemption.now,
    )
    try:
        await connection.execute(insert(channel_identities).values(**_identity_values(identity)))
    except IntegrityError as exc:
        raise BindCodeRejected("identity_bound") from exc
    await connection.execute(insert(audit_logs).values(**_audit_values(identity, redemption)))
    return identity


def _identity_values(identity: ChannelIdentityRecord) -> dict[str, object]:
    return {
        "tenant_id": identity.tenant_id,
        "channel_type": identity.channel_type,
        "channel_user_id": identity.channel_user_id,
        "platform_user_id": identity.platform_user_id,
        "created_at": identity.created_at,
    }


def _audit_values(identity: ChannelIdentityRecord, redemption: BindRedemption) -> dict[str, object]:
    return {
        "audit_id": redemption.audit_id,
        "tenant_id": redemption.tenant_id,
        "actor_id": f"{redemption.channel_type}:{redemption.channel_user_id}",
        "request_id": redemption.request_id,
        "action": "channel.bind",
        "target_type": "platform_user_identity",
        "target_id": identity.platform_user_id,
        "before_json": None,
        "after_json": {
            "channel_type": identity.channel_type,
            "channel_user_id": identity.channel_user_id,
            "result": "bound",
        },
        "created_at": redemption.now,
    }


def _rejection_audit_values(
    redemption: BindRedemption, reason: str
) -> dict[str, object]:
    return {
        "audit_id": redemption.audit_id,
        "tenant_id": redemption.tenant_id,
        "actor_id": f"{redemption.channel_type}:{redemption.channel_user_id}",
        "request_id": redemption.request_id,
        "action": "channel.bind.reject",
        "target_type": "platform_user_identity",
        "target_id": f"{redemption.channel_type}:{redemption.channel_user_id}",
        "before_json": None,
        "after_json": {"channel_type": redemption.channel_type, "result": reason},
        "created_at": redemption.now,
    }


def _identity_from_row(row: RowMapping) -> ChannelIdentityRecord:
    return ChannelIdentityRecord(
        tenant_id=str(row["tenant_id"]),
        channel_type=str(row["channel_type"]),
        channel_user_id=str(row["channel_user_id"]),
        platform_user_id=str(row["platform_user_id"]),
        created_at=_aware(cast(datetime, row["created_at"])),
    )


def _platform_user_from_row(row: RowMapping) -> PlatformUserRecord:
    return PlatformUserRecord(
        tenant_id=str(row["tenant_id"]),
        platform_user_id=str(row["platform_user_id"]),
        display_name=str(row["display_name"]),
        created_at=_aware(cast(datetime, row["created_at"])),
    )


def _chat_access_values(record: ChatAccessRecord) -> dict[str, object]:
    return {
        "access_id": record.access_id,
        "tenant_id": record.tenant_id,
        "platform_user_id": record.platform_user_id,
        "agent_id": record.agent_id,
        "token_hash": record.token_hash,
        "created_at": record.created_at,
        "revoked_at": record.revoked_at,
    }


def _chat_access_from_row(row: RowMapping) -> ChatAccessRecord:
    revoked_at = cast(datetime | None, row["revoked_at"])
    return ChatAccessRecord(
        access_id=str(row["access_id"]),
        tenant_id=str(row["tenant_id"]),
        platform_user_id=str(row["platform_user_id"]),
        agent_id=str(row["agent_id"]),
        token_hash=str(row["token_hash"]),
        created_at=_aware(cast(datetime, row["created_at"])),
        revoked_at=None if revoked_at is None else _aware(revoked_at),
    )


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
