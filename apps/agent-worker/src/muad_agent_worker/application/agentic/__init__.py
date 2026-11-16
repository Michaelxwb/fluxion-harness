"""Worker 的 Agentic 执行适配器：把冻结 Snapshot v4 接进中立 Kernel 的端口。

设计 §3.1.2 的判据是"一套 Agent 循环、两份适配器"：这里不实现第二个循环，只实现
Runtime 那份适配器的宿主端口——`ExecutionEventPort`（task_event + checkpoint + 等待判定）、
`ToolSuspensionPort`（异步工具 / Human 提问）、模型池与容量、工具目录、只读记忆、
压缩与 Task 产物。Kernel 自身只在 `packages/agent-core` 里，执行入口在
`muad_agent_worker.worker.agentic_executor`。
"""

from __future__ import annotations

from .snapshot import FrozenAgenticExecution, FrozenExecutionError, load_frozen_execution

__all__ = [
    "FrozenAgenticExecution",
    "FrozenExecutionError",
    "load_frozen_execution",
]
