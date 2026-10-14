"""Authenticated service commands select ADMIN authority only on the server."""

from uuid import UUID

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts.memory import FactContent, MemoryCandidate, MemoryCategory
from muad_contracts.memory_admin import MemoryAdminMutation, MemoryAdminRequest, MemoryCommandResponse
from muad_contracts.platform_settings import MemoryPolicySettings
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.models.runtime import UserMemory
from .memory_commands import MemoryCommand, MemoryMutation, MemoryWriteContext
from .memory_service import MemoryService


class MemoryAdminService:
    def __init__(
        self,
        session: AsyncSession,
        policy: MemoryPolicySettings,
        credential_canaries: tuple[str, ...] = (),
    ) -> None:
        self._session, self._policy = session, policy
        self._credential_canaries = credential_canaries

    async def execute(
        self,
        tenant: str,
        user: UUID,
        request: MemoryAdminRequest,
        *,
        command_id: str,
        endpoint: str = "memory.admin.commands",
    ) -> MemoryCommandResponse:
        context = MemoryWriteContext(
            tenant_id=tenant,
            user_id=user,
            actor_type="ADMIN",
            actor_id=str(request.actor_id),
            settings=self._policy,
            credential_canaries=self._credential_canaries,
        )
        async with self._session.begin():
            mutation = await self._mutation(tenant, user, request.mutation)
            outcome = await MemoryService().execute_in_session(
                self._session,
                context,
                MemoryCommand(command_id=command_id, endpoint=endpoint, mutations=[mutation]),
            )
        return outcome.response

    async def _mutation(self, tenant: str, user: UUID, incoming: MemoryAdminMutation) -> MemoryMutation:
        options: dict[str, object] = incoming.model_dump(
            exclude_unset=True, exclude={"memory_id", "category", "value"}
        )
        if incoming.action == "UPSERT":
            assert (
                incoming.memory_key is not None
                and incoming.category is not None
                and incoming.value is not None
            )
            options.pop("memory_key", None)
            options["candidate"] = MemoryCandidate(
                memory_key=incoming.memory_key,
                category=MemoryCategory(incoming.category),
                content=FactContent(value=incoming.value),
            )
        elif incoming.memory_id is not None:
            key = await self._session.scalar(
                select(UserMemory.memory_key).where(
                    UserMemory.id == incoming.memory_id,
                    UserMemory.tenant_id == tenant,
                    UserMemory.user_id == user,
                )
            )
            if key is None:
                raise AppError(ErrorCode.MEMORY_NOT_FOUND)
            options["memory_key"] = key
        return MemoryMutation.model_validate(options)
