"""[B-240][B-08] Token 交换与独立 CHAT 会话生命周期（design §3.3 / §3.4 API-04 / §3.7；TASK-040）。

真实边界（业务路径不 mock）：

- **真实 PostgreSQL 与事务**：`control.chat_access_token` / `control.chat_session` 的
  partial unique、"只存摘要"约束、`SELECT … FOR UPDATE` 真实行锁与同事务撤销都由库兜底；
  期限裁决用 **PG 服务端时间**（`now()` 在事务内固定）——"到期瞬间"用例在同一事务里构造
  `expires_at = now()` 的边界，不做任何时钟替身；
- **真实 Console 应用服务与 HTTP 依赖**：直接驱动 `ChatSessionService` / `ChatTokenService`；
  "CHAT 会话不是 Console 凭据"用例走真实 ASGI `muad_console_platform.main.app`
  （真实会话裁决依赖），不伪造主体；
- **真实设置源**：会话期限只来自 `control.platform_setting`（无版本行 = schema 默认 12 小时），
  损坏文档显式 503、不静默回落；**读取路径不读设置**；
- **明文卫生**：用户 Token 原文只在签发响应出现一次、会话原文只在交换返回值出现一次，库里只有
  各自的 SHA-256 摘要；审计/日志/被拒响应里都不得出现原文或摘要；
- 本任务只做服务与仓储层：Cookie/CSRF/公开路由（API-04 端点）由 TASK-041/042 落实，
  因此本文件不提交 HTTP Token、不签 Cookie——那些断言归属后续任务；本路径不发起任何外部模型
  调用，故"外部模型真实 HTTP 探针"边界在此不适用。

清库隔离：`tests/console_auth/conftest.py` 的租户清理不含本需求新表；本文件自带按 `tenant_id`
的清理（会话→Token→用户→设置→审计，顺序满足外键），tenant 每用例唯一。
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_console_platform.application.audit_service import AuditActor
from muad_console_platform.application.chat_auth_service import (
    ChatSessionGrant,
    ChatSessionService,
)
from muad_console_platform.application.chat_token_service import (
    ChatTokenService,
    hash_chat_token,
)
from muad_console_platform.infrastructure.db import get_engine, get_session_factory
from muad_console_platform.infrastructure.models.auth import (
    ChatAccessToken,
    ChatSession,
    ConsoleSession,
)
from muad_console_platform.infrastructure.models.control import (
    ConfigAuditLog,
    PlatformSetting,
    PlatformUser,
)
from muad_contracts.webchat import WebchatSessionInfo
from pydantic import SecretStr
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

DEFAULT_SESSION_TTL = timedelta(hours=12)
#: 256 位熵的 url-safe base64 至少 43 个字符（43 × 6 = 258 位 ≥ 256 位）。
MIN_TOKEN_CHARS = 43
#: API-04 的公开会话信息恰好这七个字段：多一个都是泄漏面。
SESSION_INFO_FIELDS = {
    "tenant_id",
    "user_id",
    "display_name",
    "session_id",
    "expires_at",
    "auth_context_id",
    "auth_method",
}
SERVICE_LOGGER = "muad_console_platform.application.chat_auth_service"
PURGE_STATEMENTS = (
    "DELETE FROM control.chat_session WHERE tenant_id = :tenant_id",
    "DELETE FROM control.chat_access_token WHERE tenant_id = :tenant_id",
    "DELETE FROM control.platform_user WHERE tenant_id = :tenant_id",
    "DELETE FROM control.platform_setting WHERE tenant_id = :tenant_id",
    "DELETE FROM control.config_audit_log WHERE tenant_id = :tenant_id",
)


@dataclass(frozen=True)
class SessionScope:
    """本用例租户里的目标用户 + 同租户旁观用户 + 另一租户的用户（跨租户不串读）。"""

    tenant_id: str
    user_id: uuid.UUID
    display_name: str
    other_user_id: uuid.UUID
    foreign_tenant_id: str
    foreign_user_id: uuid.UUID


def _actor() -> AuditActor:
    return AuditActor(account_id=uuid.uuid4(), source_ip="203.0.113.11")


async def _purge_tenant(tenant_id: str) -> None:
    async with get_session_factory()() as session:
        for statement in PURGE_STATEMENTS:
            await session.execute(text(statement), {"tenant_id": tenant_id})
        await session.commit()


@pytest.fixture
async def session_scope(database_guard: None) -> AsyncIterator[SessionScope]:
    """真实平台用户：目标用户、同租户旁观用户、外租户用户。"""
    tenant_id = f"chat-{uuid.uuid4()}"
    foreign_tenant_id = f"chat-foreign-{uuid.uuid4()}"
    factory = get_session_factory()
    async with factory() as session:
        users = [
            PlatformUser(
                tenant_id=tenant_id,
                user_code=f"u-{uuid.uuid4().hex[:8]}",
                display_name="Chat Owner",
            ),
            PlatformUser(
                tenant_id=tenant_id,
                user_code=f"u-{uuid.uuid4().hex[:8]}",
                display_name="Chat Bystander",
            ),
            PlatformUser(
                tenant_id=foreign_tenant_id,
                user_code=f"u-{uuid.uuid4().hex[:8]}",
                display_name="Foreign Chat User",
            ),
        ]
        session.add_all(users)
        await session.commit()
        scope = SessionScope(
            tenant_id=tenant_id,
            user_id=users[0].id,
            display_name="Chat Owner",
            other_user_id=users[1].id,
            foreign_tenant_id=foreign_tenant_id,
            foreign_user_id=users[2].id,
        )
    try:
        yield scope
    finally:
        await _purge_tenant(tenant_id)
        await _purge_tenant(foreign_tenant_id)


# --- 服务驱动（真实服务、真实事务；提交由调用方决定） ---------------------------------


async def _issue_user_token(
    scope: SessionScope, *, user_id: uuid.UUID | None = None, tenant_id: str | None = None
) -> str:
    """真实 `ChatTokenService` 首签：拿到可直接交换的用户 Token 原文。"""
    async with get_session_factory()() as session:
        issued = await ChatTokenService(session).issue_token(
            tenant_id=tenant_id or scope.tenant_id,
            user_id=user_id or scope.user_id,
            expected_revision=0,
            actor=_actor(),
        )
        await session.commit()
        return issued.token.get_secret_value()


async def _exchange(token: str, session: AsyncSession | None = None) -> ChatSessionGrant:
    if session is not None:
        return await ChatSessionService(session).exchange(SecretStr(token))
    async with get_session_factory()() as owned:
        grant = await ChatSessionService(owned).exchange(SecretStr(token))
        await owned.commit()
        return grant


async def _resolve(session_token: str, session: AsyncSession | None = None) -> WebchatSessionInfo:
    if session is not None:
        return await ChatSessionService(session).resolve(SecretStr(session_token))
    async with get_session_factory()() as owned:
        return await ChatSessionService(owned).resolve(SecretStr(session_token))


async def _logout(session_token: str, session: AsyncSession | None = None) -> None:
    if session is not None:
        await ChatSessionService(session).logout(SecretStr(session_token))
        return
    async with get_session_factory()() as owned:
        await ChatSessionService(owned).logout(SecretStr(session_token))
        await owned.commit()


# --- 库内回读 -------------------------------------------------------------------------


async def _token_rows(tenant_id: str) -> list[ChatAccessToken]:
    async with get_session_factory()() as session:
        rows = await session.scalars(
            select(ChatAccessToken)
            .where(ChatAccessToken.tenant_id == tenant_id)
            .order_by(ChatAccessToken.revision)
        )
        return list(rows)


async def _session_rows(tenant_id: str) -> list[ChatSession]:
    async with get_session_factory()() as session:
        rows = await session.scalars(
            select(ChatSession)
            .where(ChatSession.tenant_id == tenant_id)
            .order_by(ChatSession.issued_at, ChatSession.id)
        )
        return list(rows)


async def _audit_rows(tenant_id: str) -> list[ConfigAuditLog]:
    async with get_session_factory()() as session:
        rows = await session.scalars(
            select(ConfigAuditLog)
            .where(ConfigAuditLog.tenant_id == tenant_id)
            .order_by(ConfigAuditLog.create_time)
        )
        return list(rows)


def _row_dump(row: Any) -> str:
    """整行（含摘要列）的文本快照：用于断言库内任何列都不含凭据原文。"""
    return str({column.name: getattr(row, column.name) for column in row.__table__.columns})


def _audit_dump(rows: list[ConfigAuditLog]) -> str:
    return str(
        [{"action": row.action, "before": row.before_json, "after": row.after_json} for row in rows]
    )


def _log_dump(records: list[logging.LogRecord]) -> str:
    """服务的结构化日志字段（含 extra）：用于断言日志里没有凭据原文与摘要。"""
    return str([{key: str(value) for key, value in record.__dict__.items()} for record in records])


def _service_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.name == SERVICE_LOGGER]


async def _add_settings_revision(tenant_id: str, revision: int, document: dict[str, Any]) -> None:
    async with get_session_factory()() as session:
        session.add(
            PlatformSetting(
                tenant_id=tenant_id,
                revision=revision,
                settings_json=document,
                actor_user_id=uuid.uuid4(),
            )
        )
        await session.commit()


async def _set_token_times(
    tenant_id: str, revision: int, *, issued_at: Any, expires_at: Any
) -> None:
    """直接改 Token 的期限列（真实 SQL）：构造"只剩 X 小时"或"已过期未撤销"的边界。"""
    async with get_session_factory()() as session:
        await session.execute(
            sa.update(ChatAccessToken)
            .where(
                ChatAccessToken.tenant_id == tenant_id,
                ChatAccessToken.revision == revision,
            )
            .values(issued_at=issued_at, expires_at=expires_at)
        )
        await session.commit()


async def _set_user_status(scope: SessionScope, status: str) -> None:
    async with get_session_factory()() as session:
        await session.execute(
            sa.update(PlatformUser)
            .where(
                PlatformUser.tenant_id == scope.tenant_id,
                PlatformUser.id == scope.user_id,
            )
            .values(status=status)
        )
        await session.commit()


# --- 真实 PG 锁观测（辅助） ------------------------------------------------------------


async def _wait_until(predicate: Callable[[], Awaitable[bool]], *, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if await predicate():
            return True
        await asyncio.sleep(0.02)
    return False


async def _blocking_pids(pid: int) -> list[int]:
    """`pg_blocking_pids`：谁在挡这个后端（未获锁的等待者）。"""
    async with get_session_factory()() as session:
        rows = await session.scalars(text("SELECT unnest(pg_blocking_pids(:pid))"), {"pid": pid})
        return [int(row) for row in rows]


async def _blocked_by(waiter_pid: int, holder_pid: int) -> bool:
    return holder_pid in await _blocking_pids(waiter_pid)


# --- B-240：交换、独立会话与生命周期 --------------------------------------------------


async def test_b240_exchange_creates_an_independent_session_after_validating_source(
    session_scope: SessionScope, caplog: pytest.LogCaptureFixture
) -> None:
    """[B-240] 交换：核验来源 Token/用户后生成**独立**会话；原文与摘要不进库外任何记录。"""
    caplog.set_level(logging.INFO, logger=SERVICE_LOGGER)
    user_token = await _issue_user_token(session_scope)
    token_row = (await _token_rows(session_scope.tenant_id))[0]

    grant = await _exchange(user_token)
    session_token = grant.session_token.get_secret_value()
    info = grant.info

    assert set(info.model_dump()) == SESSION_INFO_FIELDS, "公开会话信息不得多带字段"
    assert info.auth_method == "USER_TOKEN" and info.auth_context_id == info.session_id
    assert info.tenant_id == session_scope.tenant_id
    assert info.user_id == session_scope.user_id
    assert info.display_name == session_scope.display_name, "展示名由服务端从用户记录导出"
    assert "session_token" not in info.model_dump() and session_token not in repr(grant)
    assert len(session_token) >= MIN_TOKEN_CHARS, "独立会话原文同样至少 256 位熵"

    rows = await _session_rows(session_scope.tenant_id)
    assert len(rows) == 1
    row = rows[0]
    assert row.token_hash == hash_chat_token(session_token)
    assert row.token_hash != hash_chat_token(user_token), "会话摘要与用户 Token 摘要相互独立"
    assert user_token not in _row_dump(row) and session_token not in _row_dump(row)
    assert (row.access_token_id, row.platform_user_id, row.revoked_at) == (
        token_row.id,
        session_scope.user_id,
        None,
    )
    assert row.issued_at < row.expires_at
    assert info.session_id == row.id and info.expires_at == row.expires_at

    # 交换不改写来源 Token：不续期、不撤销、不换身份。
    tokens = await _token_rows(session_scope.tenant_id)
    assert [(item.revision, item.revoked_at, item.expires_at) for item in tokens] == [
        (1, None, token_row.expires_at)
    ]

    # 明文不进审计（交换不是配置变更，不新增审计行）、不进日志。
    audits = await _audit_rows(session_scope.tenant_id)
    assert [audit.action for audit in audits] == ["CREATE"], "只有签发写了审计，交换自身不写配置审计"
    for secret in (user_token, session_token):
        assert secret not in _audit_dump(audits)
        assert secret not in _log_dump(_service_records(caplog))
    assert hash_chat_token(session_token) not in _audit_dump(audits), "会话摘要也不进审计"

    # 伪造/未知 Token 不建会话；会话原文也不能反过来再交换一次。
    for bogus in ("not-a-real-token", session_token):
        with pytest.raises(AppError) as invalid:
            await _exchange(bogus)
        assert invalid.value.code == ErrorCode.CHAT_TOKEN_INVALID
        assert bogus not in str(invalid.value)
    assert len(await _session_rows(session_scope.tenant_id)) == 1


async def test_b240_session_expiry_is_min_of_exchange_ttl_and_source_remaining(
    session_scope: SessionScope,
) -> None:
    """[B-240] 绝对期限 = min(交换 + session_ttl, 来源 Token 到期)；idle TTL 同设置。"""
    default_token = await _issue_user_token(session_scope)
    default_grant = await _exchange(default_token)
    rows = {row.id: row for row in await _session_rows(session_scope.tenant_id)}
    default_row = rows[default_grant.info.session_id]
    assert default_grant.idle_ttl_sec == int(DEFAULT_SESSION_TTL.total_seconds())
    assert abs((default_row.expires_at - default_row.issued_at) - DEFAULT_SESSION_TTL) <= timedelta(seconds=1)
    assert default_grant.info.expires_at == default_row.expires_at

    # 设置 24 小时：仍未越过 30 天 Token 的到期时刻，取交换时刻 + 24 小时。
    await _add_settings_revision(
        session_scope.tenant_id, 1, {"webchat": {"session_ttl_hours": 24}}
    )
    long_token = await _issue_user_token(session_scope, user_id=session_scope.other_user_id)
    long_grant = await _exchange(long_token)
    long_row = {row.id: row for row in await _session_rows(session_scope.tenant_id)}[
        long_grant.info.session_id
    ]
    assert long_grant.idle_ttl_sec == int(timedelta(hours=24).total_seconds())
    assert abs((long_row.expires_at - long_row.issued_at) - timedelta(hours=24)) <= timedelta(seconds=1)

    # 设置 1 小时：只影响后续交换，已建立的会话不漂移。
    await _add_settings_revision(
        session_scope.tenant_id, 2, {"webchat": {"session_ttl_hours": 1}}
    )
    short_grant = await _exchange(default_token)
    short_row = {row.id: row for row in await _session_rows(session_scope.tenant_id)}[
        short_grant.info.session_id
    ]
    assert short_grant.idle_ttl_sec == int(timedelta(hours=1).total_seconds())
    assert abs((short_row.expires_at - short_row.issued_at) - timedelta(hours=1)) <= timedelta(seconds=1)
    assert rows[default_grant.info.session_id].expires_at == (
        await _session_rows(session_scope.tenant_id)
    )[0].expires_at, "改设置不影响已建立的会话"

    # Token 只剩 1 小时：会话期限被来源期限压到恰好 1 小时（min 取来源）。
    foreign_token = await _issue_user_token(
        session_scope,
        user_id=session_scope.foreign_user_id,
        tenant_id=session_scope.foreign_tenant_id,
    )
    await _set_token_times(
        session_scope.foreign_tenant_id,
        1,
        issued_at=sa.text("now() - interval '1 hour'"),
        expires_at=sa.text("now() + interval '1 hour'"),
    )
    bound_grant = await _exchange(foreign_token)
    bound_row = (await _session_rows(session_scope.foreign_tenant_id))[0]
    bound_expires = (await _token_rows(session_scope.foreign_tenant_id))[0].expires_at
    assert bound_row.expires_at == bound_expires, "min 取来源：会话恰好在来源 Token 到期时结束"
    assert timedelta(minutes=59) < bound_row.expires_at - bound_row.issued_at <= timedelta(hours=1), (
        "剩余不足 12 小时的来源 Token 只能换来 ≤ 剩余期限的会话"
    )
    assert bound_grant.info.expires_at == bound_row.expires_at


async def test_resolve_uses_one_joined_query_and_preserves_error_priority(
    session_scope: SessionScope,
) -> None:
    """[S-14/TASK-002] 读取路径一次 JOIN 取齐会话/来源 Token/用户；错误优先级不变。"""
    token = await _issue_user_token(session_scope)
    grant = await _exchange(token)
    statements: list[str] = []

    @sa.event.listens_for(get_engine().sync_engine, "before_cursor_execute")
    def _capture(conn, cursor, statement, parameters, context, executemany):  # type: ignore[no-untyped-def]
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    try:
        info = await _resolve(grant.session_token.get_secret_value())
    finally:
        sa.event.remove(get_engine().sync_engine, "before_cursor_execute", _capture)

    assert info.user_id == session_scope.user_id
    assert len(statements) == 1, (
        "resolve 只允许一条 SELECT（session+token+user JOIN）；"
        f"实际 {len(statements)} 条：{statements}"
    )

    with pytest.raises(AppError) as missing:
        await _resolve("not-a-real-session-token")
    assert missing.value.code == ErrorCode.UNAUTHORIZED


async def test_b240_resolve_reads_live_session_and_source_without_sliding(
    session_scope: SessionScope,
) -> None:
    """[B-240] 读取：按摘要定位实时校验会话/来源 Token/用户状态，绝不滑动续期。"""
    user_token = await _issue_user_token(session_scope)
    grant = await _exchange(user_token)
    session_token = grant.session_token.get_secret_value()
    before_session = (await _session_rows(session_scope.tenant_id))[0]
    before_token = (await _token_rows(session_scope.tenant_id))[0]

    for _ in range(3):
        info = await _resolve(session_token)
        assert info.model_dump() == grant.info.model_dump()

    after_session = (await _session_rows(session_scope.tenant_id))[0]
    assert (after_session.issued_at, after_session.expires_at, after_session.update_time) == (
        before_session.issued_at,
        before_session.expires_at,
        before_session.update_time,
    ), "读请求不得滑动续期，也不得改写会话行"
    assert (await _token_rows(session_scope.tenant_id))[0].expires_at == before_token.expires_at
    assert len(await _session_rows(session_scope.tenant_id)) == 1

    # 已认证请求不读设置：设置源损坏也不影响既有会话的读取。
    await _add_settings_revision(session_scope.tenant_id, 1, {"webchat": {"session_ttl_hours": 0}})
    assert (await _resolve(session_token)).session_id == grant.info.session_id

    # 匿名会话：未知摘要一律 UNAUTHORIZED，不回显输入。
    unknown = "unknown-session-token"
    with pytest.raises(AppError) as anonymous:
        await _resolve(unknown)
    assert anonymous.value.code == ErrorCode.UNAUTHORIZED and unknown not in str(anonymous.value)

    # 后续认证同时验证会话与来源 Token/用户状态：任何一侧失效都拒绝读取。
    await _set_user_status(session_scope, "DISABLED")
    with pytest.raises(AppError) as disabled:
        await _resolve(session_token)
    assert disabled.value.code == ErrorCode.CHAT_SESSION_REVOKED
    await _set_user_status(session_scope, "ACTIVE")
    assert (await _resolve(session_token)).session_id == grant.info.session_id

    async with get_session_factory()() as session:
        await session.execute(
            sa.update(ChatAccessToken)
            .where(ChatAccessToken.tenant_id == session_scope.tenant_id)
            .values(revoked_at=sa.text("now()"))
        )
        await session.commit()
    with pytest.raises(AppError) as revoked_source:
        await _resolve(session_token)
    assert revoked_source.value.code == ErrorCode.CHAT_SESSION_REVOKED, (
        "来源 Token 被直接撤销时，未同步撤销的会话也不得继续可用"
    )


async def test_b240_expiry_instant_is_rejected_for_token_and_session(
    session_scope: SessionScope,
) -> None:
    """[B-08] 到期**瞬间**拒绝：`expires_at = now()`（同一事务的 PG 时间）即不可交换/读取。"""
    user_token = await _issue_user_token(session_scope)
    async with get_session_factory()() as session:
        await session.execute(
            sa.text(
                "UPDATE control.chat_access_token SET issued_at = now() - interval '1 hour', "
                "expires_at = now() WHERE tenant_id = :tenant_id AND revision = 1"
            ),
            {"tenant_id": session_scope.tenant_id},
        )
        with pytest.raises(AppError) as expired_instant:
            await _exchange(user_token, session)
        assert expired_instant.value.code == ErrorCode.CHAT_TOKEN_EXPIRED
        await session.rollback()
    assert await _session_rows(session_scope.tenant_id) == [], "到期瞬间不得留下会话行"

    # 会话到期瞬间：同一事务里构造 `expires_at = now()` 后读取必须拒绝（不滑动）。
    grant = await _exchange(user_token)
    session_token = grant.session_token.get_secret_value()
    async with get_session_factory()() as session:
        await session.execute(
            sa.text(
                "UPDATE control.chat_session SET issued_at = now() - interval '2 hours', "
                "expires_at = now() WHERE tenant_id = :tenant_id AND id = :session_id"
            ),
            {"tenant_id": session_scope.tenant_id, "session_id": grant.info.session_id},
        )
        with pytest.raises(AppError) as session_expired:
            await _resolve(session_token, session)
        assert session_expired.value.code == ErrorCode.CHAT_SESSION_EXPIRED
        await session.rollback()
    untouched = (await _session_rows(session_scope.tenant_id))[0]
    assert untouched.expires_at == grant.info.expires_at, "被拒读取不改写会话期限"


async def test_b240_logout_revokes_only_the_presented_session(session_scope: SessionScope) -> None:
    """[B-240] 登出只撤本会话：同一 Token 的其他设备会话与来源 Token 都不受影响。"""
    user_token = await _issue_user_token(session_scope)
    first = await _exchange(user_token)
    second = await _exchange(user_token)
    first_token = first.session_token.get_secret_value()
    second_token = second.session_token.get_secret_value()
    assert first.info.session_id != second.info.session_id and first_token != second_token
    assert len(await _session_rows(session_scope.tenant_id)) == 2, "多设备会话可并存"

    await _logout(first_token)
    rows = {row.id: row for row in await _session_rows(session_scope.tenant_id)}
    revoked_at = rows[first.info.session_id].revoked_at
    assert revoked_at is not None and revoked_at <= datetime.now(UTC) + timedelta(seconds=5)
    assert rows[second.info.session_id].revoked_at is None, "其他设备会话不因此退出"
    assert (await _token_rows(session_scope.tenant_id))[0].revoked_at is None, "登出不撤销用户 Token"

    with pytest.raises(AppError) as revoked:
        await _resolve(first_token)
    assert revoked.value.code == ErrorCode.CHAT_SESSION_REVOKED
    assert (await _resolve(second_token)).session_id == second.info.session_id

    # 重放登出幂等：不改时间戳；未知会话是空操作。
    await _logout(first_token)
    replayed = {row.id: row for row in await _session_rows(session_scope.tenant_id)}
    assert replayed[first.info.session_id].revoked_at == revoked_at
    await _logout("never-issued-session")
    assert len(await _session_rows(session_scope.tenant_id)) == 2


async def test_b240_cookie_expiry_reauthentication_keeps_identity_and_history(
    session_scope: SessionScope,
) -> None:
    """[B-240] Cookie 到期重新认证：同租户同用户、保留既有会话/历史，不删行、不扩权。"""
    user_token = await _issue_user_token(session_scope)
    expired = await _exchange(user_token)
    other_device = await _exchange(user_token)
    expired_token = expired.session_token.get_secret_value()
    other_token = other_device.session_token.get_secret_value()

    async with get_session_factory()() as session:
        await session.execute(
            sa.text(
                "UPDATE control.chat_session SET issued_at = now() - interval '13 hours', "
                "expires_at = now() - interval '1 hour' WHERE id = :session_id"
            ),
            {"session_id": expired.info.session_id},
        )
        await session.commit()
    with pytest.raises(AppError) as cookie_gone:
        await _resolve(expired_token)
    assert cookie_gone.value.code == ErrorCode.CHAT_SESSION_EXPIRED

    renewed = await _exchange(user_token)
    renewed_token = renewed.session_token.get_secret_value()
    assert renewed.info.session_id not in (expired.info.session_id, other_device.info.session_id)
    assert (renewed.info.tenant_id, renewed.info.user_id) == (
        expired.info.tenant_id,
        expired.info.user_id,
    ), "重新认证回到同一 platform_user"
    assert renewed_token not in (expired_token, other_token)

    rows = {row.id: row for row in await _session_rows(session_scope.tenant_id)}
    assert len(rows) == 3, "过期会话行保留为历史，不物理删除"
    assert rows[expired.info.session_id].revoked_at is None
    assert rows[other_device.info.session_id].revoked_at is None, "重新认证不踢掉其他设备"
    assert (await _resolve(other_token)).session_id == other_device.info.session_id

    await _logout(renewed_token)
    rows = {row.id: row for row in await _session_rows(session_scope.tenant_id)}
    assert rows[expired.info.session_id].revoked_at is None, "登出只作用于本会话"
    assert rows[other_device.info.session_id].revoked_at is None
    assert rows[renewed.info.session_id].revoked_at is not None


async def test_b240_chat_session_cannot_authenticate_as_console(
    client: AsyncClient, session_scope: SessionScope
) -> None:
    """[B-240] CHAT 会话不是 Console 凭据：Bearer/Cookie 冒充管理登录一律 401 且不回显。"""
    user_token = await _issue_user_token(session_scope)
    session_token = (await _exchange(user_token)).session_token.get_secret_value()
    digest = hash_chat_token(session_token)

    bearer = {"Authorization": f"Bearer {session_token}"}
    me = await client.get("/api/v1/auth/me", headers=bearer)
    users = await client.get("/api/v1/users", headers=bearer)
    manage = await client.put(
        f"/api/v1/users/{session_scope.user_id}/webchat-token",
        json={"expected_revision": 0},
        headers=bearer,
    )
    client.cookies.set("muad_session", session_token, path="/")
    cookie_me = await client.get("/api/v1/auth/me")

    for response in (me, users, manage, cookie_me):
        assert response.status_code == 401 and response.json()["code"] == "UNAUTHORIZED"
        assert session_token not in response.text and digest not in response.text
    async with get_session_factory()() as session:
        console_sessions = await session.scalar(
            sa.select(sa.func.count())
            .select_from(ConsoleSession)
            .where(ConsoleSession.token_hash == digest)
        )
    assert int(console_sessions or 0) == 0, "CHAT 摘要不得落成 Console 会话"


# --- B-08：期限边界、并发单胜与版本撤销 -------------------------------------------------


@pytest.mark.parametrize(
    "document",
    [
        {"webchat": {"session_ttl_hours": 0}},
        {"webchat": {"session_ttl_hours": 25}},
        {"webchat": {"session_ttl_hours": 1.5}},
        {"webchat": {"access_ttl_days": 0}},
        {"webchat": {"access_ttl_days": 366}},
    ],
)
async def test_b08_out_of_bounds_validity_fails_explicitly(
    session_scope: SessionScope, document: dict[str, Any]
) -> None:
    """[B-08] 有效期必须正整数且有界：越界/非整数设置源显式 503，不静默回落默认值。"""
    user_token = await _issue_user_token(session_scope)
    await _add_settings_revision(session_scope.tenant_id, 1, document)
    with pytest.raises(AppError) as corrupt:
        await _exchange(user_token)
    assert corrupt.value.code == ErrorCode.PLATFORM_SETTINGS_UNAVAILABLE
    assert await _session_rows(session_scope.tenant_id) == [], "失败不得留下半写出会话"
    assert (await _token_rows(session_scope.tenant_id))[0].revoked_at is None
    assert user_token not in str(corrupt.value)


async def test_b08_positive_session_ttl_bounds_are_applied(
    session_scope: SessionScope,
) -> None:
    """[B-08] 边界内的正整数（1 与 24 小时）都按来源生效。"""
    await _add_settings_revision(
        session_scope.tenant_id, 1, {"webchat": {"session_ttl_hours": 1}}
    )
    one_hour = await _exchange(await _issue_user_token(session_scope))
    one_row = {row.id: row for row in await _session_rows(session_scope.tenant_id)}[
        one_hour.info.session_id
    ]
    assert one_hour.idle_ttl_sec == int(timedelta(hours=1).total_seconds())
    assert abs((one_row.expires_at - one_row.issued_at) - timedelta(hours=1)) <= timedelta(seconds=1)

    await _add_settings_revision(
        session_scope.tenant_id, 2, {"webchat": {"session_ttl_hours": 24}}
    )
    day_long = await _exchange(
        await _issue_user_token(session_scope, user_id=session_scope.other_user_id)
    )
    day_row = {row.id: row for row in await _session_rows(session_scope.tenant_id)}[
        day_long.info.session_id
    ]
    assert day_long.idle_ttl_sec == int(timedelta(hours=24).total_seconds())
    assert abs((day_row.expires_at - day_row.issued_at) - timedelta(hours=24)) <= timedelta(seconds=1)


async def test_b08_concurrent_reset_has_a_single_winner_and_replay_never_rotates(
    session_scope: SessionScope,
) -> None:
    """[B-08] 并发重置仅一个 `expected_revision` 胜；响应丢失重放不轮换、不回放明文。"""
    first_token = await _issue_user_token(session_scope)
    bound = await _exchange(first_token)  # 旧版本 Token 的活跃会话，重置必须同事务撤销

    factory = get_session_factory()
    sessions = [factory(), factory()]

    async def attempt(session: AsyncSession) -> str:
        issued = await ChatTokenService(session).issue_token(
            tenant_id=session_scope.tenant_id,
            user_id=session_scope.user_id,
            expected_revision=1,
            actor=_actor(),
        )
        await session.commit()
        return issued.token.get_secret_value()

    try:
        results = await asyncio.wait_for(
            asyncio.gather(*(attempt(session) for session in sessions), return_exceptions=True),
            timeout=15,
        )
    finally:
        for session in sessions:
            await session.close()

    winners = [result for result in results if isinstance(result, str)]
    losers = [result for result in results if isinstance(result, AppError)]
    assert len(winners) == 1 and len(losers) == 1
    assert losers[0].code == ErrorCode.REVISION_CONFLICT
    assert winners[0] not in str(losers[0]), "并发落败的响应不得回放胜者明文"

    rows = await _token_rows(session_scope.tenant_id)
    assert [row.revision for row in rows] == [1, 2], "并发只产生一个新版本"
    assert sum(1 for row in rows if row.revoked_at is None) == 1, "每用户至多一个未撤销 Token"
    assert rows[1].token_hash == hash_chat_token(winners[0])
    assert first_token not in _row_dump(rows[1])

    bound_row = {row.id: row for row in await _session_rows(session_scope.tenant_id)}[bound.info.session_id]
    assert bound_row.revoked_at == rows[0].revoked_at, "重置同事务撤销旧版本的关联会话"

    # 响应丢失后的重放：同一 expected_revision 再次 PUT 只得到冲突，不再次轮换、不回放明文。
    with pytest.raises(AppError) as replayed:
        async with factory() as session:
            await ChatTokenService(session).issue_token(
                tenant_id=session_scope.tenant_id,
                user_id=session_scope.user_id,
                expected_revision=1,
                actor=_actor(),
            )
    assert replayed.value.code == ErrorCode.REVISION_CONFLICT
    assert winners[0] not in str(replayed.value)
    assert [row.revision for row in await _token_rows(session_scope.tenant_id)] == [1, 2]

    # 摘要唯一：同租户另一用户的 Token 摘要与本用户任何版本都不相同。
    other_token = await _issue_user_token(session_scope, user_id=session_scope.other_user_id)
    all_rows = await _token_rows(session_scope.tenant_id)
    digests = [row.token_hash for row in all_rows]
    assert len(set(digests)) == len(digests), "摘要全局唯一"
    assert hash_chat_token(other_token) not in [
        row.token_hash for row in all_rows if row.platform_user_id == session_scope.user_id
    ]
    assert sum(
        1
        for row in all_rows
        if row.platform_user_id == session_scope.other_user_id and row.revoked_at is None
    ) == 1, "每用户至多一个未撤销 Token"

    with pytest.raises(AppError) as revoked_old:
        await _exchange(first_token)
    assert revoked_old.value.code == ErrorCode.CHAT_TOKEN_REVOKED


async def test_b08_exchange_serializes_on_the_user_row_lock(session_scope: SessionScope) -> None:
    """[B-08] 交换与重置共用"用户→Token"锁序：持用户行锁时交换先等锁，等待期间不落会话。"""
    user_token = await _issue_user_token(session_scope)
    factory = get_session_factory()
    holder = factory()
    waiter = factory()
    pending: asyncio.Task[ChatSessionGrant] | None = None
    try:
        await holder.execute(
            text("SELECT id FROM control.platform_user WHERE id = :user_id FOR UPDATE"),
            {"user_id": session_scope.user_id},
        )
        holder_pid = int(await holder.scalar(text("SELECT pg_backend_pid()")))
        waiter_pid = int(await waiter.scalar(text("SELECT pg_backend_pid()")))
        pending = asyncio.create_task(ChatSessionService(waiter).exchange(SecretStr(user_token)))
        assert await _wait_until(
            lambda: _blocked_by(waiter_pid, holder_pid)
        ), "交换必须等待平台用户行锁（用户→Token 锁序的第一把锁）"
        assert pending.done() is False
        assert await _session_rows(session_scope.tenant_id) == [], "被锁阻塞时不得落会话行"

        await holder.commit()
        grant = await asyncio.wait_for(pending, timeout=5.0)
        await waiter.commit()
    finally:
        if pending is not None and not pending.done():
            pending.cancel()
        await holder.close()
        await waiter.close()

    assert (await _session_rows(session_scope.tenant_id))[0].id == grant.info.session_id


async def test_b08_late_revoke_replay_spares_later_token_and_its_session(
    session_scope: SessionScope,
) -> None:
    """[B-08] 旧版本撤销重放不影响后续新 Token 及其活跃会话。"""
    first_token = await _issue_user_token(session_scope)
    async with get_session_factory()() as session:
        revoked = await ChatTokenService(session).revoke_token(
            tenant_id=session_scope.tenant_id,
            user_id=session_scope.user_id,
            expected_revision=1,
            actor=_actor(),
        )
        await session.commit()
    assert (revoked.status, revoked.revision) == ("REVOKED", 1)

    async with get_session_factory()() as session:
        reissued = await ChatTokenService(session).issue_token(
            tenant_id=session_scope.tenant_id,
            user_id=session_scope.user_id,
            expected_revision=1,
            actor=_actor(),
        )
        await session.commit()
        second_token = reissued.token.get_secret_value()
    assert reissued.revision == 2

    live = await _exchange(second_token)
    async with get_session_factory()() as session:
        late = await ChatTokenService(session).revoke_token(
            tenant_id=session_scope.tenant_id,
            user_id=session_scope.user_id,
            expected_revision=1,
            actor=_actor(),
        )
        await session.commit()
    assert (late.status, late.revision, late.token_id) == ("ACTIVE", 2, reissued.token_id)

    rows = await _token_rows(session_scope.tenant_id)
    assert [row.revoked_at is not None for row in rows] == [True, False]
    assert rows[1].token_hash == hash_chat_token(second_token)
    session_row = (await _session_rows(session_scope.tenant_id))[0]
    assert session_row.revoked_at is None, "旧版本迟到的撤销不得带走新版本的会话"
    assert (await _resolve(live.session_token.get_secret_value())).session_id == live.info.session_id
    assert first_token not in _row_dump(rows[1])
