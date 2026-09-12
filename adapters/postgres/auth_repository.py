from __future__ import annotations

import hmac
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from adapters.postgres.base import DEFAULT_TENANT_ID
from adapters.postgres.models import AuthAccountModel, AuthSessionModel
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
        """Create the initial ADMIN account if no enabled account exists yet."""
        async with self._session_factory() as session:
            async with session.begin():
                existing = await session.scalar(
                    select(AuthAccountModel)
                    .where(AuthAccountModel.role == ROLE_ADMIN, AuthAccountModel.enabled.is_(True))
                    .limit(1)
                )
                if existing is not None:
                    return None
                account = AuthAccountModel(
                    tenant_id=DEFAULT_TENANT_ID,
                    username=username,
                    password_hash=hash_password(password),
                    role=ROLE_ADMIN,
                    enabled=True,
                )
                session.add(account)
                await session.flush()
                return account.id

    async def login(self, username: str, password: str) -> tuple[str, SessionPrincipal]:
        """Verify credentials and mint a bearer token; returns (token, principal)."""
        async with self._session_factory() as session:
            async with session.begin():
                account = await session.scalar(
                    select(AuthAccountModel).where(
                        AuthAccountModel.username == username,
                        AuthAccountModel.tenant_id == DEFAULT_TENANT_ID,
                        AuthAccountModel.enabled.is_(True),
                        AuthAccountModel.is_deleted.is_(False),
                    )
                )
                if account is None or not verify_password(password, account.password_hash):
                    raise AppError(
                        code="AUTH_INVALID_CREDENTIALS",
                        message="invalid username or password",
                        status_code=401,
                    )
                token = generate_session_token()
                session.add(
                    AuthSessionModel(
                        tenant_id=DEFAULT_TENANT_ID,
                        account_id=account.id,
                        token_hash=hash_token(token),
                        expires_at=datetime.now(UTC) + SESSION_TTL,
                    )
                )
                return token, SessionPrincipal(
                    account_id=str(account.id),
                    tenant_id=account.tenant_id,
                    username=account.username,
                    role=account.role,
                )

    async def resolve(self, token: str) -> SessionPrincipal:
        """Resolve a bearer token to its principal; expired/revoked sessions fail."""
        async with self._session_factory() as session:
            row = await session.scalar(
                select(AuthSessionModel).where(AuthSessionModel.token_hash == hash_token(token))
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
            account = await session.scalar(
                select(AuthAccountModel).where(
                    AuthAccountModel.id == row.account_id,
                    AuthAccountModel.enabled.is_(True),
                    AuthAccountModel.is_deleted.is_(False),
                )
            )
            if account is None:
                raise AppError(code="AUTH_SESSION_INVALID", message="account disabled", status_code=401)
            if not hmac.compare_digest(account.tenant_id, row.tenant_id):
                raise AppError(code="AUTH_SESSION_INVALID", message="tenant mismatch", status_code=401)
            return SessionPrincipal(
                account_id=str(account.id),
                tenant_id=account.tenant_id,
                username=account.username,
                role=account.role,
            )

    async def logout(self, token: str) -> None:
        async with self._session_factory() as session:
            async with session.begin():
                await session.execute(
                    update(AuthSessionModel)
                    .where(AuthSessionModel.token_hash == hash_token(token))
                    .values(revoked_at=datetime.now(UTC))
                )
