"""IM 通道（bot_account）Console CRUD：secret 明文入 Owner 表，不进审计/响应。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.models.channel import BotAccount
from ..infrastructure.models.control import AgentDefinition
from .audit_service import AuditActor, AuditService

AUDIT_RESOURCE_TYPE = "AGENT"


def channel_snapshot(channel: BotAccount) -> dict[str, Any]:
    return {
        "channel": channel.channel,
        "name": channel.name,
        "bot_id": channel.bot_id,
        "agent_id": str(channel.agent_id),
        "enabled": channel.enabled,
    }


def channel_item(channel: BotAccount) -> dict[str, Any]:
    return {
        "channel_account_id": str(channel.id),
        "channel": channel.channel,
        "name": channel.name,
        "bot_id": channel.bot_id,
        "agent_id": str(channel.agent_id),
        "secret_configured": bool(channel.secret),
        "enabled": channel.enabled,
        "config": channel.config_json,
        "last_connected_at": (
            channel.last_connected_at.isoformat() if channel.last_connected_at else None
        ),
        "create_time": channel.create_time.isoformat(),
    }


class ChannelAdminService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._audit = AuditService(session)

    async def _require_agent(self, tenant_id: str, agent_id: uuid.UUID) -> AgentDefinition:
        agent = await self._session.get(AgentDefinition, agent_id)
        if agent is None or agent.is_deleted or agent.tenant_id != tenant_id:
            raise AppError(ErrorCode.AGENT_NOT_FOUND)
        return agent

    async def _get_channel(self, agent_id: uuid.UUID, channel_account_id: uuid.UUID) -> BotAccount:
        channel = await self._session.get(BotAccount, channel_account_id)
        if channel is None or channel.is_deleted or channel.agent_id != agent_id:
            raise AppError(ErrorCode.BOT_NOT_FOUND)
        return channel

    async def list_channels(
        self, tenant_id: str, agent_id: uuid.UUID, page: int, page_size: int
    ) -> tuple[list[dict[str, Any]], int]:
        await self._require_agent(tenant_id, agent_id)
        conditions = (
            BotAccount.agent_id == agent_id,
            BotAccount.is_deleted.is_(False),
        )
        total = await self._session.scalar(
            select(func.count()).select_from(BotAccount).where(*conditions)
        )
        rows = await self._session.execute(
            select(BotAccount)
            .where(*conditions)
            .order_by(BotAccount.create_time)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        items = [channel_item(channel) for channel in rows.scalars().all()]
        return items, int(total or 0)

    async def add_channel(
        self,
        tenant_id: str,
        agent_id: uuid.UUID,
        payload: dict[str, Any],
        actor: AuditActor,
    ) -> dict[str, Any]:
        agent = await self._require_agent(tenant_id, agent_id)
        secret = payload.get("secret")
        if not secret:
            raise AppError(ErrorCode.COMMON_INTERNAL_ERROR)
        channel = BotAccount(
            tenant_id=tenant_id,
            channel=payload.get("channel") or "WECOM",
            name=payload["name"],
            bot_id=payload["bot_id"],
            secret=secret,
            agent_id=agent.id,
            enabled=payload.get("enabled", True),
            config_json=payload.get("config") or {},
        )
        self._session.add(channel)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            raise AppError(
                ErrorCode.BOT_ID_EXISTS,
                message_args={"bot_id": payload["bot_id"]},
                data={"field": "bot_id", "bot_id": payload["bot_id"]},
            ) from exc
        await self._audit.record_config_change(
            tenant_id=tenant_id,
            actor=actor,
            resource_type=AUDIT_RESOURCE_TYPE,
            resource_id=agent.id,
            action="CREATE",
            before=None,
            after=channel_snapshot(channel),
        )
        return {
            "channel_account_id": str(channel.id),
            "bot_id": channel.bot_id,
            "agent_id": str(channel.agent_id),
            "enabled": channel.enabled,
            "last_connected_at": None,
        }

    async def update_channel(
        self,
        tenant_id: str,
        agent_id: uuid.UUID,
        channel_account_id: uuid.UUID,
        payload: dict[str, Any],
        actor: AuditActor,
    ) -> dict[str, Any]:
        await self._require_agent(tenant_id, agent_id)
        channel = await self._get_channel(agent_id, channel_account_id)
        before = channel_snapshot(channel)
        if payload.get("name") is not None:
            channel.name = payload["name"]
        if payload.get("bot_id") is not None:
            channel.bot_id = payload["bot_id"]
        if payload.get("secret"):
            channel.secret = payload["secret"]  # 传入即轮换（明文覆盖）
        if payload.get("enabled") is not None:
            channel.enabled = payload["enabled"]
        if payload.get("config") is not None:
            channel.config_json = payload["config"]
        channel.update_time = datetime.now(UTC)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            raise AppError(
                ErrorCode.BOT_ID_EXISTS,
                message_args={"bot_id": channel.bot_id},
                data={"field": "bot_id", "bot_id": channel.bot_id},
            ) from exc
        await self._audit.record_config_change(
            tenant_id=tenant_id,
            actor=actor,
            resource_type=AUDIT_RESOURCE_TYPE,
            resource_id=agent_id,
            action="UPDATE",
            before=before,
            after=channel_snapshot(channel),
        )
        return {
            "channel_account_id": str(channel.id),
            "bot_id": channel.bot_id,
            "enabled": channel.enabled,
            "last_connected_at": (
                channel.last_connected_at.isoformat() if channel.last_connected_at else None
            ),
            "update_time": channel.update_time.isoformat(),
        }

    async def remove_channel(
        self,
        tenant_id: str,
        agent_id: uuid.UUID,
        channel_account_id: uuid.UUID,
        actor: AuditActor,
    ) -> dict[str, Any]:
        await self._require_agent(tenant_id, agent_id)
        channel = await self._get_channel(agent_id, channel_account_id)
        before = channel_snapshot(channel)
        channel.is_deleted = True
        channel.update_time = datetime.now(UTC)
        await self._session.flush()
        await self._audit.record_config_change(
            tenant_id=tenant_id,
            actor=actor,
            resource_type=AUDIT_RESOURCE_TYPE,
            resource_id=agent_id,
            action="DELETE",
            before=before,
            after=None,
        )
        return {"channel_account_id": str(channel.id), "is_deleted": True}
