from __future__ import annotations

import asyncio
import hashlib
import re
import secrets
import traceback
from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import uuid4

from fluxion.errors.console import (
    CHANNEL_AGENT_NOT_FOUND,
    INTERNAL_ERROR,
    VALIDATION_FAILED,
    ConsoleError,
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
from fluxion.repositories.credential_projection import CredentialProjectionReader
from fluxion.resources import ResourceDefinition, ResourceKind, ResourceStatus, ResourceVisibility
from fluxion.runtime.secrets import (
    CredentialResolver,
    SecretMetadata,
    SecretMetadataStore,
    SecretStore,
)
from fluxion.runtime.tracing import TraceRecord, TraceStore
from fluxion.services.approval_app import ApprovalStore, InMemoryApprovalStore
from fluxion.services.console_contracts import (
    ConsoleActor,
    CreateResourceDraftRequest,
    UpdateResourceDraftRequest,
)
from fluxion.services.console_governance import ConsoleGovernanceOps
from fluxion.services.console_payloads import (
    IssuedChatAccess,
    _capability_payload,
    platform_user_payload,
)
from fluxion.services.console_resources import ConsoleResourceOps
from fluxion.services.release_gate import ReleaseGateService
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
        release_gate: ReleaseGateService | None = None,
        release_gate_enforced: bool = False,
        credential_resolver: CredentialResolver | None = None,
        secret_store: SecretStore | None = None,
        credential_projection_reader: CredentialProjectionReader | None = None,
    ) -> None:
        self._store = store
        self._trace_store = trace_store
        self._secret_metadata_store = secret_metadata_store
        self._approval_store = approval_store or InMemoryApprovalStore()
        # TASK-019 返工：连接测试凭据注入（Provider Authorization / MCP transport）。
        self._credential_resolver = credential_resolver
        # golden-path-closure TASK-009：明文 Secret 写入（Credential 创建 Journey）。
        self._secret_store = secret_store
        # FEAT-04：Credential Projection 查询接口注入；None 时按 store engine
        # 懒装配默认 Repository（双库同语义），保持旧装配点零改动。
        self._credential_projection_reader = credential_projection_reader
        self._workflow_validator = WorkflowDefinitionValidator(store)
        self._deployment_actions: list[str] = []
        # 只读运行时身份快照：由装配方（dev bundle）注入，避免 Console 反向依赖 Runtime。
        self._plugin_summaries = tuple(plugin_summaries)
        self._service_instance_id = service_instance_id or "console-standalone"
        # Phase 5 TASK-005：publish 管道 Release Gate（请求带 gate 参数时评估）。
        # review P1-7：enforced=True 时 gate 从 opt-in 变强制策略——不带 gate 参数
        # 的 publish fail-closed 阻断（生产装配必须开启；dev bundle 保持 False，
        # 既有发布流不受影响）。
        self._release_gate = release_gate
        self._release_gate_enforced = release_gate_enforced
        # 单进程内按资源串行化 publish/rollback/deprecate，保证 optimistic-lock
        # 的 check-then-commit 原子；多实例部署需依赖 DB 级串行化（如 advisory lock）。
        self._publication_locks: dict[tuple[str, ResourceKind, str], asyncio.Lock] = {}

    @property
    def store(self) -> ChannelRegistryStore:
        """Registry 只读入口（ADR-A010：API 层解析租户默认 RuntimeProfile 等场景）。"""
        return self._store

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
        keyword: str | None = None,
    ) -> tuple[list[PlatformUserRecord], int]:
        return await self._store.list_platform_users(
            tenant_id=actor.tenant_id,
            offset=(page - 1) * page_size,
            limit=page_size,
            keyword=keyword,
        )

    async def list_policies(
        self,
        actor: ConsoleActor,
        *,
        page: int,
        page_size: int,
        keyword: str | None = None,
        status: ResourceStatus | None = None,
    ) -> tuple[list[ResourceDefinition], int]:
        """列出 tenant 已注册的 Policy 资源（P1 Plugin/Hook Policy 视图只读数据源）。"""
        return await self._store.list_resources(
            kind=ResourceKind.POLICY,
            tenant_id=actor.tenant_id,
            offset=(page - 1) * page_size,
            limit=page_size,
            keyword=keyword,
            status=status,
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

    async def create_credential(
        self,
        actor: ConsoleActor,
        *,
        name: str,
        plaintext: str,
        purpose: str = "",
    ) -> ResourceDefinition:
        """golden-path-closure TASK-009：明文只写不回显——写 SecretStore 得 secret_ref，
        创建 Secret 元数据资源（spec 只保存 SecretRef，规则 17）。"""
        if self._secret_store is None:
            raise ConsoleError(
                VALIDATION_FAILED, "secret store is not configured for credential creation", 503
            )
        # SecretRef 的逻辑名必须与 Registry SECRET resource_id 一致：发布校验、
        # Binding 和运行期 Resolver 都以该逻辑 ID 关联资源。显示名只保留在 spec.name。
        # 逻辑 ID 由服务端生成唯一 ID（review-fixes S-01）：显示名独立，同名/并发
        # 创建不得覆盖既有 SecretRef——slug 前缀保可读，hex 后缀保证唯一。
        slug = re.sub(r"[^a-z0-9-]+", "-", name.strip().lower()).strip("-") or "cred"
        credential_id = f"{slug}-{uuid4().hex[:8]}"
        secret_ref = await self._secret_store.put(actor.tenant_id, credential_id, plaintext)
        return await self.create_resource_draft(
            actor,
            CreateResourceDraftRequest(
                tenant_id=actor.tenant_id,
                kind=ResourceKind.SECRET,
                resource_id=credential_id,
                version="1",
                visibility=ResourceVisibility.PRIVATE,
                spec={"name": name, "secret_ref": secret_ref, "purpose": purpose},
            ),
        )

    async def rotate_credential(
        self,
        actor: ConsoleActor,
        *,
        credential_id: str,
        plaintext: str,
    ) -> ResourceDefinition:
        """golden-path-closure TASK-009：轮换——store 层写入新版本 SecretRef，
        working draft spec 更新指向新 ref；旧版本 ref 保留（版本化，消费者按需
        重新 pin，ADR-A003 快照冻结语义不受影响）。明文不进 Registry。"""
        if self._secret_store is None:
            raise ConsoleError(
                VALIDATION_FAILED, "secret store is not configured for credential rotation", 503
            )
        # ensure_working_draft 兼容 draft（复用）与 published（fork 下一版），
        # 并在资源不存在时 fail-closed（store.get 无版本时只解析 published）。
        working = await self.ensure_working_draft(actor, ResourceKind.SECRET, credential_id)
        old_ref = str(working.spec_json.get("secret_ref", ""))
        new_ref = await self._secret_store.rotate(old_ref, plaintext)
        updated = await self.update_resource_draft(
            actor,
            UpdateResourceDraftRequest(
                tenant_id=actor.tenant_id,
                kind=ResourceKind.SECRET,
                resource_id=credential_id,
                version=working.version,
                spec={**working.spec_json, "secret_ref": new_ref},
            ),
        )
        await self._append_audit(
            actor,
            action="credential.rotate",
            target_type="secret",
            target_id=credential_id,
            before={"secret_ref": old_ref},
            after={"secret_ref": new_ref},
        )
        return updated

    async def disable_credential(
        self,
        actor: ConsoleActor,
        *,
        credential_id: str,
    ) -> ResourceDefinition:
        """golden-path-closure TASK-009：禁用——store 层 revoke（后续 resolve
        fail-closed `secret_revoked`），spec 标记 revoked 供 UI 呈现与治理。"""
        if self._secret_store is None:
            raise ConsoleError(
                VALIDATION_FAILED, "secret store is not configured for credential disabling", 503
            )
        working = await self.ensure_working_draft(actor, ResourceKind.SECRET, credential_id)
        secret_ref = str(working.spec_json.get("secret_ref", ""))
        await self._secret_store.revoke(secret_ref)
        updated = await self.update_resource_draft(
            actor,
            UpdateResourceDraftRequest(
                tenant_id=actor.tenant_id,
                kind=ResourceKind.SECRET,
                resource_id=credential_id,
                version=working.version,
                spec={**working.spec_json, "revoked": True},
            ),
        )
        await self._append_audit(
            actor,
            action="credential.disable",
            target_type="secret",
            target_id=credential_id,
            before={"revoked": False},
            after={"revoked": True},
        )
        return updated

    async def list_runs(
        self,
        actor: ConsoleActor,
        *,
        page: int,
        page_size: int,
        status: str | None = None,
        keyword: str | None = None,
    ) -> tuple[list[TraceRecord], int]:
        if self._trace_store is None:
            return [], 0
        return await self._trace_store.list_recent(
            tenant_id=actor.tenant_id,
            offset=(page - 1) * page_size,
            limit=page_size,
            status=status,
            keyword=keyword,
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
        action: str | None = None,
        actor_id: str | None = None,
        target_type: str | None = None,
        created_from: str | None = None,
        created_to: str | None = None,
    ) -> tuple[list[AuditRecord], int]:
        # TASK-021（§8.10）：审计过滤透传（SQL 下推，时间范围不做前端全量过滤）
        return await self._store.list_audit(
            tenant_id=actor.tenant_id,
            action=action,
            actor_id=actor_id,
            target_type=target_type,
            created_from=created_from,
            created_to=created_to,
            offset=(page - 1) * page_size,
            limit=page_size,
        )

    async def issue_chat_access(
        self,
        actor: ConsoleActor,
        *,
        platform_user_id: str,
        agent_id: str,
    ) -> IssuedChatAccess:
        user = await self._store.get_platform_user(
            tenant_id=actor.tenant_id,
            platform_user_id=platform_user_id,
        )
        if user is None:
            raise ConsoleResourceNotFoundError("platform user not found")
        agent = await self._store.get(
            ResourceKind.AGENT_DEFINITION,
            agent_id,
            tenant_id=actor.tenant_id,
        )
        if agent is None or agent.status is not ResourceStatus.PUBLISHED:
            raise ConsoleError(
                CHANNEL_AGENT_NOT_FOUND,
                f"agent_not_found: {agent_id}",
                404,
            )
        # TASK-A105：product routing key = agent_id；发行即校验存在且 PUBLISHED
        # （BE-E-05 前置 404 agent_not_found），不再允许悬空引用延迟到运行期。
        token = secrets.token_urlsafe(32)
        record = ChatAccessRecord(
            access_id=f"chat_access_{uuid4().hex}",
            tenant_id=actor.tenant_id,
            platform_user_id=platform_user_id,
            agent_id=agent_id,
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

    async def list_user_chat_access(
        self,
        actor: ConsoleActor,
        *,
        platform_user_id: str,
    ) -> list[ChatAccessRecord]:
        """按用户列出未撤销对话链接（只读，供用户详情链接 Tab）。"""
        user = await self._store.get_platform_user(
            tenant_id=actor.tenant_id,
            platform_user_id=platform_user_id,
        )
        if user is None:
            raise ConsoleResourceNotFoundError("platform user not found")
        return await self._store.list_chat_access(
            tenant_id=actor.tenant_id,
            platform_user_id=platform_user_id,
        )

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
        "agent_id": record.agent_id,
        "revoked": record.revoked_at is not None,
    }
