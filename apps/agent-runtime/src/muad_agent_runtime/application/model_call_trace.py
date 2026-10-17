"""Exact event → model audit linkage, scoped to one runner execution."""

import uuid


class ModelCallTrace:
    """一次 runner 执行内的模型审计关联（事件里的 `model_audit_id` → 审计行主键）。

    一个模型回合可能因为资源池的轮换竞争发生**多次尝试**（预占 → 解析 Key → 发上游）：
    `begin()` 铸出回合 id，`begin_attempt()` 为每次尝试认领一个审计行 id——第一次尝试复用
    回合 id（`model.started` 与最终审计行仍指向同一次尝试），后续尝试各铸新 id，保证审计行
    主键不冲突且逐 attempt 可追溯。
    """

    def __init__(self) -> None:
        self.audit_id: uuid.UUID | None = None
        self._attempt_claimed = False

    def begin(self) -> dict[str, str]:
        self.audit_id = uuid.uuid4()
        self._attempt_claimed = False
        return self.event_fields()

    def begin_attempt(self) -> uuid.UUID:
        """一次资源池尝试（预占 → 解析 → 发上游）的审计行身份。"""
        if self.audit_id is None or self._attempt_claimed:
            self.audit_id = uuid.uuid4()
        self._attempt_claimed = True
        return self.audit_id

    def event_fields(self) -> dict[str, str]:
        return {"model_audit_id": str(self.audit_id)} if self.audit_id is not None else {}
