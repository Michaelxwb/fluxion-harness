"""投递正文（design §3.5.9 / FEAT-04）：最终结果与 Human 问题各一套模板。

渠道适配器只发送文本，因此正文必须自带用户能读懂的事实：

- COMPLETED：优先用结果里的 `summary/text/message/answer` 字段；BATCH 聚合结果给出
  成功/失败/取消计数；其它结构化结果给截断后的紧凑 JSON；大结果附 Artifact 引用；
- FAILED：错误码 + 截断后的错误摘要（deadline 命中给明确说明）；
- CANCELLED：说明已取消；
- HUMAN_QUESTION：任务名、问题、截止时间与回复方式。

文案按 `default_locale` 取 zh-CN / en-US 两套模板（RULE-i18n-001），不含任何密钥、内部栈
或 UUID——任务名由 Schedule 名称（立即任务取提醒正文）表达，执行行本身没有可读名称。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from muad_contracts import AttachmentRef, DeliveryMessage, TaskStatus

from ..infrastructure.models.task import TaskExecution

MAX_RESULT_CHARS = 1500
MAX_ERROR_CHARS = 300
MAX_TITLE_CHARS = 120
SUMMARY_KEYS = ("summary", "text", "message", "answer")
BATCH_KEYS = ("total", "succeeded", "failed", "cancelled")
DEADLINE_CODE = "TASK_DEADLINE_EXCEEDED"
REMINDER_TEXT_KEY = "reminder_text"

TEMPLATES: dict[str, dict[str, str]] = {
    "zh-CN": {
        "fallback_title": "后台任务",
        "completed": "后台任务已完成：{title}",
        "batch": "共 {total} 项：成功 {succeeded}，失败 {failed}，取消 {cancelled}",
        "artifact": "完整结果见附件：{artifact_id}",
        "failed": "后台任务失败：{title}\n原因：{code} {message}",
        "deadline": "后台任务超过截止时间未完成：{title}",
        "cancelled": "后台任务已取消：{title}",
        "question": (
            "任务「{title}」需要你的回复：\n{question}\n"
            "请在 {deadline} 前直接回复本消息；同时有多个待处理任务时请注明任务名。"
        ),
    },
    "en-US": {
        "fallback_title": "Background task",
        "completed": "Background task completed: {title}",
        "batch": "{total} items: {succeeded} succeeded, {failed} failed, {cancelled} cancelled",
        "artifact": "Full result attached: {artifact_id}",
        "failed": "Background task failed: {title}\nReason: {code} {message}",
        "deadline": "Background task missed its deadline: {title}",
        "cancelled": "Background task cancelled: {title}",
        "question": (
            'Task "{title}" needs your reply:\n{question}\n'
            "Reply to this message before {deadline}; if several tasks are waiting, name the task."
        ),
    },
}


def _texts(locale: str) -> Mapping[str, str]:
    return TEMPLATES.get(locale, TEMPLATES["zh-CN"])


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _result_summary(result: Mapping[str, Any] | None, texts: Mapping[str, str]) -> str | None:
    if not result:
        return None
    for key in SUMMARY_KEYS:
        value = result.get(key)
        if isinstance(value, str) and value.strip():
            return _truncate(value.strip(), MAX_RESULT_CHARS)
    if all(isinstance(result.get(key), int) for key in BATCH_KEYS):
        return texts["batch"].format(**{key: result[key] for key in BATCH_KEYS})
    compact = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return _truncate(compact, MAX_RESULT_CHARS)


def task_title(task: TaskExecution, schedule_name: str | None, locale: str) -> str:
    """投递正文里的任务名：Schedule 名称优先，立即任务退回冻结的提醒正文。"""
    texts = _texts(locale)
    if schedule_name and schedule_name.strip():
        return _truncate(schedule_name.strip(), MAX_TITLE_CHARS)
    payload = task.input_json if isinstance(task.input_json, Mapping) else {}
    reminder = payload.get(REMINDER_TEXT_KEY)
    if isinstance(reminder, str) and reminder.strip():
        return _truncate(reminder.strip(), MAX_TITLE_CHARS)
    return texts["fallback_title"]


def _completed_text(task: TaskExecution, title: str, texts: Mapping[str, str]) -> str:
    lines = [texts["completed"].format(title=title)]
    summary = _result_summary(task.result_json, texts)
    if summary:
        lines.append(summary)
    if task.result_artifact_id is not None:
        lines.append(texts["artifact"].format(artifact_id=task.result_artifact_id))
    return "\n".join(lines)


def build_delivery_message(
    task: TaskExecution,
    locale: str,
    *,
    title: str,
    artifact: AttachmentRef | None = None,
) -> DeliveryMessage:
    texts = _texts(locale)
    if task.status == str(TaskStatus.COMPLETED):
        if artifact is not None:
            # 有产物就**发产物**，不再给用户一串 UUID（FEAT-09 的硬要求）。
            # 形态由渠道中立的 `kind` 决定，渠道怎么发是适配器的事。
            return DeliveryMessage(
                type="image" if artifact.kind == "IMAGE" else "artifact",
                artifact=artifact,
            )
        return DeliveryMessage(text=_completed_text(task, title, texts))
    if task.status == str(TaskStatus.FAILED):
        if task.error_code == DEADLINE_CODE:
            return DeliveryMessage(text=texts["deadline"].format(title=title))
        message = _truncate(task.error_message or "", MAX_ERROR_CHARS)
        text = texts["failed"].format(
            title=title, code=task.error_code or "UNKNOWN", message=message
        )
        return DeliveryMessage(text=text.strip())
    return DeliveryMessage(text=texts["cancelled"].format(title=title))


def build_human_question_message(
    *,
    question: str,
    title: str,
    deadline_at: datetime,
    locale: str,
) -> DeliveryMessage:
    """Human 问题正文：任务名、问题、截止时间与回复方式（design §3.5.9）。"""
    return DeliveryMessage(
        text=_texts(locale)["question"].format(
            title=title,
            question=_truncate(question.strip(), MAX_RESULT_CHARS),
            deadline=deadline_at.isoformat(),
        )
    )


__all__ = ["build_delivery_message", "build_human_question_message", "task_title"]
