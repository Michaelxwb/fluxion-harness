"""MSSWWEB 验收种子：在既有 IM 验收栈的租户下补一份「外部身份 + 入口授权」。

为什么不走管理面 HTTP 造数据：`seed_console_admin` 依赖 `uv`，而 CI/远程验收环境里没有；
这里的种子与 `tests/e2e/seed_im_gateway.py::_seed` 同口径（直接写控制库），只补 MSSWWEB
需要的三类行。**写操作本身**（授权/撤权/编辑身份）仍走真实管理面 API —— 那正是要验证的
失效链路（E-05 / E-10），不能在种子里绕过。

租户、Agent、平台用户沿用 `seed_control` 的产物：验收关注的是「同一个平台用户在两个通道
看到同一批数据」（S-11 / E-04），新造一套租户反而失去这个前提。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from muad_common import SharedSettings
from muad_console_platform.application.auth_service import hash_password
from muad_console_platform.application.external_assertion.principal import compute_identity_hash
from muad_console_platform.infrastructure.models.auth import ROLE_ADMIN, ConsoleAccount
from muad_console_platform.infrastructure.models.channel import (
    BotAccount,
    EntryAccessGrant,
    ExternalIdentity,
)
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.e2e.mssw_token_factory import MsswPlatform

MSSWWEB_BOT_ID = "e2e-mssw-entry"
ADMIN_USERNAME = "e2e-mssw-admin"
ADMIN_PASSWORD = "e2e-mssw-admin-password"


@dataclass(frozen=True, slots=True)
class MsswSeed:
    """MSSWWEB 侧的行 id 与凭据；用例按名字取，不按位置取。"""

    tenant_id: str
    agent_id: uuid.UUID
    platform_user_id: uuid.UUID
    entry_id: uuid.UUID
    identity_id: uuid.UUID
    grant_id: uuid.UUID
    expert_id: str
    platform_tenant_id: str
    admin_username: str
    admin_password: str

    def identity(self) -> dict[str, str]:
        return {"expert_id": self.expert_id, "tenant_id": self.platform_tenant_id}


def seed_msswweb(
    *,
    platform: MsswPlatform,
    tenant_id: str,
    agent_id: uuid.UUID,
    platform_user_id: uuid.UUID,
    bot_id: str = MSSWWEB_BOT_ID,
    expert_id: str | None = None,
    platform_tenant_id: str = "1001",
) -> MsswSeed:
    """写入 MSSWWEB 入口 + 用户级外部身份 + 入口授权 + 一个可登录管理员。

    幂等前提：调用方保证这是**同一个新租户**（验收栈启动时 seed 一次）。重复调用会撞
    `uq_external_identity_tenant_platform_hash`，这是期望的（防止用例间悄悄共享行）。
    """
    expert_id = expert_id or uuid.uuid4().hex[:24]
    identity = {"expert_id": expert_id, "tenant_id": platform_tenant_id}
    identity_hash = compute_identity_hash(identity)

    async def run() -> MsswSeed:
        engine = create_async_engine(SharedSettings().require_database_url())
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with session_factory() as session:
                async with session.begin():
                    entry = BotAccount(
                        id=uuid.uuid4(),
                        tenant_id=tenant_id,
                        channel="MSSWWEB",
                        name="MSSW E2E Entry",
                        bot_id=bot_id,
                        secret=None,  # SECRETLESS_CHANNELS：该通道无 Secret
                        agent_id=agent_id,
                        enabled=True,
                        config_json={},
                    )
                    session.add(entry)
                    await session.flush()

                    identity_row = ExternalIdentity(
                        id=uuid.uuid4(),
                        tenant_id=tenant_id,
                        platform_key=platform.platform_key,
                        platform_user_id=platform_user_id,
                        identity_hash=identity_hash,
                        identity_json=identity,
                        status="ACTIVE",
                    )
                    session.add(identity_row)
                    await session.flush()

                    grant = EntryAccessGrant(
                        id=uuid.uuid4(),
                        tenant_id=tenant_id,
                        external_identity_id=identity_row.id,
                        bot_account_id=entry.id,
                    )
                    session.add(grant)
                    await session.flush()

                    await _ensure_admin(session, tenant_id)

                    return MsswSeed(
                        tenant_id=tenant_id,
                        agent_id=agent_id,
                        platform_user_id=platform_user_id,
                        entry_id=entry.id,
                        identity_id=identity_row.id,
                        grant_id=grant.id,
                        expert_id=expert_id,
                        platform_tenant_id=platform_tenant_id,
                        admin_username=ADMIN_USERNAME,
                        admin_password=ADMIN_PASSWORD,
                    )
        finally:
            await engine.dispose()

    return _run_in_thread(run)


async def _ensure_admin(session: Any, tenant_id: str) -> None:
    """建一个可登录的管理员（管理面写操作要用真实登录拿 CSRF）。"""
    existing = await session.scalar(
        ConsoleAccount.__table__.select().where(
            ConsoleAccount.tenant_id == tenant_id,
            ConsoleAccount.username == ADMIN_USERNAME,
            ConsoleAccount.is_deleted.is_(False),
        )
    )
    if existing is not None:
        return
    session.add(
        ConsoleAccount(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            username=ADMIN_USERNAME,
            display_name="MSSW E2E Admin",
            password_hash=hash_password(ADMIN_PASSWORD),
            role=ROLE_ADMIN,
            enabled=True,
        )
    )


def _run_in_thread(coro_fn: Any) -> Any:
    """种子跑在自己的事件循环里（验收栈存在多个 loop，不能复用）。"""
    import asyncio
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro_fn()).result()
