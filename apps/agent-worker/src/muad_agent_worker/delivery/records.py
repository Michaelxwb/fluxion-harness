"""投递记录的身份：种类、状态与稳定键（design §3.2.11 / §3.3.10 / §3.5.9）。

`task_delivery` + `task_delivery_attempt` 是投递的**唯一状态源**；Execution 行只表达业务
终态，不持有任何投递列。稳定键由本模块统一构造，入队方（终态事务、立即任务创建、投递循环
的 Human 问题发现）共用同一份口径——键就是幂等身份，两处拼法不同就会发两次。
"""

from __future__ import annotations

import uuid
from enum import StrEnum


class DeliveryKind(StrEnum):
    """`task_delivery.delivery_kind`：最终结果与人工问题分开记账。"""

    PUSH = "PUSH"
    HUMAN_QUESTION = "HUMAN_QUESTION"


class DeliveryState(StrEnum):
    """`task_delivery.status`：PENDING 可被投递；SENT 只在接收方明确确认后写入。"""

    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


#: `execution:{execution_id}:final`：一条 Execution 的最终结果只有一条投递。
EXECUTION_DELIVERY_KEY = "execution:{execution_id}:final"
#: `human:{interaction_id}`：一个 Human 问题只有一条投递（重投不新增未读）。
HUMAN_DELIVERY_KEY = "human:{interaction_id}"
HUMAN_KEY_PREFIX = "human:"


def execution_delivery_key(execution_id: uuid.UUID) -> str:
    return EXECUTION_DELIVERY_KEY.format(execution_id=execution_id)


def human_delivery_key(interaction_id: uuid.UUID) -> str:
    return HUMAN_DELIVERY_KEY.format(interaction_id=interaction_id)


def interaction_id_from_key(delivery_key: str) -> uuid.UUID | None:
    """从 Human 投递键取 interaction id；不是这个形状（最终结果键）就返回 None。

    键的形状归本模块所有：读取方只按这里的解析走，不在别处再拆一次字符串。
    """
    if not delivery_key.startswith(HUMAN_KEY_PREFIX):
        return None
    try:
        return uuid.UUID(delivery_key.removeprefix(HUMAN_KEY_PREFIX))
    except ValueError:
        return None


__all__ = [
    "DeliveryKind",
    "DeliveryState",
    "EXECUTION_DELIVERY_KEY",
    "HUMAN_DELIVERY_KEY",
    "HUMAN_KEY_PREFIX",
    "execution_delivery_key",
    "human_delivery_key",
    "interaction_id_from_key",
]
