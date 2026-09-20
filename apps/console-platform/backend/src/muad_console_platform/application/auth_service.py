import hashlib
import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error, InvalidHashError
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.models.auth import ROLE_ADMIN, ROLE_BUILDER, ConsoleAccount, ConsoleSession
from ..infrastructure.repositories.console_account_repository import ConsoleAccountRepository
from ..infrastructure.repositories.console_session_repository import ConsoleSessionRepository

ROLES = (ROLE_ADMIN, ROLE_BUILDER)
MIN_PASSWORD_LENGTH = 12
MAX_FAILED_ATTEMPTS = 5
LOCK_DURATION = timedelta(minutes=15)
SESSION_TTL = timedelta(hours=12)
SLIDE_THRESHOLD = SESSION_TTL / 2

_PASSWORD_HASHER = PasswordHasher()
_logger = logging.getLogger(__name__)
_DUMMY_HASH = _PASSWORD_HASHER.hash("muad-console-timing-equalizer")


def hash_password(password: str) -> str:
    return _PASSWORD_HASHER.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _PASSWORD_HASHER.verify(password_hash, password)
    except (Argon2Error, InvalidHashError):
        return False


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class AuthService:
    def __init__(self, session: AsyncSession, tenant_id: str | None) -> None:
        self._session = session
        self._tenant_id = tenant_id
        self._accounts = ConsoleAccountRepository(session)
        self._sessions = ConsoleSessionRepository(session)

    async def login(
        self,
        username: str,
        password: str,
        source_ip: str | None,
    ) -> tuple[ConsoleAccount, str]:
        account = await self._find_login_account(username)
        if account is None:
            verify_password(_DUMMY_HASH, password)
            raise AppError(ErrorCode.INVALID_CREDENTIALS)
        now = datetime.now(UTC)
        if not account.enabled:
            verify_password(_DUMMY_HASH, password)
            raise AppError(ErrorCode.INVALID_CREDENTIALS)
        if account.locked_until is not None and account.locked_until > now:
            raise AppError(ErrorCode.ACCOUNT_LOCKED)
        if not verify_password(account.password_hash, password):
            await self._register_failure(account, now)
            raise AppError(ErrorCode.INVALID_CREDENTIALS)
        account.failed_attempts = 0
        account.locked_until = None
        account.last_login_at = now
        account.update_time = now
        token = secrets.token_urlsafe(32)
        session_row = ConsoleSession(
            account_id=account.id,
            token_hash=hash_session_token(token),
            issued_at=now,
            expires_at=now + SESSION_TTL,
            last_seen_at=now,
            source_ip=source_ip,
        )
        await self._sessions.add(session_row)
        return account, token

    def _require_tenant(self) -> str:
        if not self._tenant_id:
            raise RuntimeError("tenant is required for this operation")
        return self._tenant_id

    async def _find_login_account(self, username: str) -> ConsoleAccount | None:
        if self._tenant_id:
            return await self._accounts.find_by_username(self._require_tenant(), username)
        candidates = await self._accounts.find_by_username_global(username)
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            _logger.warning("console_login_ambiguous_username candidates=%s", len(candidates))
        return None

    async def _register_failure(self, account: ConsoleAccount, now: datetime) -> None:
        account.failed_attempts += 1
        if account.failed_attempts >= MAX_FAILED_ATTEMPTS:
            account.failed_attempts = 0
            account.locked_until = now + LOCK_DURATION
        account.update_time = now
        await self._session.commit()

    async def resolve_session(self, token: str) -> ConsoleAccount:
        now = datetime.now(UTC)
        row = await self._sessions.find_by_token_hash(hash_session_token(token))
        if row is None or row.revoked_at is not None or row.expires_at <= now:
            raise AppError(ErrorCode.UNAUTHORIZED)
        account = await self._accounts.get(row.account_id)
        if account is None or not account.enabled:
            raise AppError(ErrorCode.UNAUTHORIZED)
        if row.expires_at - now < SLIDE_THRESHOLD:
            row.expires_at = now + SESSION_TTL
        row.last_seen_at = now
        row.update_time = now
        await self._session.flush()
        return account

    async def logout(self, token: str) -> None:
        row = await self._sessions.find_by_token_hash(hash_session_token(token))
        if row is None or row.revoked_at is not None:
            return
        row.revoked_at = datetime.now(UTC)
        row.update_time = row.revoked_at
        await self._session.flush()

    async def create_account(
        self,
        *,
        username: str,
        password: str,
        display_name: str,
        role: str = ROLE_BUILDER,
    ) -> ConsoleAccount:
        if role not in ROLES:
            raise AppError(ErrorCode.COMMON_BAD_REQUEST)
        if len(password) < MIN_PASSWORD_LENGTH:
            raise AppError(ErrorCode.COMMON_BAD_REQUEST)
        if await self._accounts.find_by_username(self._require_tenant(), username) is not None:
            raise AppError(ErrorCode.ACCOUNT_USERNAME_EXISTS, message_args={"username": username})
        account = ConsoleAccount(
            tenant_id=self._tenant_id,
            username=username,
            display_name=display_name,
            password_hash=hash_password(password),
            role=role,
        )
        try:
            return await self._accounts.add(account)
        except IntegrityError as exc:
            raise AppError(ErrorCode.ACCOUNT_USERNAME_EXISTS, message_args={"username": username}) from exc

    async def change_password(
        self,
        account_id: uuid.UUID,
        current_password: str,
        new_password: str,
    ) -> None:
        # 按 id 在本 session 内重新加载：调用方持有的可能是会话校验器（另一个 session）返回的对象
        account = await self._accounts.get(account_id)
        if account is None or not account.enabled:
            raise AppError(ErrorCode.UNAUTHORIZED)
        if not verify_password(account.password_hash, current_password):
            raise AppError(ErrorCode.INVALID_CREDENTIALS)
        if len(new_password) < MIN_PASSWORD_LENGTH:
            raise AppError(ErrorCode.COMMON_BAD_REQUEST)
        account.password_hash = hash_password(new_password)
        account.failed_attempts = 0
        account.locked_until = None
        account.update_time = datetime.now(UTC)
        await self._session.flush()

    async def list_accounts(self) -> list[ConsoleAccount]:
        return await self._accounts.list(self._require_tenant())

    async def has_any_account(self) -> bool:
        return await self._accounts.count_all() > 0
