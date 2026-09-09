"""ChatSessionService：外部会话 → Runtime 逻辑 Session 映射（设计 §10）。

- 普通消息：`resolve_session()` 取 Head 的 active 指针（无 Head 即建）；
- `/new`：`rotate_session()` 原子轮换指针（revision CAS，不重试，不隐式 Stop）；
- 长期数据（UserProfile/Personal Memory/授权/Binding/Trace）一律不删：
  旧 Memory 行保留，只是新 session 不再引用。

active execution 冲突经可注入 probe 判定（TASK-005 前默认无执行跟踪，
见 `services.channel_app` 缺省实现）。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from fluxion.registry import AuditRecord, ChannelRegistryStore, ChatSessionHead
from fluxion.registry.store import VersionConflictError


class SessionBusyError(RuntimeError):
    """旧 Session 仍有 active execution，拒绝轮换。"""

    code = "new_session_conflict"

    def __init__(self) -> None:
        super().__init__("当前任务仍在运行，请先使用 /stop")


class SessionConflictError(RuntimeError):
    """Head 被并发修改（CAS 失败），请重试。"""

    code = "execution_control_conflict"

    def __init__(self) -> None:
        super().__init__("会话已被并发修改，请重试")


SessionActivityProbe = Callable[[ChatSessionHead], Awaitable[bool]]
"""判定 Head 的旧 session 是否有 active execution（TASK-005 接真实 Control Store）。"""


@dataclass(frozen=True, slots=True)
class SessionKey:
    tenant_id: str
    channel_type: str
    external_conversation_id: str
    platform_user_id: str
    agent_id: str


def new_session_id() -> str:
    """服务端生成的 Runtime 逻辑 Session ID（§10.4）。"""
    return f"sess_{uuid4().hex}"


class ChatSessionService:
    """服务端 Session Head 管理（无状态服务，实例可跨请求复用）。"""

    def __init__(
        self,
        store: ChannelRegistryStore,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._clock = clock or (lambda: datetime.now(UTC))

    async def resolve_session(
        self,
        tenant_id: str,
        channel_type: str,
        external_conversation_id: str,
        platform_user_id: str,
        agent_id: str,
    ) -> str:
        """取 active 指针；无 Head 则创建后返回（§10.5）。"""
        head = await self._store.get_session_head(
            tenant_id=tenant_id,
            channel_type=channel_type,
            external_conversation_id=external_conversation_id,
            platform_user_id=platform_user_id,
            agent_id=agent_id,
        )
        if head is not None:
            return head.active_session_id
        now = self._clock()
        created = ChatSessionHead(
            tenant_id=tenant_id,
            channel_type=channel_type,
            external_conversation_id=external_conversation_id,
            platform_user_id=platform_user_id,
            agent_id=agent_id,
            active_session_id=new_session_id(),
            revision=0,
            created_at=now,
            updated_at=now,
        )
        try:
            return (await self._store.create_session_head(created)).active_session_id
        except Exception:
            # 并发首次建 Head：PK 冲突，后到的读胜者指针（有界一次回读）。
            existing = await self._store.get_session_head(
                tenant_id=tenant_id,
                channel_type=channel_type,
                external_conversation_id=external_conversation_id,
                platform_user_id=platform_user_id,
                agent_id=agent_id,
            )
            if existing is None:
                raise
            return existing.active_session_id

    async def rotate_session(
        self,
        tenant_id: str,
        channel_type: str,
        external_conversation_id: str,
        platform_user_id: str,
        agent_id: str,
        *,
        request_id: str = "",
        has_active_execution: SessionActivityProbe | None = None,
    ) -> str:
        """原子轮换 active 指针并审计（§10.6）。CAS 失败不重试，直接冲突。"""
        head = await self._store.get_session_head(
            tenant_id=tenant_id,
            channel_type=channel_type,
            external_conversation_id=external_conversation_id,
            platform_user_id=platform_user_id,
            agent_id=agent_id,
        )
        if head is None:
            # 首条消息即 /new：建 Head 即新会话，无旧指针可冲突。
            return await self.resolve_session(
                tenant_id, channel_type, external_conversation_id, platform_user_id, agent_id
            )
        if has_active_execution is not None and await has_active_execution(head):
            raise SessionBusyError()
        now = self._clock()
        rotated = ChatSessionHead(
            tenant_id=head.tenant_id,
            channel_type=head.channel_type,
            external_conversation_id=head.external_conversation_id,
            platform_user_id=head.platform_user_id,
            agent_id=head.agent_id,
            active_session_id=new_session_id(),
            revision=head.revision + 1,
            created_at=head.created_at,
            updated_at=now,
        )
        try:
            await self._store.rotate_session_head(rotated, expected_revision=head.revision)
        except VersionConflictError as exc:
            raise SessionConflictError() from exc
        await self._store.append_audit(
            AuditRecord(
                audit_id=f"audit_{uuid4().hex}",
                tenant_id=tenant_id,
                actor_id=platform_user_id,
                request_id=request_id,
                action="session.rotated",
                target_type="chat_session",
                target_id=head.active_session_id,
                before={"session_id": head.active_session_id, "revision": head.revision},
                after={"session_id": rotated.active_session_id, "revision": rotated.revision},
                created_at=now,
            )
        )
        return rotated.active_session_id
