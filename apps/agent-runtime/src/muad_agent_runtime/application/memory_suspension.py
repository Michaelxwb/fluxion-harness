"""Runtime 的记忆确认挂起：中立挂起协议的一种实现。

Kernel 只读 `SuspensionRequest` 的中立字段；`proposal` 是 Runtime 自己的恢复载荷，留在
适配器这一侧（Kernel 不认识 Memory，也不该认识）。
"""

from __future__ import annotations

from dataclasses import dataclass

from muad_agent_core.agent.suspension import SuspensionRequest
from muad_contracts.memory import MemoryConfirmationProposal

#: 中断事件上的宿主分类；Kernel 原样透传，不解释。
MEMORY_CONFIRMATION_REASON = "MEMORY_CONFIRMATION"


@dataclass(frozen=True, slots=True, kw_only=True)
class MemorySuspendRequest(SuspensionRequest):
    """记忆确认：整模型回合边界的人类挂起 + 待确认提议。"""

    proposal: MemoryConfirmationProposal


__all__ = ["MEMORY_CONFIRMATION_REASON", "MemorySuspendRequest"]
