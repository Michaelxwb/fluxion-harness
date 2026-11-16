"""只读记忆注入：把快照里冻结的那个块原样交给模型，运行期不再读、也不写。

- 定时任务只读长期记忆（`SnapshotMemory.mode=READ_ONLY`）：本模块**不注册任何记忆写工具**，
  也不触发提炼作业；
- 注入块在准入时冻结（`memory.block.rendered_text`），恢复/每轮都只用这一份——当前记忆内容
  变了也不影响本次执行（RULE-snapshot-001）；
- 读取失败 / 降级（`degraded`）⇒ 空块：不阻断任务，也不在运行期补读。
"""

from __future__ import annotations

from muad_agent_core.model import ModelMessage, ModelRole

MEMORY_HEADER = (
    "以下是与本用户相关的长期记忆（只读）。它们来自准入时冻结的快照，"
    "可能已与用户最新情况不同；仅供本次任务参考，不得据此声称掌握了用户的实时状态。"
)


def frozen_memory_message(rendered_text: str) -> ModelMessage | None:
    """冻结块 → 一条 SYSTEM 消息；空白块返回 None（不注入空壳）。"""
    text = rendered_text.strip()
    if not text:
        return None
    return ModelMessage(role=ModelRole.SYSTEM, content=f"{MEMORY_HEADER}\n\n{text}")


__all__ = ["MEMORY_HEADER", "frozen_memory_message"]
