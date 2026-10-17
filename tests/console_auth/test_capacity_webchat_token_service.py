"""[B-238] 用户 Webchat Token 签发、重置与版本撤销（design §3.3 / §3.4 API-03 / §3.7）。

真实边界（业务路径不 mock）：

- **真实 PostgreSQL**：`control.chat_access_token` / `control.chat_session` 的 partial unique
  与"只存摘要"约束、`SELECT … FOR UPDATE` 的真实行锁、真实事务（服务只 flush，提交由调用方
  决定——本文件用第二个连接做"未提交不可见"的旁观者证据）；
- **真实 Console 应用服务**：直接驱动 `ChatTokenService`，无替身、无 HTTP 替身；
- **真实设置源**：TTL 只来自 `control.platform_setting`（无版本行 = schema 默认 30 天），
  损坏文档显式失败、不静默回落默认值；
- 明文卫生以真实回读的 `control.chat_access_token` 行与 `config_audit_log` 行为证据：
  原文只出现在签发响应对象里一次，库里只有 SHA-256 摘要。

清库隔离：`tests/console_auth/conftest.py` 的租户清理不含本任务的新表；本文件自带按 `tenant_id`
的清理（会话→Token→用户→设置→审计，顺序满足外键），tenant 每用例唯一。
"""

from __future__ import annotations

import asyncio
import hashlib
import secrets
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_console_platform.application.audit_service import AuditActor
from muad_console_platform.application.chat_token_service import (
    ChatTokenService,
    generate_chat_token,
    hash_chat_token,
)
from muad_console_platform.infrastructure.db import get_session_factory
from muad_console_platform.infrastructure.models.auth import ChatAccessToken, ChatSession
from muad_console_platform.infrastructure.models.control import (
    ConfigAuditLog,
    PlatformSetting,
    PlatformUser,
)
from muad_contracts.webchat import WebchatTokenIssued, WebchatTokenMetadata
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

AUDIT_RESOURCE_TYPE = "CHAT_ACCESS_TOKEN"
DEFAULT_TTL = timedelta(days=30)
#: 256 位熵的 url-safe base64 至少 43 个字符（43 × 6 = 258 位 ≥ 256 位）。
MIN_TOKEN_CHARS = 43
LOCK_WAIT_TIMEOUT_SEC = 5.0
POLL_INTERVAL_SEC = 0.02


@dataclass(frozen=True)
class TokenContext:
    tenant_id: str
    user_id: uuid.UUID
    other_user_id: uuid.UUID
    foreign_tenant_id: str
    foreign_user_id: uuid.UUID


def _unique_tenant(prefix: str = "tok") -> str:
    return f"{prefix}-{uuid.uuid4()}"


def _actor() -> AuditActor:
    return AuditActor(account_id=uuid.uuid4(), source_ip="203.0.113.7")


async def _purge_tenant(tenant_id: str) -> None:
    """删本租户的会话/Token/用户/设置/审计行：顺序满足外键，不触碰其他租户。"""
    async with get_session_factory()() as session:
        for statement in (
            "DELETE FROM control.chat_session WHERE tenant_id = :tenant_id",
            "DELETE FROM control.chat_access_token WHERE tenant_id = :tenant_id",
            "DELETE FROM control.platform_user WHERE tenant_id = :tenant_id",
            "DELETE FROM control.platform_setting WHERE tenant_id = :tenant_id",
            "DELETE FROM control.config_audit_log WHERE tenant_id = :tenant_id",
        ):
            await session.execute(text(statement), {"tenant_id": tenant_id})
        await session.commit()


@pytest.fixture
async def token_tenant(database_guard: None) -> AsyncIterator[TokenContext]:
    """两个租户、各一个平台用户：跨租户/跨用户都不串行；结束清理本任务新表行。"""
    tenant_id = _unique_tenant()
    foreign_tenant_id = _unique_tenant("tok-foreign")
    factory = get_session_factory()
    async with factory() as session:
        users = [
            PlatformUser(tenant_id=tenant_id, user_code=f"u-{uuid.uuid4().hex[:8]}", display_name="Owner"),
            PlatformUser(tenant_id=tenant_id, user_code=f"u-{uuid.uuid4().hex[:8]}", display_name="Other"),
            PlatformUser(
                tenant_id=foreign_tenant_id,
                user_code=f"u-{uuid.uuid4().hex[:8]}",
                display_name="Foreign",
            ),
        ]
        session.add_all(users)
        await session.commit()
        context = TokenContext(
            tenant_id=tenant_id,
            user_id=users[0].id,
            other_user_id=users[1].id,
            foreign_tenant_id=foreign_tenant_id,
            foreign_user_id=users[2].id,
        )
    try:
        yield context
    finally:
        await _purge_tenant(tenant_id)
        await _purge_tenant(foreign_tenant_id)


