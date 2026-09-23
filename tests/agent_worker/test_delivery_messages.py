"""Final Delivery 正文：投递的是最终结果而不是一句「已完成」（FEAT-04 / RULE-i18n-001）。"""

from __future__ import annotations

import uuid

from muad_agent_worker.delivery.messages import MAX_RESULT_CHARS, build_delivery_message
from muad_agent_worker.infrastructure.models.task import TaskExecution


def _task(**values: object) -> TaskExecution:
    defaults: dict[str, object] = {"intent_key": "policy_check", "status": "COMPLETED"}
    defaults.update(values)
    return TaskExecution(**defaults)


def test_completed_message_carries_result_summary() -> None:
    text = build_delivery_message(_task(result_json={"summary": "A 客户策略合规"}), "zh-CN").text
    assert text == "后台任务已完成：policy_check\nA 客户策略合规"


def test_completed_structured_result_is_compacted_and_truncated() -> None:
    text = build_delivery_message(_task(result_json={"rows": ["x" * 5000]}), "en-US").text
    assert text.startswith("Background task completed: policy_check\n{\"rows\"")
    assert len(text.splitlines()[1]) == MAX_RESULT_CHARS


def test_batch_result_reports_counts_and_artifact_reference() -> None:
    artifact = uuid.uuid4()
    text = build_delivery_message(
        _task(
            result_json={"mode": "ALL", "total": 20, "succeeded": 18, "failed": 2, "cancelled": 0},
            result_artifact_id=artifact,
        ),
        "zh-CN",
    ).text
    assert "共 20 项：成功 18，失败 2，取消 0" in text
    assert str(artifact) in text


def test_failed_and_deadline_and_cancelled_messages() -> None:
    failed = build_delivery_message(
        _task(status="FAILED", error_code="SKILL_EXECUTION_FAILED", error_message="boom"), "zh-CN"
    ).text
    assert failed == "后台任务失败：policy_check\n原因：SKILL_EXECUTION_FAILED boom"
    deadline = build_delivery_message(
        _task(status="FAILED", error_code="TASK_DEADLINE_EXCEEDED"), "en-US"
    ).text
    assert deadline == "Background task missed its deadline: policy_check"
    cancelled = build_delivery_message(_task(status="CANCELLED"), "fr-FR").text
    assert cancelled == "后台任务已取消：policy_check", "未知 locale 回落 zh-CN"
