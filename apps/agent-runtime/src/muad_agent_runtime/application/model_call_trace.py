"""Exact event → model audit linkage and reply-generation ledger, scoped to one runner execution."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from .run_events import FIRST_REPLY_GENERATION


@dataclass(frozen=True, slots=True)
class ModelRound:
    """一次 `model.started` 开启的模型回合：审计关联字段 + 回复生成代号。

    `supersedes=True` 表示上一回合在**已产生正文之后**失败：本回合是显式开启的新代，
    调用方必须先落一条 `message.snapshot` 替换边界，再继续产出本代 delta（design §3.6）。
    """

    generation: int
    supersedes: bool
    event_fields: dict[str, str] = field(default_factory=dict)


class ModelCallTrace:
    """一次 runner 执行内的模型审计关联与回复生成代号账本。

    **审计关联**：一个模型回合可能因为资源池的轮换竞争发生**多次尝试**（预占 → 解析 Key →
    发上游）：`begin()` 铸出回合 id，`begin_attempt()` 为每次尝试认领一个审计行 id——第一次尝试
    复用回合 id（`model.started` 与最终审计行仍指向同一次尝试），后续尝试各铸新 id，保证审计行
    主键不冲突且逐 attempt 可追溯。

    **回复生成代号**（TASK-062 / design §3.6）：模型调用在已产生部分正文后失败时，重试不得把
    另一 attempt 的正文拼到旧正文上——失败尝试把事实记入账本（`note_failure_after_output`），
    下一次 `begin()` 由此**显式分代**（`generation += 1`，`supersedes=True`），调用方以持久
    snapshot 整组替换旧代后再继续 delta。回合之外（或回合已收尾）到达的输出一律被 fence
    （`accepts_output` 为 False），不进入任何一代正文。

    账本与审计**解耦**：`audit_enabled=False` 的调用点只使用分代账本，事件里不冒充审计身份
    （`event_fields` 返回空）。
    """

    def __init__(self, *, audit_enabled: bool = True) -> None:
        self._audit_enabled = audit_enabled
        self.audit_id: uuid.UUID | None = None
        self._attempt_claimed = False
        self._generation = FIRST_REPLY_GENERATION
        self._round_open = False
        self._round_emitted = False
        self._round_failed_after_output = False

    def begin(self) -> ModelRound:
        """开启一个模型回合；上一回合"已产生正文后失败"时在此显式分代。"""
        supersedes = self._round_failed_after_output
        if supersedes:
            self._generation += 1
        self._round_failed_after_output = False
        self._round_emitted = False
        self._round_open = True
        self.audit_id = uuid.uuid4()
        self._attempt_claimed = False
        return ModelRound(
            generation=self._generation, supersedes=supersedes, event_fields=self.event_fields()
        )

    def begin_attempt(self) -> uuid.UUID:
        """一次资源池尝试（预占 → 解析 → 发上游）的审计行身份。"""
        if self.audit_id is None or self._attempt_claimed:
            self.audit_id = uuid.uuid4()
        self._attempt_claimed = True
        return self.audit_id

    def event_fields(self) -> dict[str, str]:
        if not self._audit_enabled or self.audit_id is None:
            return {}
        return {"model_audit_id": str(self.audit_id)}

    @property
    def reply_generation(self) -> int:
        return self._generation

    def note_delta(self) -> None:
        """本回合已产生正文（分代判定的事实依据）。"""
        self._round_emitted = True

    def close_round(self) -> None:
        """回合收尾（成功或失败）：此后到达的迟到输出被 fence。"""
        self._round_open = False

    def note_failure_after_output(self) -> None:
        """一次上游尝试在**已产生正文之后**失败：下一次 `begin` 必须显式分代（design §3.6）。"""
        if self._round_emitted:
            self._round_failed_after_output = True

    def accepts_output(self) -> bool:
        """当前回合是否仍接受正文输出；回合外/被取代的迟到输出返回 False（被 fence）。"""
        return self._round_open
