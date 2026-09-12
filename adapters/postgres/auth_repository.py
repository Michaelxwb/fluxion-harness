from __future__ import annotations

import hmac
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from adapters.postgres.base import DEFAULT_TENANT_ID
from adapters.postgres.models import PlatformUserModel, SessionTokenModel
from framework.web.errors import AppError
from framework.web.security import (
    ROLE_ADMIN,
    SessionPrincipal,
    generate_session_token,
    hash_password,
    hash_token,
    verify_password,
)

SESSION_TTL = timedelta(hours=12)


class AuthRepository:
    """Login/session storage for the Console control plane (ADR-021)."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def bootstrap_admin(self, username: str, password: str) -> UUID | None:
        """Create the initial ADMIN platform_user if none exists yet (fail-closed)."""
        async with self._session_factory() as session:
            async with session.begin():
                existing = await session.scalar(
                    select(PlatformUserModel)
                    .where(
                        PlatformUserModel.role == ROLE_ADMIN,
                        PlatformUserModel.status == "ACTIVE",
                        PlatformUserModel.password_hash.is_not(None),
                    )
                    .limit(1)
                )
                if existing is not None:
                    return None
                user = PlatformUserModel(
                    tenant_id=DEFAULT_TENANT_ID,
                    user_key=username,
                    display_name=username,
                    password_hash=hash_password(password),
                    role=ROLE_ADMIN,
                    status="ACTIVE",
                )
                session.add(user)
                await session.flush()
                return user.id

    async def login(self, username: str, password: str) -> tuple[str, SessionPrincipal]:
        """Verify credentials and mint a bearer token; returns (token, principal)."""
        async with self._session_factory() as session:
            async with session.begin():
                user = await session.scalar(
                    select(PlatformUserModel).where(
                        PlatformUserModel.user_key == username,
                        PlatformUserModel.tenant_id == DEFAULT_TENANT_ID,
                        PlatformUserModel.status == "ACTIVE",
                        PlatformUserModel.is_deleted.is_(False),
                    )
                )
                if (
                    user is None
                    or user.password_hash is None
                    or not verify_password(password, user.password_hash)
                ):
                    raise AppError(
                        code="AUTH_INVALID_CREDENTIALS",
                        message="invalid username or password",
                        status_code=401,
                    )
                token = generate_session_token()
                session.add(
                    SessionTokenModel(
                        tenant_id=DEFAULT_TENANT_ID,
                        platform_user_id=user.id,
                        token_hash=hash_token(token),
                        expires_at=datetime.now(UTC) + SESSION_TTL,
                    )
                )
                return token, SessionPrincipal(
                    account_id=str(user.id),
                    tenant_id=user.tenant_id,
                    username=user.user_key,
                    role=user.role,
                )

    async def resolve(self, token: str) -> SessionPrincipal:
        """Resolve a bearer token to its principal; expired/revoked sessions fail."""
        async with self._session_factory() as session:
            row = await session.scalar(
                select(SessionTokenModel).where(SessionTokenModel.token_hash == hash_token(token))
            )
            now = datetime.now(UTC)
            if (
                row is None
                or row.revoked_at is not None
                or row.expires_at <= now
                or row.is_deleted
            ):
                raise AppError(
                    code="AUTH_SESSION_INVALID",
                    message="session invalid or expired",
                    status_code=401,
                )
            # 每次解析都读当前 platform_user：角色/状态变更立即生效，
            # 客户端不得缓存角色快照（ADR-067/D14 + P0-8）。
            user = await session.scalar(
                select(PlatformUserModel).where(
                    PlatformUserModel.id == row.platform_user_id,
                    PlatformUserModel.status == "ACTIVE",
                    PlatformUserModel.is_deleted.is_(False),
                )
            )
            if user is None:
                raise AppError(code="AUTH_SESSION_INVALID", message="account disabled", status_code=401)
            if not hmac.compare_digest(user.tenant_id, row.tenant_id):
                raise AppError(code="AUTH_SESSION_INVALID", message="tenant mismatch", status_code=401)
            return SessionPrincipal(
                account_id=str(user.id),
                tenant_id=user.tenant_id,
                username=user.user_key,
                role=user.role,
            )

    async def logout(self, token: str) -> None:
        async with self._session_factory() as session:
            async with session.begin():
                await session.execute(
                    update(SessionTokenModel)
                    .where(SessionTokenModel.token_hash == hash_token(token))
                    .values(revoked_at=datetime.now(UTC))
                )
