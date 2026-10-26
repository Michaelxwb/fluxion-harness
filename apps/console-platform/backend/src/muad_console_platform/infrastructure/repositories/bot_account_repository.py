from collections.abc import Collection

from sqlalchemy import String, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from ..models.channel import BotAccount


class BotAccountRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_enabled(self, tenant_id: str, bot_id: str) -> BotAccount | None:
        account: BotAccount | None = await self._session.scalar(
            select(BotAccount).where(
                BotAccount.tenant_id == tenant_id,
                BotAccount.bot_id == bot_id,
                BotAccount.enabled.is_(True),
                BotAccount.is_deleted.is_(False),
            )
        )
        return account

    async def list_enabled(self, tenant_id: str) -> list[BotAccount]:
        accounts = await self._session.scalars(
            select(BotAccount)
            .where(
                BotAccount.tenant_id == tenant_id,
                BotAccount.enabled.is_(True),
                BotAccount.is_deleted.is_(False),
            )
            .order_by(BotAccount.bot_id)
        )
        return list(accounts.all())

    async def list_enabled_page(
        self,
        tenant_id: str,
        *,
        limit: int,
        offset: int,
        channels: Collection[str] | None = None,
    ) -> list[BotAccount]:
        """按稳定顺序取一页启用 bot（有界查询）；`channels` 限定调用方拥有的通道。"""
        accounts = await self._session.scalars(
            select(BotAccount)
            .where(
                BotAccount.tenant_id == tenant_id,
                BotAccount.enabled.is_(True),
                BotAccount.is_deleted.is_(False),
                *_channel_clause(channels),
            )
            .order_by(BotAccount.bot_id, BotAccount.id)
            .limit(limit)
            .offset(offset)
        )
        return list(accounts.all())

    async def enabled_snapshot_digest(
        self, tenant_id: str, *, channels: Collection[str] | None = None
    ) -> tuple[str, int]:
        """一次聚合查询：total 与覆盖**同一通道集合**启用 bot（路由 + secret）的摘要输入。

        分页之外的 total/revision 同源，保证 Gateway 跨页校验可用；摘要不暴露明文。
        按 `channels` 过滤是安全边界：Gateway 只该看到自己拥有通道的 secret。
        """
        digest_input = func.concat_ws(
            "|",
            cast(BotAccount.id, String),
            BotAccount.bot_id,
            cast(BotAccount.agent_id, String),
            func.coalesce(BotAccount.secret, ""),
            cast(BotAccount.enabled, String),
        )
        # 与行序无关的聚合摘要（逐行 hashtextextended 求和后再 sha256），
        # 单次查询即可覆盖全部启用 bot 的路由与 secret，不依赖分页顺序。
        row_digest = func.sum(func.hashtextextended(digest_input, 0)).cast(String)
        row = (
            await self._session.execute(
                select(
                    func.count(),
                    func.encode(
                        func.sha256(func.convert_to(func.coalesce(row_digest, ""), "UTF8")),
                        "hex",
                    ),
                ).where(
                    BotAccount.tenant_id == tenant_id,
                    BotAccount.enabled.is_(True),
                    BotAccount.is_deleted.is_(False),
                    *_channel_clause(channels),
                )
            )
        ).one()
        total, digest = int(row[0] or 0), row[1]
        return (str(digest) if digest else "", total)


def _channel_clause(channels: Collection[str] | None) -> tuple[ColumnElement[bool], ...]:
    """通道过滤子句：`None`/空集合 = 不过滤（管理面口径）。"""
    if not channels:
        return ()
    return (BotAccount.channel.in_(sorted(channels)),)
