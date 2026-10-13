"""Bounded deterministic Runtime choices; no model-produced numbering or root."""

from collections.abc import Iterable
from datetime import datetime, timedelta
from uuid import UUID

from muad_agent_core.memory.rank import MemoryRecord
from muad_agent_core.memory.resume import resume_candidate_hash
from muad_agent_core.memory.tokens import lexical_coverage
from muad_contracts.memory import (
    MemoryResumeOption,
    MemoryResumeOptions,
    MemoryResumeOptionsBody,
    TaskStateContent,
    memory_hash,
)


def task_candidates(records: Iterable[MemoryRecord], theme: str | None) -> tuple[MemoryRecord, ...]:
    scored = []
    for row in records:
        if not isinstance(row.content, TaskStateContent):
            continue
        score = lexical_coverage(theme, row.content.title + "\n" + row.content.goal) if theme else 1.0
        if score > 0:
            scored.append((row, score))
    scored.sort(key=lambda pair: (-pair[1], -pair[0].updated_at.timestamp(), pair[0].memory_key))
    return tuple(row for row, _ in scored)


def build_options(
    records: tuple[MemoryRecord, ...],
    *,
    user_id: UUID,
    source_user_event_id: UUID,
    theme: str | None,
    now: datetime,
) -> MemoryResumeOptions:
    items = []
    for number, row in enumerate(records[:20], 1):
        if not isinstance(row.content, TaskStateContent):
            raise ValueError("resume options only accept task state")
        items.append(
            MemoryResumeOption(
                number=number,
                memory_key=row.memory_key,
                version=row.version,
                root_conversation_id=row.content.root_conversation_id,
                title=row.content.title,
                candidate_hash=resume_candidate_hash(row),
            )
        )
    body = MemoryResumeOptionsBody(
        user_id=user_id,
        source_user_event_id=source_user_event_id,
        theme=theme,
        items=items,
        expires_at=now + timedelta(seconds=300),
    )
    payload = body.model_dump(mode="json")
    return MemoryResumeOptions.model_validate({**payload, "options_hash": memory_hash(payload)})


def options_prompt(options: MemoryResumeOptions) -> str:
    lines = [
        "以下为会话提炼的未确认任务线索，请核对；执行前需要核验真实任务、现有授权和待办有效性。",
        "请回复完整编号（如“第2个”）、完整 key 或唯一标题；清单有效期为300秒。",
    ]
    lines.extend(f"{row.number}. {row.title}（{row.memory_key}，版本{row.version}）" for row in options.items)
    lines.append("若清单中没有目标，请回复“继续<具体主题>任务”；最多显示20条线索。")
    return "\n".join(lines)


def valid_option(option: MemoryResumeOption, records: Iterable[MemoryRecord]) -> MemoryRecord | None:
    return next(
        (
            row
            for row in records
            if row.memory_key == option.memory_key
            and row.version == option.version
            and resume_candidate_hash(row) == option.candidate_hash
        ),
        None,
    )
