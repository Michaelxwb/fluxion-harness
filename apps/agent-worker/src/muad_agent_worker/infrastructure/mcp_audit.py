"""Worker 侧 MCP 逻辑审计：与 Runtime 共用 `runtime.egress_audit` 口径。

`McpClient` 只产出脱敏后的逻辑事实（server/tool、HTTP 状态、错误码、耗时），
本模块负责把它们写进既有 egress 审计表；认证头与工具正文不会进入这里。
"""

from __future__ import annotations

import asyncio

import sqlalchemy as sa
from muad_logging import get_logger
from muad_platform_sdk.egress_audit_store import EgressAuditIdentity
from muad_platform_sdk.mcp_audit import McpAuditEvent
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = get_logger(__name__)

#: 审计写失败不阻断业务调用；超时有界，避免坏库拖住整个执行。
MCP_AUDIT_TIMEOUT_SEC = 5.0

_INSERT = sa.text(
    """
    INSERT INTO runtime.egress_audit
      (tenant_id, run_id, task_id, user_id, target_type, target, operation, method,
       policy_decision, status_code, result_status, latency_ms, error_code, attempt_id,
       phase, business_trace_id)
    VALUES
      (:tenant, :run, :task, :user, 'MCP', :target, 'mcp.tool', 'POST',
       'ALLOW', :status, :result, :latency, :error, :attempt, 'completed',
       CAST(:business_trace_id AS uuid))
    ON CONFLICT (tenant_id, attempt_id, phase) DO NOTHING
    """
)


class DatabaseMcpAudit:
    """把 `McpAuditEvent` 写进 `runtime.egress_audit`（Task 归属，run_id 为空）。"""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        identity: EgressAuditIdentity,
        *,
        timeout_seconds: float = MCP_AUDIT_TIMEOUT_SEC,
    ) -> None:
        self._sessions = sessions
        self._identity = identity
        self._timeout = timeout_seconds

    async def record(self, event: McpAuditEvent) -> None:
        identity = self._identity
        try:
            async with asyncio.timeout(self._timeout):
                async with self._sessions.begin() as session:
                    await session.execute(
                        _INSERT,
                        {
                            "tenant": identity.tenant_id,
                            "run": identity.run_id,
                            "task": identity.task_id,
                            "user": identity.user_id,
                            "target": f"mcp://{event.server_key}/{event.tool_name}",
                            "status": event.http_status,
                            "result": event.result_status,
                            "latency": event.latency_ms,
                            "error": event.error_code,
                            "attempt": event.attempt_id,
                            "business_trace_id": event.business_trace_id,
                        },
                    )
        except Exception:  # noqa: BLE001 - 审计降级不阻断工具调用，但必须留痕
            logger.warning(
                "mcp_audit_write_failed",
                extra={
                    "tenant_id": identity.tenant_id,
                    "task_id": str(identity.task_id) if identity.task_id else None,
                    "server": event.server_key,
                    "tool": event.tool_name,
                    "attempt_id": event.attempt_id,
                },
            )


__all__ = ["DatabaseMcpAudit", "MCP_AUDIT_TIMEOUT_SEC"]
