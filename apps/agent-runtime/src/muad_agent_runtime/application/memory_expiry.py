"""Immediate read TTL is authoritative; cleanup uses the same atomic writer."""

import logging

from muad_api import AppError
from muad_contracts.memory import MemoryRetirementReason, memory_hash
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from ..infrastructure.db import SessionFactory
from ..infrastructure.db_clock import database_now
from ..infrastructure.models.runtime import UserMemory
from .memory_commands import MemoryCommand, MemoryMutation, MemoryWriteContext
from .memory_observability import observe
from .memory_service import MemoryService

logger = logging.getLogger(__name__)


async def expire_memories(factory: SessionFactory, *, scope_limit: int = 16) -> int:
    if isinstance(scope_limit, bool) or not 1 <= scope_limit <= 100:
        raise ValueError("invalid expiry scope limit")
    async with factory() as session:
        scopes = (
            await session.execute(
                select(UserMemory.tenant_id, UserMemory.user_id)
                .where(UserMemory.is_deleted.is_(False), UserMemory.expires_at <= func.statement_timestamp())
                .distinct()
                .order_by(UserMemory.tenant_id, UserMemory.user_id)
                .limit(scope_limit)
            )
        ).all()
    changed = 0
    for tenant, user in scopes:
        context = MemoryWriteContext(
            tenant_id=tenant, user_id=user, actor_type="SYSTEM", actor_id="memory-expiry"
        )
        try:
            changed += await _expire_scope(factory, context)
        except (SQLAlchemyError, AppError, ValidationError) as exc:
            logger.warning(
                "memory_expiry_failed",
                extra={"tenant_id": tenant, "user_id": str(user), "error_type": type(exc).__name__},
            )
            observe("memory_expiry_total", labels={"outcome": "ERROR"})
    return changed


async def _expire_scope(factory: SessionFactory, context: MemoryWriteContext) -> int:
    async with factory.begin() as session:
        await MemoryService.lock_scope(session, context)
        now = await database_now(session)
        rows = list(
            await session.scalars(
                select(UserMemory)
                .where(
                    UserMemory.tenant_id == context.tenant_id,
                    UserMemory.user_id == context.user_id,
                    UserMemory.is_deleted.is_(False),
                    UserMemory.expires_at <= now,
                )
                .order_by(UserMemory.memory_key)
                .limit(1000)
            )
        )
        if rows:
            command = MemoryCommand(
                command_id="expiry:"
                + memory_hash(
                    [context.tenant_id, str(context.user_id), [[str(row.id), row.version] for row in rows]]
                ),
                endpoint="memory.expiry",
                mutations=[
                    MemoryMutation(
                        action="RETIRE",
                        memory_key=row.memory_key,
                        expected_version=row.version,
                        retirement_reason=MemoryRetirementReason.EXPIRE,
                    )
                    for row in rows
                ],
            )
            outcome = await MemoryService().execute_in_session(session, context, command)
            amount = outcome.response.changed_count
        else:
            amount = 0
    return amount
