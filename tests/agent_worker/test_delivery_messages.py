"""投递正文：投递的是最终结果/问题而不是一句「已完成」（FEAT-04 / RULE-i18n-001）。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from muad_agent_worker.delivery.messages import (
    MAX_RESULT_CHARS,
    build_delivery_message,
    build_human_question_message,
    task_title,
)
from muad_agent_worker.infrastructure.models.task import TaskExecution


def _task(**values: object) -> TaskExecution:
    defaults: dict[str, object] = {"status": "COMPLETED", "input_json": {}}
    defaults.update(values)
    return TaskExecution(**defaults)


def test_completed_message_carries_result_summary() -> None:
    text = build_delivery_message(
        _task(result_json={"summary": "A 客户策略合规"}), "zh-CN", title="A 客户周报"
    ).text
    assert text == "后台任务已完成：A 客户周报\nA 客户策略合规"


def test_completed_structured_result_is_compacted_and_truncated() -> None:
    text = build_delivery_message(
        _task(result_json={"rows": ["x" * 5000]}), "en-US", title="Weekly report"
    ).text
    assert text.startswith("Background task completed: Weekly report\n{\"rows\"")
    assert len(text.splitlines()[1]) == MAX_RESULT_CHARS


def test_batch_result_reports_counts_and_artifact_reference() -> None:
    artifact = uuid.uuid4()
    text = build_delivery_message(
        _task(
            result_json={"mode": "ALL", "total": 20, "succeeded": 18, "failed": 2, "cancelled": 0},
            result_artifact_id=artifact,
        ),
        "zh-CN",
        title="A 客户周报",
    ).text
    assert "共 20 项：成功 18，失败 2，取消 0" in text
    assert str(artifact) in text


def test_failed_and_deadline_and_cancelled_messages() -> None:
    failed = build_delivery_message(
        _task(status="FAILED", error_code="SKILL_EXECUTION_FAILED", error_message="boom"),
        "zh-CN",
        title="A 客户周报",
    ).text
    assert failed == "后台任务失败：A 客户周报\n原因：SKILL_EXECUTION_FAILED boom"
    deadline = build_delivery_message(
        _task(status="FAILED", error_code="TASK_DEADLINE_EXCEEDED"), "en-US", title="Weekly report"
    ).text
    assert deadline == "Background task missed its deadline: Weekly report"
    cancelled = build_delivery_message(
        _task(status="CANCELLED"), "fr-FR", title="A 客户周报"
    ).text
    assert cancelled == "后台任务已取消：A 客户周报", "未知 locale 回落 zh-CN"


def test_task_title_prefers_schedule_name_then_reminder_text() -> None:
    assert task_title(_task(), "A 客户周报", "zh-CN") == "A 客户周报"
    assert (
        task_title(_task(input_json={"reminder_text": "喝水"}), None, "zh-CN") == "喝水"
    ), "立即任务的提醒正文就是它的可读名字"
    assert task_title(_task(), None, "en-US") == "Background task"


def test_human_question_message_carries_name_question_deadline_and_reply_hint() -> None:
    deadline = datetime(2026, 11, 16, 10, 0, tzinfo=UTC)
    text = build_human_question_message(
        question="请选择客户", title="A 客户周报", deadline_at=deadline, locale="zh-CN"
    ).text
    assert "A 客户周报" in text
    assert "请选择客户" in text
    assert deadline.isoformat() in text
    assert "直接回复本消息" in text
    assert "message_id" not in text and "secret" not in text.lower()
