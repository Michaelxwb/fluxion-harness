"""AuditHookPlugin 示例（105 P1-02 / TASK-006）。

8 个固定 Hook Point 的审计记录插件：安装后（entry_points ``fluxion.hooks``）
每次执行在 tool 调用前后各留一条审计记录，其余 6 点记录执行生命周期。

使用（pip 包内声明 entry point，Runtime 启动自动安装，无需改核心代码）::

    [project.entry-points."fluxion.hooks"]
    audit-hook = "audit_hook:plugin"

设计约束（与生产 Hook 一致）：
- payload 全部 frozen 只读：只观测记录，不篡改、不返回业务值；
- ``fail_policy=FAIL_OPEN``：审计失败只记录，不阻断业务；
- 记录经 ``trace_sink``（context）进 trace ``hook.completed`` 事件，
  不另建存储（无状态，守住 Runtime 无状态）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fluxion.kernel.events import (
    AfterExecutionPayload,
    AfterModelCallPayload,
    AfterToolCallPayload,
    BeforeExecutionPayload,
    BeforeModelCallPayload,
    BeforeToolCallPayload,
    FailPolicy,
    HookRegistration,
    OnExecutionCancelledPayload,
    OnExecutionErrorPayload,
)
from fluxion.plugins.contracts import (
    PluginContext,
    PluginManifest,
    PluginType,
    TrustLevel,
)


@dataclass
class AuditRecord:
    """单条审计记录：点位＋执行关联 ID＋摘要（无敏感参数原文）。"""

    point: str
    tenant_id: str
    execution_id: str
    trace_id: str
    summary: str


@dataclass
class AuditHookPlugin:
    """审计 Hook 插件：8 点位各一条记录（内存列表，测试/演示用）。"""

    records: list[AuditRecord] = field(default_factory=list)

    @property
    def manifest(self) -> PluginManifest:
        return PluginManifest(
            plugin_id="audit-hook",
            version="1",
            plugin_type=PluginType.HOOK,
            entrypoint="audit_hook:plugin",
            trust_level=TrustLevel.TRUSTED,
            permissions=[],
            dependencies=[],
            compatibility={},
        )

    async def setup(self, _ctx: PluginContext) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    def hook_registrations(self) -> list[HookRegistration]:  # type: ignore[type-arg]
        return [
            self._registration(
                "audit-before-execution", BeforeExecutionPayload, self._on_before_execution
            ),
            self._registration(
                "audit-after-execution", AfterExecutionPayload, self._on_after_execution
            ),
            self._registration(
                "audit-before-model", BeforeModelCallPayload, self._on_before_model
            ),
            self._registration(
                "audit-after-model", AfterModelCallPayload, self._on_after_model
            ),
            self._registration(
                "audit-before-tool", BeforeToolCallPayload, self._on_before_tool
            ),
            self._registration(
                "audit-after-tool", AfterToolCallPayload, self._on_after_tool
            ),
            self._registration(
                "audit-on-error", OnExecutionErrorPayload, self._on_error
            ),
            self._registration(
                "audit-on-cancelled", OnExecutionCancelledPayload, self._on_cancelled
            ),
        ]

    @staticmethod
    def _registration(
        registration_id: str, event_type: type, handler: object
    ) -> HookRegistration:  # type: ignore[type-arg]
        return HookRegistration(
            registration_id=registration_id,
            event_type=event_type,
            priority=100,
            timeout_ms=1000,
            fail_policy=FailPolicy.FAIL_OPEN,
            handler=handler,  # type: ignore[arg-type]
        )

    def _record(
        self, point: str, tenant_id: str, execution_id: str, trace_id: str, summary: str
    ) -> None:
        self.records.append(
            AuditRecord(
                point=point,
                tenant_id=tenant_id,
                execution_id=execution_id,
                trace_id=trace_id,
                summary=summary,
            )
        )

    async def _on_before_execution(self, payload: BeforeExecutionPayload) -> None:
        self._record(
            "before_execution",
            payload.tenant_id,
            payload.execution_id,
            payload.trace_id,
            f"agent={payload.agent_id} profile={payload.runtime_profile_id}",
        )

    async def _on_after_execution(self, payload: AfterExecutionPayload) -> None:
        self._record(
            "after_execution",
            payload.tenant_id,
            payload.execution_id,
            payload.trace_id,
            f"agent={payload.agent_id} terminal={payload.terminal_state}",
        )

    async def _on_before_model(self, payload: BeforeModelCallPayload) -> None:
        self._record(
            "before_model",
            payload.tenant_id,
            payload.execution_id,
            payload.trace_id,
            f"provider={payload.provider_id}",
        )

    async def _on_after_model(self, payload: AfterModelCallPayload) -> None:
        self._record(
            "after_model",
            payload.tenant_id,
            payload.execution_id,
            payload.trace_id,
            f"provider={payload.provider_id} chars={payload.output_chars}",
        )

    async def _on_before_tool(self, payload: BeforeToolCallPayload) -> None:
        # 审计只记 tool_id，不记 arguments 原文（防敏感参数落盘）。
        self._record(
            "before_tool",
            payload.tenant_id,
            payload.execution_id,
            payload.trace_id,
            f"tool={payload.tool_id}",
        )

    async def _on_after_tool(self, payload: AfterToolCallPayload) -> None:
        self._record(
            "after_tool",
            payload.tenant_id,
            payload.execution_id,
            payload.trace_id,
            f"tool={payload.tool_id} status={payload.status}",
        )

    async def _on_error(self, payload: OnExecutionErrorPayload) -> None:
        self._record(
            "on_error",
            payload.tenant_id,
            payload.execution_id,
            payload.trace_id,
            f"code={payload.error_code}",
        )

    async def _on_cancelled(self, payload: OnExecutionCancelledPayload) -> None:
        self._record(
            "on_cancelled",
            payload.tenant_id,
            payload.execution_id,
            payload.trace_id,
            f"reason={payload.reason}",
        )


plugin = AuditHookPlugin()