async def _issue(
    context: TokenContext,
    *,
    expected_revision: int,
    user_id: uuid.UUID | None = None,
    tenant_id: str | None = None,
) -> WebchatTokenIssued:
    async with get_session_factory()() as session:
        issued = await ChatTokenService(session).issue_token(
            tenant_id=tenant_id or context.tenant_id,
            user_id=user_id or context.user_id,
            expected_revision=expected_revision,
            actor=_actor(),
        )
        await session.commit()
        return issued


async def _metadata(
    context: TokenContext, *, user_id: uuid.UUID | None = None, tenant_id: str | None = None
) -> WebchatTokenMetadata:
    async with get_session_factory()() as session:
        return await ChatTokenService(session).read_metadata(
            tenant_id or context.tenant_id, user_id or context.user_id
        )


async def _revoke(
    context: TokenContext, *, expected_revision: int, user_id: uuid.UUID | None = None
) -> WebchatTokenMetadata:
    async with get_session_factory()() as session:
        metadata = await ChatTokenService(session).revoke_token(
            tenant_id=context.tenant_id,
            user_id=user_id or context.user_id,
            expected_revision=expected_revision,
            actor=_actor(),
        )
        await session.commit()
        return metadata


async def _token_rows(tenant_id: str) -> list[ChatAccessToken]:
    async with get_session_factory()() as session:
        rows = await session.scalars(
            select(ChatAccessToken)
            .where(ChatAccessToken.tenant_id == tenant_id)
            .order_by(ChatAccessToken.revision)
        )
        return list(rows)


def _row_dump(row: ChatAccessToken) -> dict[str, Any]:
    """整行列值的文本视图：用于断言"库里任何列都不含 Token 原文"。"""
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


async def _audit_rows(tenant_id: str) -> list[ConfigAuditLog]:
    async with get_session_factory()() as session:
        rows = await session.scalars(
            select(ConfigAuditLog)
            .where(
                ConfigAuditLog.tenant_id == tenant_id,
                ConfigAuditLog.resource_type == AUDIT_RESOURCE_TYPE,
            )
            .order_by(ConfigAuditLog.action.asc())
        )
        return list(rows)


async def _add_chat_session(
    context: TokenContext,
    *,
    access_token_id: uuid.UUID,
    issued_at: datetime,
    expires_at: datetime,
    revoked_at: datetime | None = None,
) -> uuid.UUID:
    """直接落会话行（TASK-040 的交换路径尚不存在）；只用于验证撤销的关联面。"""
    async with get_session_factory()() as session:
        row = ChatSession(
            tenant_id=context.tenant_id,
            access_token_id=access_token_id,
            platform_user_id=context.user_id,
            token_hash=hashlib.sha256(secrets.token_urlsafe(32).encode()).hexdigest(),
            issued_at=issued_at,
            expires_at=expires_at,
            revoked_at=revoked_at,
        )
        session.add(row)
        await session.commit()
        return row.id


async def _session_rows(session_ids: list[uuid.UUID]) -> dict[uuid.UUID, ChatSession]:
    async with get_session_factory()() as session:
        rows = await session.scalars(
            select(ChatSession).where(ChatSession.id.in_(session_ids)).order_by(ChatSession.create_time)
        )
        return {row.id: row for row in rows}


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


async def _force_expired(tenant_id: str, revision: int) -> None:
    """把签发时间与到期时间同时挪到过去，构造"已过期但未撤销"的真实行。"""
    async with get_session_factory()() as session:
        await session.execute(
            sa.update(ChatAccessToken)
            .where(
                ChatAccessToken.tenant_id == tenant_id,
                ChatAccessToken.revision == revision,
            )
            .values(
                issued_at=sa.text("now() - interval '2 hours'"),
                expires_at=sa.text("now() - interval '1 hour'"),
            )
        )
        await session.commit()


