from __future__ import annotations

import asyncio
import hashlib
import secrets
import traceback
from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import uuid4

from fluxion.errors.console import (
    INTERNAL_ERROR,
    ConsoleResourceNotFoundError,
)
from fluxion.observability.logging import emit_error_log
from fluxion.registry import (
    AuditRecord,
    ChannelRegistryStore,
    ChatAccessRecord,
    NotFoundError,
    PlatformUserRecord,
)
from fluxion.resources import ResourceDefinition, ResourceKind
from fluxion.runtime.secrets import SecretMetadata, SecretMetadataStore
from fluxion.runtime.tracing import TraceRecord, TraceStore
from fluxion.services.approval_app import ApprovalStore, InMemoryApprovalStore
from fluxion.services.console_contracts import ConsoleActor
from fluxion.services.console_governance import ConsoleGovernanceOps
from fluxion.services.console_payloads import (
    IssuedChatAccess,
    _capability_payload,
    platform_user_payload,
)
from fluxion.services.console_resources import ConsoleResourceOps
from fluxion.services.runtime_contracts import PluginSummary
from fluxion.services.workflow_app import WorkflowDefinitionValidator


class ConsoleApplicationService(ConsoleResourceOps, ConsoleGovernanceOps):
    def __init__(
        self,
        store: ChannelRegistryStore,
        *,
        trace_store: TraceStore | None = None,
        secret_metadata_store: SecretMetadataStore | None = None,
        approval_store: ApprovalStore | None = None,
        plugin_summaries: Sequence[PluginSummary] = (),
        service_instance_id: str | None = None,
    ) -> None:
        self._store = store
        self._trace_store = trace_store
        self._secret_metadata_store = secret_metadata_store
        self._approval_store = approval_store or InMemoryApprovalStore()
        self._workflow_validator = WorkflowDefinitionValidator(store)
        self._deployment_actions: list[str] = []
        # 只读运行时身份快照：由装配方（dev bundle）注入，避免 Console 反向依赖 Runtime。
        self._plugin_summaries = tuple(plugin_summaries)
        self._service_instance_id = service_instance_id or "console-standalone"
        # 单进程内按资源串行化 publish/rollback/deprecate，保证 optimistic-lock
        # 的 check-then-commit 原子；多实例部署需依赖 DB 级串行化（如 advisory lock）。
        self._publication_locks: dict[tuple[str, ResourceKind, str], asyncio.Lock] = {}

    @property
    def deployment_actions(self) -> tuple[str, ...]:
        return tuple(self._deployment_actions)

    async def initialize(self) -> None:
        await self._store.initialize()

    async def close(self) -> None:
        await self._store.close()

    async def create_platform_user(
        self,
        actor: ConsoleActor,
        *,
        platform_user_id: str,
        display_name: str,
    ) -> PlatformUserRecord:
        now = datetime.now(UTC)
        record = PlatformUserRecord(
            tenant_id=actor.tenant_id,
            platform_user_id=platform_user_id,
            display_name=display_name or platform_user_id,
            created_at=now,
        )
        created = await self._store.create_platform_user(record)
        await self._append_audit(
            actor,
            action="platform_user.create",
            target_type="platform_user",
            target_id=platform_user_id,
            before=None,
            after=platform_user_payload(created),
        )
        return created

    async def list_platform_users(
        self,
        actor: ConsoleActor,
        *,
        page: int,
        page_size: int,
    ) -> tuple[list[PlatformUserRecord], int]:
        return await self._store.list_platform_users(
            tenant_id=actor.tenant_id,
            offset=(page - 1) * page_size,
            limit=page_size,
        )

    async def list_policies(
        self,
        actor: ConsoleActor,
        *,
        page: int,
        page_size: int,
    ) -> tuple[list[ResourceDefinition], int]:
        """列出 tenant 已注册的 Policy 资源（P1 Plugin/Hook Policy 视图只读数据源）。"""
        return await self._store.list_resources(
            kind=ResourceKind.POLICY,
            tenant_id=actor.tenant_id,
            offset=(page - 1) * page_size,
            limit=page_size,
        )

    async def list_capabilities(
        self,
        actor: ConsoleActor,
    ) -> list[dict[str, object]]:
        """Capability Registry 视图：枚举已装配运行时插件的能力描述（只读快照）。"""
        del actor  # 能力注册表为全局装配快照，不按 tenant 划分
        return [_capability_payload(summary) for summary in self._plugin_summaries]

    async def runtime_status(
        self,
        actor: ConsoleActor,
    ) -> dict[str, object]:
        """Runtime Status 视图：只读运行时身份与健康摘要，不管理 Agent Pod。"""
        del actor
        return {
            "service_instance_id": self._service_instance_id,
            "status": "healthy",
            "provider_count": len(self._plugin_summaries),
            "plugin_count": len(self._plugin_summaries),
        }

    async def get_trace(self, actor: ConsoleActor, trace_id: str) -> TraceRecord:
        if self._trace_store is None:
            raise ConsoleResourceNotFoundError("trace store is not configured")
        trace = await self._trace_store.get(trace_id)
        if trace is None or trace.tenant_id != actor.tenant_id:
            raise ConsoleResourceNotFoundError("trace not found")
        return trace

    async def list_credentials(
        self,
        actor: ConsoleActor,
        *,
        page: int,
        page_size: int,
    ) -> tuple[list[SecretMetadata], int]:
        if self._secret_metadata_store is None:
            return [], 0
        return await self._secret_metadata_store.list_metadata(
            tenant_id=actor.tenant_id,
            offset=(page - 1) * page_size,
            limit=page_size,
        )

    async def list_runs(
        self,
        actor: ConsoleActor,
        *,
        page: int,
        page_size: int,
    ) -> tuple[list[TraceRecord], int]:
        if self._trace_store is None:
            return [], 0
        return await self._trace_store.list_recent(
            tenant_id=actor.tenant_id,
            offset=(page - 1) * page_size,
            limit=page_size,
        )

    async def get_run(self, actor: ConsoleActor, execution_id: str) -> TraceRecord:
        if self._trace_store is None:
            raise ConsoleResourceNotFoundError("trace store is not configured")
        trace = await self._trace_store.get_by_execution(
            tenant_id=actor.tenant_id,
            execution_id=execution_id,
        )
        if trace is None:
            raise ConsoleResourceNotFoundError("run not found")
        return trace

    async def list_audit(
        self,
        actor: ConsoleActor,
        *,
        page: int,
        page_size: int,
    ) -> tuple[list[AuditRecord], int]:
        return await self._store.list_audit(
            tenant_id=actor.tenant_id,
            offset=(page - 1) * page_size,
            limit=page_size,
        )

    async def issue_chat_access(
        self,
        actor: ConsoleActor,
        *,
        platform_user_id: str,
        runtime_profile_id: str,
    ) -> IssuedChatAccess:
        user = await self._store.get_platform_user(
            tenant_id=actor.tenant_id,
            platform_user_id=platform_user_id,
        )
        if user is None:
            raise ConsoleResourceNotFoundError("platform user not found")
        # 已知 gap：不校验 runtime_profile 是否已发布——dev 模式下 profile 可由
        # 运行时按需解析（见 test_S_P13_04），强制校验会破坏该契约。token 绑定到
        # profile_id，运行时解析失败会以 runtime 错误呈现，而非越权风险。
        token = secrets.token_urlsafe(32)
        record = ChatAccessRecord(
            access_id=f"chat_access_{uuid4().hex}",
            tenant_id=actor.tenant_id,
            platform_user_id=platform_user_id,
            runtime_profile_id=runtime_profile_id,
            token_hash=_hash_access_token(token),
            created_at=datetime.now(UTC),
        )
        await self._store.create_chat_access(record)
        await self._append_audit(
            actor,
            action="chat_access.create",
            target_type="chat_access",
            target_id=record.access_id,
            before=None,
            after=_chat_access_audit_payload(record),
        )
        return IssuedChatAccess(record=record, token=token)

    async def revoke_chat_access(
        self,
        actor: ConsoleActor,
        *,
        access_id: str,
    ) -> ChatAccessRecord:
        try:
            record = await self._store.revoke_chat_access(
                tenant_id=actor.tenant_id,
                access_id=access_id,
                revoked_at=datetime.now(UTC),
            )
        except NotFoundError as exc:
            raise ConsoleResourceNotFoundError("chat access not found") from exc
        await self._append_audit(
            actor,
            action="chat_access.revoke",
            target_type="chat_access",
            target_id=access_id,
            before=None,
            after=_chat_access_audit_payload(record),
        )
        return record

    async def _append_audit(
        self,
        actor: ConsoleActor,
        *,
        action: str,
        target_type: str,
        target_id: str,
        before: dict[str, object] | None,
        after: dict[str, object] | None,
    ) -> None:
        # Binding 等治理类操作沿用独立 Audit 写入；Publication 使用 Registry 原子事务。
        # A20/契约§7：审计写失败不再静默吞掉（fail-open）——Binding 权限变更等必须
        # 进 AuditLog 独立持久化，审计失败应令操作可见地失败，对齐 Publication 的
        # fail-closed。注：当前 audit 在主操作之后独立写入（非同事务），真正的原子性
        # 需将 audit 并入主操作事务（后续 store 契约重构项）；此处先消除静默吞没。
        try:
            await self._store.append_audit(
                AuditRecord(
                    audit_id=f"audit_{uuid4().hex}",
                    tenant_id=actor.tenant_id,
                    actor_id=actor.actor_id,
                    request_id=actor.request_id,
                    action=action,
                    target_type=target_type,
                    target_id=target_id,
                    before=before,
                    after=after,
                )
            )
        except Exception as exc:
            emit_error_log(
                request_id=actor.request_id,
                trace_id=actor.trace_id,
                tenant_id=actor.tenant_id,
                actor_id=actor.actor_id,
                method="service",
                route=f"audit.{action}",
                error_type=type(exc).__name__,
                error_code=INTERNAL_ERROR,
                stack=traceback.format_exc(),
            )
            raise


def _hash_access_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _chat_access_audit_payload(record: ChatAccessRecord) -> dict[str, object]:
    return {
        "access_id": record.access_id,
        "platform_user_id": record.platform_user_id,
        "runtime_profile_id": record.runtime_profile_id,
        "revoked": record.revoked_at is not None,
    }
