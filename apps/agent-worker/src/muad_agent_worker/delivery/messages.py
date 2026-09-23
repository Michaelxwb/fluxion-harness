"""Final Delivery 消息正文（设计 FEAT-04「主动投递最终结果」）。

渠道适配器只发送文本，因此最终结果摘要必须进入正文：
- COMPLETED：优先用结果里的 `summary/text/message/answer` 字段；BATCH 聚合结果
  给出成功/失败/取消计数；其它结构化结果给截断后的紧凑 JSON；大结果附 Artifact 引用；
- FAILED：错误码 + 截断后的错误摘要（deadline 命中给明确说明）；
- CANCELLED：说明已取消。

文案按 `default_locale` 取 zh-CN / en-US 两套模板（RULE-i18n-001），不含任何密钥。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from muad_contracts import DeliveryMessage, TaskStatus

from ..infrastructure.models.task import TaskExecution

MAX_RESULT_CHARS = 1500
MAX_ERROR_CHARS = 300
SUMMARY_KEYS = ("summary", "text", "message", "answer")
BATCH_KEYS = ("total", "succeeded", "failed", "cancelled")
DEADLINE_CODE = "TASK_DEADLINE_EXCEEDED"

TEMPLATES: dict[str, dict[str, str]] = {
    "zh-CN": {
        "completed": "后台任务已完成：{intent}",
        "batch": "共 {total} 项：成功 {succeeded}，失败 {failed}，取消 {cancelled}",
        "artifact": "完整结果见附件：{artifact_id}",
        "failed": "后台任务失败：{intent}\n原因：{code} {message}",
        "deadline": "后台任务超过截止时间未完成：{intent}",
        "cancelled": "后台任务已取消：{intent}",
    },
    "en-US": {
        "completed": "Background task completed: {intent}",
        "batch": "{total} items: {succeeded} succeeded, {failed} failed, {cancelled} cancelled",
        "artifact": "Full result attached: {artifact_id}",
        "failed": "Background task failed: {intent}\nReason: {code} {message}",
        "deadline": "Background task missed its deadline: {intent}",
        "cancelled": "Background task cancelled: {intent}",
    },
}


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


def _completed_text(task: TaskExecution, texts: Mapping[str, str]) -> str:
    lines = [texts["completed"].format(intent=task.intent_key)]
    summary = _result_summary(task.result_json, texts)
    if summary:
        lines.append(summary)
    if task.result_artifact_id is not None:
        lines.append(texts["artifact"].format(artifact_id=task.result_artifact_id))
    return "\n".join(lines)


def build_delivery_message(task: TaskExecution, locale: str) -> DeliveryMessage:
    texts = TEMPLATES.get(locale, TEMPLATES["zh-CN"])
    if task.status == str(TaskStatus.COMPLETED):
        return DeliveryMessage(text=_completed_text(task, texts))
    if task.status == str(TaskStatus.FAILED):
        if task.error_code == DEADLINE_CODE:
            return DeliveryMessage(text=texts["deadline"].format(intent=task.intent_key))
        message = _truncate(task.error_message or "", MAX_ERROR_CHARS)
        text = texts["failed"].format(
            intent=task.intent_key, code=task.error_code or "UNKNOWN", message=message
        )
        return DeliveryMessage(text=text.strip())
    return DeliveryMessage(text=texts["cancelled"].format(intent=task.intent_key))


__all__ = ["build_delivery_message"]
