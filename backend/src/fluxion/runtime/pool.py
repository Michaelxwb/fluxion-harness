"""Runtime 计算池与实例术语锚点（TASK-006 / FEAT-05 / ADR-A001）。

规则 26/27 术语固定：
- `RuntimeInstance`：实际运行的 Runtime Pod/Process（可替换计算节点）；
- `RuntimePool`：共享无状态计算池（多个 RuntimeInstance 的集合，无 sticky）。

Console 运营侧此前用「Worker/队列」指代 Runtime 计算面，统一收敛到本概念。
这两个类仅是术语锚点，不承载任何运行时行为（Runtime 无状态，事实全外置 Registry）。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RuntimeInstance:
    """RuntimeInstance 锚点：一个可替换的 Runtime Pod/Process 标识。"""

    name: str


@dataclass(frozen=True, slots=True)
class RuntimePool:
    """RuntimePool 锚点：共享无状态计算池（RuntimeInstance 集合）。"""

    name: str = "default"
