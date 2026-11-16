"""TASK-028：待处理视图的契约（design frontend §3.6、§3.3 CMP-06）。

这一层验证**源码契约与词条**：组件真的按 `kind` 分流、表单真的由 `input_schema` 生成、
提交真的复用幂等键、失败真的保留内容。运行时渲染行为（多 OPEN 并存、键盘焦点、窄屏）
不在本文件的断言范围内，见任务文档 Log 的说明。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "apps/console-platform/frontend/src/modules/webchat/task-center"
LOCALES = ROOT / "apps/console-platform/frontend/src/locales"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _locale(name: str) -> dict[str, str]:
    return json.loads(_read(LOCALES / f"{name}.json"))


def test_pending_panel_separates_human_from_attention() -> None:
    source = _read(MODULE / "PendingPanel.tsx")
    assert "item.kind === 'HUMAN'" in source
    # NEEDS_ATTENTION 不生成表单：只有 Human 卡片里出现 input_schema。
    human_start = source.index("function HumanCard")
    attention_start = source.index("function AttentionCard")
    human_body = source[human_start:attention_start]
    attention_body = source[attention_start:]
    assert "schemaFields(props.item.input_schema)" in human_body
    assert "input_schema" not in attention_body
    assert "onResume" in attention_body


def test_pending_form_is_schema_driven_and_marks_required_fields() -> None:
    source = _read(MODULE / "PendingPanel.tsx")
    assert "export function schemaFields" in source
    assert "required.has(name)" in source
    assert "aria-invalid" in source and "role=\"alert\"" in source
    # 时间一律经共享格式化器。
    assert "<DateTimeText value={props.item.deadline_at} />" in source


def test_pending_form_falls_back_to_free_text_without_a_schema() -> None:
    """`ask_user` 可以只带问题：没有 input_schema 时必须能输入自由文本回答。"""
    source = _read(MODULE / "PendingPanel.tsx")
    assert "const freeText = fields.length === 0;" in source
    assert "chat.taskCenter.pending.freeText" in source
    assert "{ answer: freeValue }" in source
    zh, en = _locale("zh-CN"), _locale("en-US")
    for key in (
        "chat.taskCenter.pending.freeText",
        "chat.taskCenter.pending.freeTextPlaceholder",
    ):
        assert key in zh and key in en, key
        assert zh[key] != en[key], f"{key} 两侧必须真的本地化"


def test_pending_controller_reuses_the_idempotency_key_on_retry() -> None:
    source = _read(MODULE / "pendingState.ts")
    assert "if (pendingKey?.interactionId !== interactionId)" in source
    assert "respondHuman(interactionId, response, { idempotencyKey: key })" in source
    # 成功后才清键；失败保留内容并给出错误码。
    assert "pendingKey = null;" in source
    assert "submitErrorCode: errorCodeOf(error)" in source


def test_pending_controller_refreshes_both_pending_and_badges() -> None:
    source = _read(MODULE / "pendingState.ts")
    assert "await Promise.all([read(true), readBadges()]);" in source
    assert "badgesErrorCode" in source, "徽标失败不阻塞列表，但必须可见"


def test_pending_locale_keys_exist_in_both_catalogs() -> None:
    zh, en = _locale("zh-CN"), _locale("en-US")
    for key in (
        "chat.taskCenter.pending.entry",
        "chat.taskCenter.pending.empty",
        "chat.taskCenter.pending.submit",
        "chat.taskCenter.pending.required",
        "chat.taskCenter.pending.error.unknown",
    ):
        assert key in zh and key in en, key
        assert zh[key] != en[key], f"{key} 两侧必须真的本地化"


def test_page_routes_the_pending_view_from_the_query_string() -> None:
    source = _read(MODULE / "TaskCenterPage.tsx")
    assert "new URLSearchParams(location.search).get('view') === 'pending'" in source
    assert "<PendingPanel" in source
    assert "onOpenPending" in source


def test_no_new_todo_or_notification_surface_was_added() -> None:
    """待处理是任务中心的过滤视图，不是新的待办/通知中心（design 明确排除）。"""
    for path in MODULE.glob("*.tsx"):
        source = _read(path)
        assert not re.search(r"\bTodoList\b|\bNotificationCenter\b", source), path.name
