"""任务中心：surface 中立的任务读写（design §3.2.7、§3.4.1、§3.4.6 API-12..19/25/31；TASK-024）。

边界与语义：

- **一个 handler set，多个 surface**：`TaskCenterSurface` 只决定路由前缀与确认来源
  （`ConfirmationSurface`）；数据范围永远是「租户 + 平台用户 + 当前授权 Agent 集合」。
  入口/渠道只作来源元数据，不参与过滤（同一 user+agent 的任务跨入口共享）。
- **授权来自本次核验**：`ChatIdentity.agents` 是 Console 每次请求核验的当前授权集合；
  `agent_id` 过滤只会与它取交集，撤销授权后列表/详情/动作/下载一律不可见（404，不泄露存在性）。
- **网关不持库**：所有事实来自 Worker 的受检内部面（列表/详情/Fire/Execution/待处理/徽标/
  回答/动作/确认），产物字节按 Runtime 的**不可变版本**解析后从共享存储取件。
- **不写投递**：定时任务的结果只在任务中心 pull；本服务不写 `task_delivery`、不写
  `channel_notification`，也不推聊天流（B-11/S-11）。
- **失败不伪装**：上游错误码原样上抛；动作被准入拒绝时显式报错，不返回一份"看起来成功"
  的详情；下载版本不符返回冲突，绝不拿最新版顶替。

已知边界（契约事实，不是本层选择）：Execution 详情与 Draft 确认的 DTO 不携带 `agent_id`，
Worker 侧按 `tenant + actor` 核属；Agent 维度在列表/详情/Fire/待处理/回答/产物下载这些
携带 `agent_id` 的面上强制。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_artifact_store import NfsArtifactStore
from muad_contracts import (
    MAX_PAGE_SIZE,
    AttachmentRef,
    ConfirmationSurface,
    ExecutionDetail,
    FireHistoryPage,
    HumanRespondRequest,
    HumanRespondResponse,
    PendingPage,
    ScheduleActionRequest,
    ScheduleDetail,
    ScheduleDraftConfirmRequest,
    ScheduleDraftConfirmResponse,
    ScheduleListItem,
    ScheduleMode,
    ScheduleStatus,
    TaskCenterBadges,
)
from pydantic import BaseModel, ValidationError

from .chat_principal import ChatIdentity
from .webchat_artifacts import ArtifactDownload, open_download_reference

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TaskCenterSurface:
    """一次 surface 挂载：路由前缀段 + 该 surface 的用户确认来源。"""

    #: 路由前缀段（`/api/v1/{name}/...`）；本需求只交付 WebChat。
    name: str
    #: 确认来源：只有真实核验过用户动作的 surface 才会写进 Draft（API-27 的必填头）。
    confirmation_surface: ConfirmationSurface


#: WebChat 任务中心：路由实际挂载的唯一 surface（MSSWWEB 另立需求，本需求不挂）。
WEBCHAT_SURFACE = TaskCenterSurface(
    name="webchat", confirmation_surface=ConfirmationSurface.WEBCHAT
)


class TaskCenterOwnerPort(Protocol):
    """任务事实的 Owner 面（Worker 是唯一实现；结构替身只需实现同一动词面）。"""

    async def list_schedules(
        self,
        *,
        tenant_id: str,
        user_id: UUID,
        agent_id: UUID | None,
        status: ScheduleStatus | None,
        page: int,
        page_size: int,
    ) -> dict[str, Any]: ...

    async def get_schedule(
        self, *, tenant_id: str, user_id: UUID, schedule_id: UUID
    ) -> dict[str, Any]: ...

    async def list_fires(
        self,
        *,
        tenant_id: str,
        user_id: UUID,
        schedule_id: UUID,
        page: int,
        page_size: int,
    ) -> dict[str, Any]: ...

    async def get_execution(
        self, *, tenant_id: str, user_id: UUID, execution_id: UUID
    ) -> dict[str, Any]: ...

    async def list_pending(
        self,
        *,
        tenant_id: str,
        user_id: UUID,
        agent_id: UUID,
        kind: str | None,
        page: int,
        page_size: int,
    ) -> dict[str, Any]: ...

    async def badges(
        self, *, tenant_id: str, user_id: UUID, agent_id: UUID
    ) -> dict[str, Any]: ...

    async def respond_human(
        self,
        *,
        tenant_id: str,
        user_id: UUID,
        agent_id: UUID | None,
        interaction_id: UUID,
        payload: dict[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]: ...

    async def schedule_action(
        self,
        *,
        tenant_id: str,
        user_id: UUID,
        schedule_id: UUID,
        action: str,
        payload: dict[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]: ...

    async def confirm_draft(
        self,
        *,
        tenant_id: str,
        user_id: UUID,
        draft_id: UUID,
        payload: dict[str, Any],
        surface: ConfirmationSurface,
        ref: str,
        idempotency_key: str,
    ) -> dict[str, Any]: ...


class TaskArtifactVersionPort(Protocol):
    """任务产物的不可变版本解析（Runtime 是唯一实现；按 owner+agent 核属）。"""

    async def resolve_task_artifact_version(
        self,
        artifact_id: UUID,
        *,
        tenant_id: str,
        user_id: UUID,
        agent_id: UUID,
        version: str,
        checksum: str,
    ) -> dict[str, Any]: ...


class TaskCenterService:
    """把已验证的 `ChatIdentity` 换成任务读写；所有方法都先做 Agent 范围核属。"""

    def __init__(
        self,
        *,
        owner: TaskCenterOwnerPort,
        artifacts: TaskArtifactVersionPort,
        store: NfsArtifactStore,
        tenant_id: str,
    ) -> None:
        self._owner = owner
        self._artifacts = artifacts
        self._store = store
        self._tenant_id = tenant_id

    # -- 列表 / 详情 -------------------------------------------------------------

    async def list_schedules(
        self,
        identity: ChatIdentity,
        *,
        agent_id: UUID | None,
        status: ScheduleStatus | None,
        mode: ScheduleMode | None,
        page: int,
        page_size: int,
    ) -> dict[str, Any]:
        """API-12：授权 Agent 范围内的任务列表（`mode` 由本层按契约过滤）。

        `agent_id` 缺省时枚举 `ChatIdentity.agents` 的全部授权 Agent。Worker 侧没有 `mode`
        过滤，因此这里按**完整范围**读出后再过滤/分页——否则首页条数与 `total` 会与筛选
        条件不一致（分页参数只有一种含义）。
        """
        allowed = self._scope(identity, agent_id)
        if not allowed:
            return self._page([], page=page, page_size=page_size, total=0)
        items: list[ScheduleListItem] = []
        scanned = await self._collect_schedules(
            identity,
            agent_id=agent_id,
            status=status,
            allowed=allowed,
            mode=mode,
            sink=items,
        )
        logger.debug("task_center_list scanned=%s authorized=%s", scanned, len(items))
        start = (page - 1) * page_size
        window = items[start : start + page_size]
        return self._page(window, page=page, page_size=page_size, total=len(items))

    async def get_schedule(
        self, identity: ChatIdentity, schedule_id: UUID, *, agent_id: UUID | None = None
    ) -> ScheduleDetail:
        """API-13：详情与列表同一 Agent 范围；撤销授权/他 Agent 一律 404。"""
        allowed = self._scope(identity, agent_id)
        data = await self._owner.get_schedule(
            tenant_id=self._tenant(identity), user_id=identity.principal.platform_user_id,
            schedule_id=schedule_id,
        )
        detail = _project(ScheduleDetail, data)
        if detail.agent_id not in allowed:
            raise AppError(ErrorCode.SCHEDULE_NOT_FOUND)
        return detail

    async def list_fires(
        self,
        identity: ChatIdentity,
        schedule_id: UUID,
        *,
        page: int,
        page_size: int,
    ) -> dict[str, Any]:
        """API-14：Fire 历史先核属 Schedule（Agent 范围），再按页读 Owner 的台账。"""
        await self.get_schedule(identity, schedule_id)
        data = await self._owner.list_fires(
            tenant_id=self._tenant(identity),
            user_id=identity.principal.platform_user_id,
            schedule_id=schedule_id,
            page=page,
            page_size=page_size,
        )
        return _project(FireHistoryPage, data).model_dump(mode="json")

    async def get_execution(self, identity: ChatIdentity, execution_id: UUID) -> dict[str, Any]:
        """API-15：Execution 详情的唯一读点（Owner 按 tenant+actor 核属，跨用户 404）。"""
        data = await self._owner.get_execution(
            tenant_id=self._tenant(identity),
            user_id=identity.principal.platform_user_id,
            execution_id=execution_id,
        )
        return _project(ExecutionDetail, data).model_dump(mode="json")

    # -- 待处理 / 徽标 / 回答 -----------------------------------------------------

    async def list_pending(
        self,
        identity: ChatIdentity,
        *,
        agent_id: UUID | None,
        kind: str | None,
        page: int,
        page_size: int,
    ) -> dict[str, Any]:
        """API-16：HUMAN 与 NEEDS_ATTENTION 混排；范围与列表一致。"""
        allowed = self._scope(identity, agent_id)
        items: list[Any] = []
        for agent in allowed:
            collected = await self._collect_pending(identity, agent=agent, kind=kind)
            items.extend(collected)
        # 与 Worker 同一稳定键：先 kind 再 schedule_id（跨 Agent 合并后仍确定性）。
        items.sort(key=lambda item: (item.kind, str(item.schedule_id)))
        start = (page - 1) * page_size
        window = items[start : start + page_size]
        return {
            "items": [item.model_dump(mode="json") for item in window],
            "page": page,
            "page_size": page_size,
            "total": len(items),
        }

    async def badges(self, identity: ChatIdentity, *, agent_id: UUID | None) -> dict[str, Any]:
        """API-17：徽标与列表同一 Agent 范围（否则页面数字与列表对不上）。"""
        allowed = self._scope(identity, agent_id)
        pending_human = 0
        needs_attention = 0
        for agent in allowed:
            data = await self._owner.badges(
                tenant_id=self._tenant(identity),
                user_id=identity.principal.platform_user_id,
                agent_id=agent,
            )
            badge = _project(TaskCenterBadges, data)
            pending_human += badge.pending_human
            needs_attention += badge.needs_attention
        return TaskCenterBadges(
            pending_human=pending_human, needs_attention=needs_attention
        ).model_dump(mode="json")

    async def respond_human(
        self,
        identity: ChatIdentity,
        interaction_id: UUID,
        request: HumanRespondRequest,
        *,
        agent_id: UUID | None,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """API-18：回答 Human 提问（写；幂等键原样交给 Owner 的台账）。

        Owner 按 `tenant + actor`（+ 可选的 Agent 一致性）核属；本层声明的 Agent 必须先
        通过当前授权集合，且只在范围恰好收敛到一个 Agent 时才把它作为一致性声明转发。
        """
        allowed = self._scope(identity, agent_id)
        if not allowed:
            # 授权全部撤销后连"回答"也不该成立（读到不存在的待处理项与写进去是两件事）。
            raise AppError(ErrorCode.TASK_ACCESS_DENIED)
        declared = agent_id if agent_id is not None else (allowed[0] if len(allowed) == 1 else None)
        data = await self._owner.respond_human(
            tenant_id=self._tenant(identity),
            user_id=identity.principal.platform_user_id,
            agent_id=declared,
            interaction_id=interaction_id,
            payload=request.model_dump(mode="json"),
            idempotency_key=idempotency_key,
        )
        return _project(HumanRespondResponse, data).model_dump(mode="json")

    # -- 动作 / 确认 -------------------------------------------------------------

    async def schedule_action(
        self,
        identity: ChatIdentity,
        schedule_id: UUID,
        request: ScheduleActionRequest,
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """API-19：pause/resume/cancel（与内部 API 共用 Owner 的 ScheduleService）。

        动作后重读详情：返回的永远是**动作生效之后**的事实；Owner 明确拒绝准入时
        （返回体里的 `admission_error`）显式报错，不把"没生效"包装成成功。
        """
        await self.get_schedule(identity, schedule_id)
        data = await self._owner.schedule_action(
            tenant_id=self._tenant(identity),
            user_id=identity.principal.platform_user_id,
            schedule_id=schedule_id,
            action=request.action,
            payload=request.model_dump(mode="json"),
            idempotency_key=idempotency_key,
        )
        admission_error = data.get("admission_error")
        if admission_error is not None:
            logger.warning("task_center_action_refused action=%s", request.action)
            raise AppError(ErrorCode.SCHEDULE_ADMISSION_FAILED, data=admission_error)
        updated = await self.get_schedule(identity, schedule_id)
        return updated.model_dump(mode="json")

    async def confirm_draft(
        self,
        identity: ChatIdentity,
        draft_id: UUID,
        request: ScheduleDraftConfirmRequest,
        *,
        surface: ConfirmationSurface,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """API-31 → API-27：把可信 ChatPrincipal 作为 actor 去确认 Draft。

        `confirmation_ref` 用调用方的幂等键（一次用户动作 = 一个稳定引用），随
        `confirmation_surface` 一起交给 Owner —— 模型工具上下文没有这条通路，无法自确认。
        """
        data = await self._owner.confirm_draft(
            tenant_id=self._tenant(identity),
            user_id=identity.principal.platform_user_id,
            draft_id=draft_id,
            payload=request.model_dump(mode="json"),
            surface=surface,
            ref=idempotency_key,
            idempotency_key=idempotency_key,
        )
        return _project(ScheduleDraftConfirmResponse, data).model_dump(mode="json")

    # -- 产物下载 ---------------------------------------------------------------

    async def open_artifact(
        self,
        identity: ChatIdentity,
        artifact_id: UUID,
        *,
        version: str,
        checksum: str,
    ) -> ArtifactDownload:
        """API-25：按任务 owner+agent 下载**指定不可变版本**（不要求 Conversation）。

        没有 `agent_id` 入参：产物 DTO 不带 Agent，Owner 解析面按
        `artifact.task_id → task_execution` 核属。这里只在**当前授权的 Agent** 之间查找，
        所以撤销/未授权 Agent 的产物必然 404；版本或校验值不匹配由 Owner 判冲突并原样上抛，
        绝不退化成"给最新版"。
        """
        agents = self._scope(identity, None)
        for agent in agents:
            try:
                data = await self._artifacts.resolve_task_artifact_version(
                    artifact_id,
                    tenant_id=self._tenant(identity),
                    user_id=identity.principal.platform_user_id,
                    agent_id=agent,
                    version=version,
                    checksum=checksum,
                )
            except AppError as exc:
                if exc.code == str(ErrorCode.TASK_ARTIFACT_NOT_FOUND):
                    continue
                raise
            reference = _project(AttachmentRef, data)
            return open_download_reference(self._store, reference)
        raise AppError(ErrorCode.TASK_ARTIFACT_NOT_FOUND)

    # -- 内部 -------------------------------------------------------------------

    def _tenant(self, identity: ChatIdentity) -> str:
        """请求作用域的租户：只认 Console 核验过的主体（部署租户已在那一步比对）。"""
        return identity.principal.tenant_id

    def _scope(self, identity: ChatIdentity, agent_id: UUID | None) -> tuple[UUID, ...]:
        """当前授权 Agent 集合；显式 `agent_id` 必须落在集合内（否则与不存在同形）。"""
        allowed = tuple(agent.agent_id for agent in identity.agents)
        if agent_id is None:
            return allowed
        if agent_id not in allowed:
            raise AppError(ErrorCode.SCHEDULE_NOT_FOUND)
        return (agent_id,)

    async def _collect_schedules(
        self,
        identity: ChatIdentity,
        *,
        agent_id: UUID | None,
        status: ScheduleStatus | None,
        allowed: tuple[UUID, ...],
        mode: ScheduleMode | None,
        sink: list[ScheduleListItem],
    ) -> int:
        """按页读 Owner 的整段范围并按 Agent/模式过滤；返回过滤前的总条数（日志用）。"""
        first = await self._schedule_page(
            identity, agent_id=agent_id, status=status, page=1
        )
        seen: set[UUID] = set()
        total = first[1]
        self._absorb(first[0], allowed=allowed, mode=mode, sink=sink, seen=seen)
        for number in range(2, _page_count(total) + 1):
            chunk, _ = await self._schedule_page(
                identity, agent_id=agent_id, status=status, page=number
            )
            self._absorb(chunk, allowed=allowed, mode=mode, sink=sink, seen=seen)
        return total

    async def _schedule_page(
        self,
        identity: ChatIdentity,
        *,
        agent_id: UUID | None,
        status: ScheduleStatus | None,
        page: int,
    ) -> tuple[list[ScheduleListItem], int]:
        data = await self._owner.list_schedules(
            tenant_id=self._tenant(identity),
            user_id=identity.principal.platform_user_id,
            agent_id=agent_id,
            status=status,
            page=page,
            page_size=MAX_PAGE_SIZE,
        )
        raw_items = data.get("items")
        total = data.get("total")
        if not isinstance(raw_items, list) or not isinstance(total, int):
            logger.warning("task_center_schedule_page_invalid")
            raise AppError(ErrorCode.COMMON_INTERNAL_ERROR)
        return [_project(ScheduleListItem, raw) for raw in raw_items], total

    @staticmethod
    def _absorb(
        chunk: list[ScheduleListItem],
        *,
        allowed: tuple[UUID, ...],
        mode: ScheduleMode | None,
        sink: list[ScheduleListItem],
        seen: set[UUID],
    ) -> None:
        for item in chunk:
            if item.agent_id not in allowed:
                continue
            if mode is not None and item.mode is not mode:
                continue
            if item.schedule_id in seen:
                continue
            seen.add(item.schedule_id)
            sink.append(item)

    async def _collect_pending(
        self, identity: ChatIdentity, *, agent: UUID, kind: str | None
    ) -> list[Any]:
        """单个 Agent 的完整待处理集合（Owner 侧本身就是内存分页，这里合并后统一分页）。"""
        first = await self._pending_page(identity, agent=agent, kind=kind, page=1)
        items: list[Any] = list(first.items)
        for number in range(2, _page_count(first.total) + 1):
            chunk = await self._pending_page(identity, agent=agent, kind=kind, page=number)
            items.extend(chunk.items)
        return items

    async def _pending_page(
        self, identity: ChatIdentity, *, agent: UUID, kind: str | None, page: int
    ) -> PendingPage:
        data = await self._owner.list_pending(
            tenant_id=self._tenant(identity),
            user_id=identity.principal.platform_user_id,
            agent_id=agent,
            kind=kind,
            page=page,
            page_size=MAX_PAGE_SIZE,
        )
        return _project(PendingPage, data)

    @staticmethod
    def _page(
        items: list[Any], *, page: int, page_size: int, total: int
    ) -> dict[str, Any]:
        return {
            "items": [item.model_dump(mode="json") for item in items],
            "page": page,
            "page_size": page_size,
            "total": total,
        }


def _page_count(total: int) -> int:
    """整段范围需要读多少页（`MAX_PAGE_SIZE` 是 Owner 的单页上限）。"""
    return max(1, -(-total // MAX_PAGE_SIZE))


def _project[ContractT: BaseModel](model: type[ContractT], data: object) -> ContractT:
    """Owner 封套 → 公开契约；字段不符显式失败（不回显内部内容）。"""
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        logger.warning("task_center_owner_projection_failed model=%s", model.__name__)
        raise AppError(ErrorCode.COMMON_INTERNAL_ERROR) from exc
