"""内部通道 Skills 查询：复用 Effective Capability 公式，不复制授权逻辑。

调用方为 IM Gateway 的 `/skills` 命令（API-04）。只返回允许字段，未授权/禁用/删除
资源既不返回也不泄露存在性；分页在有界查询内完成（count + limit/offset）。
"""

from __future__ import annotations

from uuid import UUID

from muad_api import AppError, validate_page
from muad_api.error_codes import ErrorCode
from muad_contracts import DEFAULT_PAGE_SIZE, ChannelSkillItem, ChannelSkillsResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.repositories.agent_access_grant_repository import AgentAccessGrantRepository
from ..infrastructure.repositories.skill_repository import SkillRepository


class ChannelSkillsService:
    def __init__(self, session: AsyncSession) -> None:
        self._grants = AgentAccessGrantRepository(session)
        self._skills = SkillRepository(session)

    async def list_skills(
        self,
        tenant_id: str,
        agent_id: UUID,
        platform_user_id: UUID,
        *,
        page: int = 1,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> ChannelSkillsResponse:
        valid = validate_page(page, page_size)
        if not await self._grants.has_active_grant(tenant_id, platform_user_id, agent_id):
            raise AppError(ErrorCode.AGENT_ACCESS_DENIED)
        total = await self._skills.count_effective_for_agent(
            tenant_id, agent_id, platform_user_id
        )
        rows = await self._skills.list_effective_for_agent(
            tenant_id,
            agent_id,
            platform_user_id,
            limit=valid.page_size,
            offset=(valid.page - 1) * valid.page_size,
        )
        return ChannelSkillsResponse(
            items=[
                ChannelSkillItem(
                    skill_id=skill.id,
                    key=skill.key,
                    name=skill.name,
                    platform_label=skill.platform_label or skill.name,
                    description=skill.description or "",
                )
                for skill, _artifact in rows
            ],
            page=valid.page,
            page_size=valid.page_size,
            total=total,
        )