async def _wait_until(
    predicate: Callable[[], Awaitable[bool]], *, timeout: float = LOCK_WAIT_TIMEOUT_SEC
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if await predicate():
            return True
        await asyncio.sleep(POLL_INTERVAL_SEC)
    return False


async def _blame_blockers(pid: int) -> list[int]:
    """`pg_blocking_pids`：谁在挡这个后端（未获锁的等待者）。"""
    async with get_session_factory()() as session:
        rows = await session.scalars(text("SELECT unnest(pg_blocking_pids(:pid))"), {"pid": pid})
        return [int(row) for row in rows]


async def _relation_locks(pid: int, relation: str) -> int:
    async with get_session_factory()() as session:
        total = await session.scalar(
            text("SELECT count(*) FROM pg_locks WHERE pid = :pid AND relation = CAST(:relation AS regclass)"),
            {"pid": pid, "relation": relation},
        )
        return int(total or 0)


async def _is_blocked_by(waiter_pid: int, holder_pid: int) -> bool:
    return holder_pid in await _blame_blockers(waiter_pid)


async def test_b238_first_issue_uses_secure_random_and_stores_only_the_digest(
    token_tenant: TokenContext,
) -> None:
    """[B-238] 首签：≥256 位安全随机、库里只有摘要、原文只在签发响应出现一次。"""
    issued = await _issue(token_tenant, expected_revision=0)
    plaintext = issued.to_issuance_payload()["token"]

    assert isinstance(issued, WebchatTokenIssued) and issued.status == "ACTIVE"
    assert len(plaintext) >= MIN_TOKEN_CHARS, "签发值必须承载至少 256 位熵"
    assert plaintext not in repr(issued), "原文不得进入对象表示（SecretStr）"
    assert "token" not in issued.model_dump(), "元数据投影不得含原文"

    rows = await _token_rows(token_tenant.tenant_id)
    assert len(rows) == 1
    row = rows[0]
    assert row.token_hash == hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
    assert row.token_hash == hash_chat_token(plaintext)
    assert plaintext not in str(_row_dump(row)), "库里任何列都不得含原文"
    assert (row.revision, row.revoked_at) == (1, None)
    assert row.platform_user_id == token_tenant.user_id
    assert row.expires_at - row.issued_at == DEFAULT_TTL, "无设置版本时用 schema 默认 30 天"

    metadata = await _metadata(token_tenant)
    assert (metadata.configured, metadata.status, metadata.revision) == (True, "ACTIVE", 1)
    assert metadata.token_id == row.id
    assert metadata.issued_at == row.issued_at and metadata.expires_at == row.expires_at

    audits = await _audit_rows(token_tenant.tenant_id)
    assert [audit.action for audit in audits] == ["CREATE"]
    assert audits[0].after_json["revision"] == 1
    assert plaintext not in f"{audits[0].before_json}{audits[0].after_json}", "审计不得含 Token 原文"
    assert row.token_hash not in str(audits[0].after_json), "摘要也不进入审计快照"


def test_b238_generator_is_random_and_digest_matches_database_shape() -> None:
    """[B-238] 生成器：多次签发互不相同，摘要恒为 64 位小写十六进制。"""
    tokens = {generate_chat_token() for _ in range(64)}
    assert len(tokens) == 64
    assert all(len(token) >= MIN_TOKEN_CHARS for token in tokens)
    digests = {hash_chat_token(token) for token in tokens}
    assert len(digests) == 64
    assert all(len(digest) == 64 and digest == digest.lower() for digest in digests)
    assert all(set(digest) <= set("0123456789abcdef") for digest in digests)


async def test_b238_reset_requires_matching_expected_revision_and_shows_plaintext_once(
    token_tenant: TokenContext,
) -> None:
    """[B-238] PUT：expected_revision 单胜；重置换新身份、旧行保留为版本基准。"""
    first = await _issue(token_tenant, expected_revision=0)
    first_plaintext = first.to_issuance_payload()["token"]

    with pytest.raises(AppError) as stale_zero:
        await _issue(token_tenant, expected_revision=0)
    assert stale_zero.value.code == ErrorCode.REVISION_CONFLICT
    assert [row.revision for row in await _token_rows(token_tenant.tenant_id)] == [1], "冲突不得新签"

    second = await _issue(token_tenant, expected_revision=1)
    assert second.revision == 2 and second.token_id != first.token_id
    assert second.to_issuance_payload()["token"] != first_plaintext
    assert second.status == "ACTIVE" and second.expires_at - second.issued_at == DEFAULT_TTL

    with pytest.raises(AppError) as replayed:
        await _issue(token_tenant, expected_revision=1)
    assert replayed.value.code == ErrorCode.REVISION_CONFLICT, "响应丢失后的重放不得再次轮换"

    rows = await _token_rows(token_tenant.tenant_id)
    assert [row.revision for row in rows] == [1, 2], "旧行保留、不物理删除"
    assert rows[0].revoked_at is not None and rows[1].revoked_at is None
    assert sum(1 for row in rows if row.revoked_at is None) == 1, "每用户至多一个未撤销 Token"
    assert rows[0].token_hash == hash_chat_token(first_plaintext)
    assert rows[1].token_hash == hash_chat_token(second.to_issuance_payload()["token"])
    assert first_plaintext not in str(_row_dump(rows[1]))

    audits = await _audit_rows(token_tenant.tenant_id)
    assert sorted(audit.action for audit in audits) == ["CREATE", "CREATE", "UPDATE"]
    assert {audit.resource_id for audit in audits} == {first.token_id, second.token_id}
    assert first_plaintext not in f"{audits[0].before_json}{audits[0].after_json}"
    assert second.to_issuance_payload()["token"] not in str(
        [audit.after_json for audit in audits]
    ), "审计快照不含任何版本的原文"


async def test_b238_reset_revokes_source_sessions_in_the_same_transaction(
    token_tenant: TokenContext,
) -> None:
    """[B-238] 重置在同一事务撤销旧 Token 及其全部活跃会话；旁观连接在提交前看不到。"""
    issued = await _issue(token_tenant, expected_revision=0)
    assert issued.token_id is not None
    now = datetime.now(UTC)
    stale_revoked_at = now - timedelta(minutes=5)
    live_sessions = [
        await _add_chat_session(
            token_tenant,
            access_token_id=issued.token_id,
            issued_at=now,
            expires_at=now + timedelta(hours=12),
        )
        for _ in range(2)
    ]
    already_revoked = await _add_chat_session(
        token_tenant,
        access_token_id=issued.token_id,
        issued_at=now - timedelta(hours=1),
        expires_at=now + timedelta(hours=11),
        revoked_at=stale_revoked_at,
    )
    foreign_session = await _add_chat_session(
        token_tenant,
        access_token_id=issued.token_id,
        issued_at=now,
        expires_at=now + timedelta(hours=12),
        revoked_at=now - timedelta(seconds=1),
    )

    factory = get_session_factory()
    async with factory() as session:
        reset = await ChatTokenService(session).issue_token(
            tenant_id=token_tenant.tenant_id,
            user_id=token_tenant.user_id,
            expected_revision=1,
            actor=_actor(),
        )
        assert reset.revision == 2
        # 同一事务内已可见：两条活跃会话共享同一撤销时刻
        within = await _session_rows(live_sessions)
        assert len({within[session_id].revoked_at for session_id in live_sessions}) == 1
        # 旁观连接（另一事务）仍看不到提交结果 ⇒ 撤销与签发确在同一未提交事务里
        witness_rows = await _token_rows(token_tenant.tenant_id)
        assert [(row.revision, row.revoked_at) for row in witness_rows] == [(1, None)]
        await session.commit()

    rows = await _token_rows(token_tenant.tenant_id)
    assert [(row.revision, row.revoked_at is None) for row in rows] == [(1, False), (2, True)]
    old_token_revoked_at = rows[0].revoked_at
    assert old_token_revoked_at is not None
    sessions = await _session_rows([*live_sessions, already_revoked, foreign_session])
    assert {sessions[session_id].revoked_at for session_id in live_sessions} == {old_token_revoked_at}, (
        "关联会话与来源 Token 同一事务、同一时刻撤销"
    )
    assert sessions[already_revoked].revoked_at == stale_revoked_at, "先前已撤销的会话不被改写"
    assert sessions[foreign_session].revoked_at == now - timedelta(seconds=1), "与本 Token 无关的会话不被触碰"


async def test_b238_delete_is_version_scoped_and_replay_spares_later_tokens(
    token_tenant: TokenContext,
) -> None:
    """[B-238] DELETE：按指定版本幂等撤销；旧版本重放不撤销后续新 Token。"""
    first = await _issue(token_tenant, expected_revision=0)
    assert first.token_id is not None
    now = datetime.now(UTC)
    source_sessions = [
        await _add_chat_session(
            token_tenant,
            access_token_id=first.token_id,
            issued_at=now,
            expires_at=now + timedelta(hours=12),
        )
    ]

    revoked = await _revoke(token_tenant, expected_revision=1)
    assert (revoked.configured, revoked.status, revoked.revision) == (True, "REVOKED", 1)
    rows = await _token_rows(token_tenant.tenant_id)
    assert [row.revoked_at is not None for row in rows] == [True]
    sessions = await _session_rows(source_sessions)
    assert sessions[source_sessions[0]].revoked_at == rows[0].revoked_at, "撤销 Token 时同事务撤销关联会话"

    # 重放同一版本：幂等无操作，不再改时间戳、不报错
    replay = await _revoke(token_tenant, expected_revision=1)
    assert replay.status == "REVOKED"
    assert (await _token_rows(token_tenant.tenant_id))[0].revoked_at == rows[0].revoked_at

    # 撤销后按基准版本重签：新身份；迟到的旧版本 DELETE 不得撤销新 Token
    second = await _issue(token_tenant, expected_revision=1)
    assert second.revision == 2 and second.token_id != first.token_id
    late = await _revoke(token_tenant, expected_revision=1)
    assert (late.status, late.revision) == ("ACTIVE", 2), "旧版本重放不误伤新签发行"
    latest = [row for row in await _token_rows(token_tenant.tenant_id) if row.revision == 2]
    assert len(latest) == 1 and latest[0].revoked_at is None
    assert latest[0].token_hash == hash_chat_token(second.to_issuance_payload()["token"])

    # 未配置用户与版本 0 都是幂等空操作
    fresh = await _revoke(token_tenant, expected_revision=0, user_id=token_tenant.other_user_id)
    assert (fresh.configured, fresh.status) == (False, "NOT_CONFIGURED")
    after_zero = await _revoke(token_tenant, expected_revision=0)
    assert after_zero.revision == 2, "版本 0 的撤销不得影响既有版本"


async def test_b238_issue_serializes_on_platform_user_row_lock(token_tenant: TokenContext) -> None:
    """[B-238] 用户→Token 固定锁序：签发先锁用户行；在拿到用户行前不碰 Token 表。"""
    factory = get_session_factory()
    holder = factory()
    waiter = factory()
    pending: asyncio.Task[WebchatTokenIssued] | None = None
    try:
        await holder.execute(
            text("SELECT id FROM control.platform_user WHERE id = :user_id FOR UPDATE"),
            {"user_id": token_tenant.user_id},
        )
        holder_pid = int(await holder.scalar(text("SELECT pg_backend_pid()")))
        waiter_pid = int(await waiter.scalar(text("SELECT pg_backend_pid()")))
        pending = asyncio.create_task(
            ChatTokenService(waiter).issue_token(
                tenant_id=token_tenant.tenant_id,
                user_id=token_tenant.user_id,
                expected_revision=0,
                actor=_actor(),
            )
        )
        blocked = await _wait_until(lambda: _is_blocked_by(waiter_pid, holder_pid))
        assert blocked, "签发必须等待平台用户行锁（用户→Token 锁序的第一把锁）"
        assert await _relation_locks(waiter_pid, "control.chat_access_token") == 0, (
            "等待用户行期间不得触碰 Token 表"
        )
        assert await _token_rows(token_tenant.tenant_id) == [], "被锁阻塞时不得落 Token 行"
        assert pending.done() is False

        await holder.commit()
        issued = await asyncio.wait_for(pending, timeout=LOCK_WAIT_TIMEOUT_SEC)
        await waiter.commit()
    finally:
        if pending is not None and not pending.done():
            pending.cancel()
        await holder.close()
        await waiter.close()

    assert issued.revision == 1
    rows = await _token_rows(token_tenant.tenant_id)
    assert [row.revision for row in rows] == [1]


async def test_b238_concurrent_first_issue_has_a_single_winner(token_tenant: TokenContext) -> None:
    """[B-238] 并发首签（expected_revision=0）：真实行锁串行 ⇒ 恰好一个成功。"""
    factory = get_session_factory()
    first_session = factory()
    second_session = factory()

    async def attempt(session: AsyncSession) -> WebchatTokenIssued:
        issued = await ChatTokenService(session).issue_token(
            tenant_id=token_tenant.tenant_id,
            user_id=token_tenant.user_id,
            expected_revision=0,
            actor=_actor(),
        )
        await session.commit()
        return issued

    try:
        first = asyncio.create_task(attempt(first_session))
        second = asyncio.create_task(attempt(second_session))
        results = await asyncio.wait_for(
            asyncio.gather(first, second, return_exceptions=True), timeout=15
        )
    finally:
        await first_session.close()
        await second_session.close()

    winners = [result for result in results if isinstance(result, WebchatTokenIssued)]
    losers = [result for result in results if isinstance(result, AppError)]
    assert len(winners) == 1 and len(losers) == 1
    assert losers[0].code == ErrorCode.REVISION_CONFLICT
    rows = await _token_rows(token_tenant.tenant_id)
    assert [row.revision for row in rows] == [1]
    assert rows[0].token_hash == hash_chat_token(winners[0].to_issuance_payload()["token"])


async def test_b238_ttl_follows_settings_source_and_already_issued_rows_do_not_drift(
    token_tenant: TokenContext,
) -> None:
    """[B-238] 期限唯一来源是设置版本：改设置只影响后续签发，已签发行不漂移。"""
    await _add_settings_revision(token_tenant.tenant_id, 1, {"webchat": {"access_ttl_days": 15}})
    first = await _issue(token_tenant, expected_revision=0)
    assert first.expires_at - first.issued_at == timedelta(days=15)

    await _add_settings_revision(token_tenant.tenant_id, 2, {"webchat": {"access_ttl_days": 3}})
    metadata = await _metadata(token_tenant)
    assert metadata.expires_at == first.expires_at, "已签发行的到期时间不随设置漂移"

    second = await _issue(token_tenant, expected_revision=1)
    assert second.expires_at - second.issued_at == timedelta(days=3)


async def test_b238_corrupt_settings_block_issuance_but_not_revocation(
    token_tenant: TokenContext,
) -> None:
    """[B-238] 设置源损坏：签发显式失败（503 语义）且不静默用默认值；撤销路径仍然可用。"""
    issued = await _issue(token_tenant, expected_revision=0)
    await _add_settings_revision(token_tenant.tenant_id, 1, {"webchat": {"access_ttl_days": 0}})

    with pytest.raises(AppError) as failure:
        await _issue(token_tenant, expected_revision=1)
    assert failure.value.code == ErrorCode.PLATFORM_SETTINGS_UNAVAILABLE
    rows = await _token_rows(token_tenant.tenant_id)
    assert [row.revision for row in rows] == [1], "失败不得留下半写出 Token"
    assert rows[0].revoked_at is None, "签发失败不得先撤销旧行"
    assert len(await _audit_rows(token_tenant.tenant_id)) == 1, "失败不产生审计行"

    revoked = await _revoke(token_tenant, expected_revision=1)
    assert (revoked.status, revoked.token_id) == ("REVOKED", issued.token_id), (
        "损坏的设置源不得挡住撤销路径"
    )


async def test_b238_metadata_status_and_user_scope(token_tenant: TokenContext) -> None:
    """[B-238] 元信息：未配置/到期/已撤销三态，跨租户与跨用户都不串读。"""
    fresh = await _metadata(token_tenant)
    assert fresh.model_dump() == {
        "configured": False,
        "status": "NOT_CONFIGURED",
        "token_id": None,
        "revision": 0,
        "issued_at": None,
        "expires_at": None,
    }

    issued = await _issue(token_tenant, expected_revision=0)
    await _force_expired(token_tenant.tenant_id, 1)
    expired = await _metadata(token_tenant)
    assert (expired.configured, expired.status, expired.revision) == (True, "EXPIRED", 1)

    revoked = await _revoke(token_tenant, expected_revision=1)
    assert (revoked.status, revoked.token_id) == ("REVOKED", issued.token_id)

    assert (await _metadata(token_tenant, user_id=token_tenant.other_user_id)).status == "NOT_CONFIGURED"
    assert (
        await _metadata(token_tenant, tenant_id=token_tenant.foreign_tenant_id)
    ).status == "NOT_CONFIGURED", "其他租户的用户 ID 不得读回本租户 Token"
    assert (await _metadata(token_tenant, user_id=token_tenant.foreign_user_id)).status == "NOT_CONFIGURED"


async def test_b238_issue_for_unknown_or_foreign_user_is_not_found(token_tenant: TokenContext) -> None:
    """[B-238] 归属核验：不存在/跨租户的用户不签发，且不写任何行。"""
    for user_id in (uuid.uuid4(), token_tenant.foreign_user_id):
        with pytest.raises(AppError) as failure:
            await _issue(token_tenant, expected_revision=0, user_id=user_id)
        assert failure.value.code == ErrorCode.COMMON_NOT_FOUND
    assert await _token_rows(token_tenant.tenant_id) == []
    assert await _token_rows(token_tenant.foreign_tenant_id) == []
    assert await _audit_rows(token_tenant.tenant_id) == []
