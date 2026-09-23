"""B-130：Task/Schedule 中英文文案与帮助词条（设计 §3.1/§3.3.1/§3.7）。

两种 locale 键完全对应；状态/动作/确认/帮助文案准确；帮助不承诺 Console 编排能力；
错误目录覆盖 Task/Schedule 相关错误码。
"""

from __future__ import annotations

from typing import Any

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
LOCALES = ROOT / "apps/console-platform/frontend/src/locales"
MESSAGES = ROOT / "config/api-messages.yaml"

REQUIRED_KEYS = (
    "task.title",
    "task.help.action",
    "task.help.title",
    "task.help.content",
    "task.filter.status",
    "task.filter.triggerType",
    "task.filter.deadline",
    "task.columns.taskId",
    "task.columns.agent",
    "task.columns.intent",
    "task.columns.status",
    "task.columns.triggerType",
    "task.columns.deliveryStatus",
    "task.columns.deadlineAt",
    "task.columns.error",
    "task.columns.createTime",
    "task.status.QUEUED",
    "task.status.RUNNING",
    "task.status.WAITING",
    "task.status.COMPLETED",
    "task.status.FAILED",
    "task.status.CANCELLED",
    "task.trigger.IMMEDIATE",
    "task.trigger.SCHEDULED",
    "task.taskType.SKILL",
    "task.taskType.BATCH",
    "task.delivery.PENDING",
    "task.delivery.SENT",
    "task.delivery.FAILED",
    "task.delivery.NONE",
    "task.detail.timeline",
    "task.detail.children",
    "task.detail.input",
    "task.detail.result",
    "task.detail.error",
    "task.cancel.action",
    "task.cancel.confirm",
    "task.cancel.failed",
    "task.empty",
    "task.loadFailed",
    "schedule.title",
    "schedule.help.action",
    "schedule.help.title",
    "schedule.help.content",
    "schedule.filter.status",
    "schedule.columns.name",
    "schedule.columns.agent",
    "schedule.columns.intent",
    "schedule.columns.scheduleType",
    "schedule.columns.cron",
    "schedule.columns.nextFireAt",
    "schedule.columns.lastFireAt",
    "schedule.columns.status",
    "schedule.columns.updateTime",
    "schedule.status.ACTIVE",
    "schedule.status.PAUSED",
    "schedule.status.COMPLETED",
    "schedule.status.MISSED",
    "schedule.detail.basic",
    "schedule.detail.history",
    "schedule.action.pause",
    "schedule.action.resume",
    "schedule.action.delete",
    "schedule.confirm.delete",
    "schedule.pauseFailed",
    "schedule.resumeFailed",
    "schedule.deleteFailed",
    "schedule.deleted",
    "schedule.empty",
    "schedule.loadFailed",
)

ERROR_CODES = (
    "COMMON_VALIDATION_ERROR",
    "COMMON_NOT_FOUND",
    "REVISION_CONFLICT",
    "UNAUTHORIZED",
    "FORBIDDEN",
    "COMMON_INTERNAL_ERROR",
)


def _flatten(data: dict[str, Any]) -> dict[str, str]:
    flat: dict[str, str] = {}
    for key, value in data.items():
        if isinstance(value, dict):
            for nested, text in _flatten(value).items():
                flat[f"{key}.{nested}"] = text
        else:
            flat[key] = str(value)
    return flat


def _locales() -> tuple[dict[str, str], dict[str, str]]:
    zh = _flatten(json.loads((LOCALES / "zh-CN.json").read_text(encoding="utf-8")))
    en = _flatten(json.loads((LOCALES / "en-US.json").read_text(encoding="utf-8")))
    return zh, en


def test_b130_task_schedule_keys_exist_in_both_locales() -> None:
    zh, en = _locales()
    missing_zh = [key for key in REQUIRED_KEYS if key not in zh]
    missing_en = [key for key in REQUIRED_KEYS if key not in en]
    assert not missing_zh, f"zh-CN 缺 {missing_zh}"
    assert not missing_en, f"en-US 缺 {missing_en}"


def test_b130_task_schedule_key_sets_match() -> None:
    zh, en = _locales()
    zh_keys = {key for key in zh if key.startswith(("task.", "schedule."))}
    en_keys = {key for key in en if key.startswith(("task.", "schedule."))}
    assert zh_keys == en_keys, f"差异: zh-en={sorted(zh_keys - en_keys)} en-zh={sorted(en_keys - zh_keys)}"


def test_b130_status_labels_are_localized_not_schema_keys() -> None:
    zh, en = _locales()
    assert zh["task.status.FAILED"] == "失败"
    assert zh["task.status.CANCELLED"] == "已取消"
    assert zh["task.status.COMPLETED"] == "已完成"
    assert zh["schedule.status.MISSED"] == "已错过"
    for key in (
        "task.status.FAILED",
        "task.status.CANCELLED",
        "task.status.COMPLETED",
        "schedule.status.MISSED",
        "schedule.status.ACTIVE",
        "schedule.status.PAUSED",
    ):
        assert zh[key] != en[key], f"{key} 未本地化"
        assert zh[key] != key.split(".")[-1], f"{key} 是裸 schema key"
        assert en[key] != key.split(".")[-1], f"{key} 是裸 schema key"


def test_b130_help_does_not_promise_console_orchestration() -> None:
    zh, en = _locales()
    for key in ("task.help.content", "schedule.help.content"):
        assert "Agent" in zh[key], f"{key} 未说明由 Agent 产生"
        assert "Agent" in en[key], f"{key} 未说明由 Agent 产生"
    assert "不提供任务编排" in zh["task.help.content"]
    assert "orchestrat" not in en["task.help.content"].lower()
    assert "Console 创建" not in zh["schedule.help.content"]
    assert "create in console" not in en["schedule.help.content"].lower()
    assert "console can create" not in en["schedule.help.content"].lower()


def test_b130_error_catalog_covers_task_schedule_codes() -> None:
    catalog = yaml.safe_load(MESSAGES.read_text(encoding="utf-8"))
    codes = catalog["codes"]
    for code in ERROR_CODES:
        assert code in codes, f"错误目录缺 {code}"
        messages = codes[code]["messages"]
        for locale in ("zh-CN", "en-US"):
            assert locale in messages and str(messages[locale]).strip(), f"{code} 缺 {locale}"
