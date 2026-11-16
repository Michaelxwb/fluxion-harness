"""B-130：Task/Schedule 中英文文案与帮助词条（scheduled-task-v2 §3.10）。

两种 locale 键完全对应；状态/模式/结束原因/触发结果/取消范围/帮助文案准确；帮助不承诺
Console 编排或创建能力；错误目录覆盖 Task/Schedule 相关错误码。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
LOCALES = ROOT / "apps/console-platform/frontend/src/locales"
MESSAGES = ROOT / "config/api-messages.yaml"
MODULE = ROOT / "apps/console-platform/frontend/src/modules/task-schedule"
COMMON = ROOT / "apps/console-platform/frontend/src/components/common"

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
    "task.columns.taskType",
    "task.columns.status",
    "task.columns.statusIcons",
    "task.columns.triggerType",
    "task.columns.deadlineAt",
    "task.columns.error",
    "task.columns.createTime",
    "task.status.QUEUED",
    "task.status.RUNNING",
    "task.status.WAITING_EXTERNAL",
    "task.status.WAITING_HUMAN",
    "task.status.COMPLETED",
    "task.status.FAILED",
    "task.status.CANCELLED",
    "task.status.unknown",
    # run-observability 的关联 Task 状态仍用这条词条（其枚举尚未跟上 v2），保留以免静默退化为裸 key。
    "task.status.WAITING",
    "task.trigger.IMMEDIATE",
    "task.trigger.SCHEDULED",
    "task.taskType.SKILL",
    "task.taskType.REMINDER",
    "task.taskType.AGENTIC",
    "task.taskType.BATCH",
    "task.waitKind.ASYNC_OPERATION",
    "task.waitKind.EXTERNAL_POLL",
    "task.waitKind.BATCH_FANIN",
    "task.memory.READ_ONLY",
    "task.detail.timeline",
    "task.detail.children",
    "task.detail.input",
    "task.detail.result",
    "task.detail.error",
    "task.detail.snapshot",
    "task.detail.snapshotSchema",
    "task.detail.allowlist",
    "task.detail.human",
    "task.detail.humanQuestion",
    "task.detail.waitKind",
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
    "schedule.filter.agent",
    "schedule.filter.user",
    "schedule.columns.name",
    "schedule.columns.agent",
    "schedule.columns.user",
    "schedule.columns.mode",
    "schedule.columns.attention",
    "schedule.columns.nextFireAt",
    "schedule.columns.lastFireAt",
    "schedule.columns.status",
    "schedule.status.ACTIVE",
    "schedule.status.PAUSED",
    "schedule.status.NEEDS_ATTENTION",
    "schedule.status.ENDED",
    "schedule.status.CANCELLED",
    "schedule.status.unknown",
    "schedule.mode.REMINDER",
    "schedule.mode.SKILL",
    "schedule.mode.AGENTIC",
    "schedule.endReason.COMPLETED",
    "schedule.endReason.MISSED",
    "schedule.endReason.EXHAUSTED",
    "schedule.endReason.ADMISSION_FAILED",
    "schedule.endReason.USER_CANCELLED",
    "schedule.delivery.PUSH",
    "schedule.delivery.NONE",
    "schedule.concurrency.SKIP_IF_RUNNING",
    "schedule.concurrency.QUEUE",
    "schedule.concurrency.ALLOW_PARALLEL",
    "schedule.badge.pendingHuman",
    "schedule.badge.needsAttention",
    "schedule.detail.basic",
    "schedule.detail.history",
    "schedule.detail.attentionReason",
    "schedule.detail.configVersion",
    "schedule.detail.dependencies",
    "schedule.detail.deliveryIntent",
    "schedule.detail.budget",
    "schedule.detail.memory",
    "schedule.action.pause",
    "schedule.action.resume",
    "schedule.action.cancel",
    "schedule.action.delete",
    "schedule.cancel.title",
    "schedule.cancel.content",
    "schedule.cancel.confirm",
    "schedule.cancel.scope.FUTURE_ONLY",
    "schedule.cancel.scope.ALL_ACTIVE",
    "schedule.cancel.scopeHint.FUTURE_ONLY",
    "schedule.cancel.scopeHint.ALL_ACTIVE",
    "schedule.confirm.delete",
    "schedule.pauseFailed",
    "schedule.resumeFailed",
    "schedule.deleteFailed",
    "schedule.cancelFailed",
    "schedule.admissionFailed",
    "schedule.deleted",
    "schedule.empty",
    "schedule.loadFailed",
    "schedule.fire.empty",
    "schedule.fire.columns.time",
    "schedule.fire.columns.status",
    "schedule.fire.columns.skipReason",
    "schedule.fire.columns.missedCount",
    "schedule.fire.columns.dst",
    "schedule.fire.columns.admissionError",
    "schedule.fire.columns.execution",
    "schedule.fire.status.CREATED",
    "schedule.fire.status.EXECUTION_CREATED",
    "schedule.fire.status.SKIPPED",
    "schedule.fire.status.FAILED_ADMISSION",
    "schedule.fire.status.CANCELLED",
    "schedule.fire.skipReason.OVERLAP",
    "schedule.fire.skipReason.MISFIRE",
    "schedule.fire.skipReason.QUOTA",
    "schedule.fire.skipReason.PAUSED_BY_UPDATE",
    "schedule.fire.dstResolution.FOLD_FIRST",
    "schedule.fire.dstEvent.DST_GAP_SKIPPED",
    "schedule.fire.dstEvent.DST_FOLD_SECOND_SKIPPED",
    "schedule.fire.unknown",
)

#: v1 状态与投递列已退役：留着它们会让界面「看起来还支持」旧枚举。
RETIRED_KEYS = (
    "task.columns.intent",
    "task.columns.deliveryStatus",
    "task.delivery.PENDING",
    "schedule.status.COMPLETED",
    "schedule.status.MISSED",
    "schedule.columns.cron",
    "schedule.columns.scheduleType",
    "schedule.columns.intent",
)

ERROR_CODES = (
    "COMMON_VALIDATION_ERROR",
    "COMMON_NOT_FOUND",
    "REVISION_CONFLICT",
    "UNAUTHORIZED",
    "FORBIDDEN",
    "COMMON_INTERNAL_ERROR",
    "SCHEDULE_NOT_FOUND",
    "SCHEDULE_STATE_INVALID",
    "SCHEDULE_ADMISSION_FAILED",
    "SCHEDULE_QUOTA_EXCEEDED",
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
    for locale, flat in (("zh-CN", zh), ("en-US", en)):
        missing = [key for key in REQUIRED_KEYS if key not in flat]
        assert not missing, f"{locale} 缺 {missing}"
        stale = [key for key in RETIRED_KEYS if key in flat]
        assert not stale, f"{locale} 残留退役词条 {stale}"


def test_b130_task_schedule_key_sets_match() -> None:
    zh, en = _locales()
    zh_keys = {key for key in zh if key.startswith(("task.", "schedule."))}
    en_keys = {key for key in en if key.startswith(("task.", "schedule."))}
    assert zh_keys == en_keys, f"差异: zh-en={sorted(zh_keys - en_keys)} en-zh={sorted(en_keys - zh_keys)}"


def test_b130_status_labels_are_localized_not_schema_keys() -> None:
    zh, en = _locales()
    assert zh["task.status.FAILED"] == "失败"
    assert zh["task.status.CANCELLED"] == "已取消"
    assert zh["task.status.WAITING_HUMAN"] == "等待回答"
    assert zh["task.status.WAITING_EXTERNAL"] == "等待外部结果"
    assert zh["schedule.status.ENDED"] == "已结束"
    assert zh["schedule.status.NEEDS_ATTENTION"] == "需关注"
    assert zh["schedule.endReason.MISSED"] == "已错过"
    for key in (
        "task.status.FAILED",
        "task.status.WAITING_HUMAN",
        "task.status.WAITING_EXTERNAL",
        "schedule.status.ENDED",
        "schedule.status.NEEDS_ATTENTION",
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


# 可见中文与全角字符：去掉注释后仍出现在字符串字面量里即视为硬编码文案
CJK = re.compile(r"[　-〿一-鿿！-～]")

# 名称化改动的落点：页面/单元格/详情/动作都不承载硬编码文案（实体名与短 id 都是数据）
READABILITY_SOURCES = (
    MODULE / "TaskPage.tsx",
    MODULE / "TaskStatusIcons.tsx",
    MODULE / "TaskDetailSideSheet.tsx",
    MODULE / "SchedulePage.tsx",
    MODULE / "ScheduleDetailSideSheet.tsx",
    MODULE / "ScheduleHistoryTable.tsx",
    MODULE / "ScheduleRowActions.tsx",
    MODULE / "CancelScheduleModal.tsx",
    MODULE / "scheduleCatalog.ts",
    MODULE / "EntityNameText.tsx",
    COMMON / "CopyableText.tsx",
)


def test_b130_readability_sources_have_no_hardcoded_copy() -> None:
    """[RULE-i18n-001] 名称化列不引入硬编码文案：名称/短 id 是数据，文案仍走词条。"""
    for path in READABILITY_SOURCES:
        stripped = re.sub(r"//[^\n]*", "", re.sub(r"/\*[\s\S]*?\*/", "", path.read_text(encoding="utf-8")))
        groups = re.findall(r"'([^'\n]*)'|\"([^\"\n]*)\"", stripped)
        offenders = [text for group in groups for text in group if text and CJK.search(text)]
        assert not offenders, f"{path.name} 出现硬编码中文文案：{offenders}"
