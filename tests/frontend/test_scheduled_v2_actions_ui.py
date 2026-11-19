"""TASK-028：生命周期动作的契约（design frontend §3.6、backend §3.4.5）。"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "apps/console-platform/frontend/src/modules/webchat/task-center"
LOCALES = ROOT / "apps/console-platform/frontend/src/locales"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_cancel_offers_exactly_two_scopes_and_defaults_to_future_only() -> None:
    source = _read(MODULE / "ScheduleActionsBar.tsx")
    assert "FUTURE_ONLY" in source and "ALL_ACTIVE" in source
    assert "setScope('FUTURE_ONLY')" in source, "默认必须是只停未来"
    assert "cancel_running" not in source, "旧的双语义取消不得复活"


def test_terminal_schedules_are_read_only_and_offer_recreate() -> None:
    source = _read(MODULE / "ScheduleActionsBar.tsx")
    assert "const TERMINAL = new Set(['ENDED', 'CANCELLED'])" in source
    assert "chat.taskCenter.actions.recreate" in source
    # 终态分支里没有 pause/resume/cancel 动作。
    terminal_branch = source[source.index("terminal ? (") : source.index(") : (")]
    assert "onAction('pause')" not in terminal_branch
    assert "onAction('resume')" not in terminal_branch


def test_actions_controller_reuses_the_key_per_action_and_keeps_content_on_error() -> None:
    source = _read(MODULE / "actionsState.ts")
    assert "keys.set(identity" in source and "keys.get(identity)" in source
    assert "keys.delete(identity)" in source, "成功后才丢键"
    assert "errorCode: errorCodeOf(error)" in source
    assert "detail" in source, "成功时用服务端权威详情重画"


def test_edit_goes_through_a_new_draft_conversation() -> None:
    page = _read(MODULE / "TaskCenterPage.tsx")
    assert "onOpenConversation({ draft: `修改任务 ${name}：` })" in page, (
        "修改必须回到对话走 Draft/确认，而不是本地编辑"
    )
    assert "navigate('/chat')" not in page, "不能落到裸 /chat：会丢掉当前入口与会话"


def test_attention_reason_and_repair_are_shown_not_a_form() -> None:
    source = _read(MODULE / "PendingPanel.tsx")
    attention = source[source.index("function AttentionCard") :]
    assert "props.item.reason" in attention
    assert "props.item.action" in attention


def test_action_locale_keys_exist_in_both_catalogs() -> None:
    zh = json.loads(_read(LOCALES / "zh-CN.json"))
    en = json.loads(_read(LOCALES / "en-US.json"))
    for key in (
        "chat.taskCenter.actions.pause",
        "chat.taskCenter.actions.resume",
        "chat.taskCenter.actions.cancel",
        "chat.taskCenter.actions.scopeFutureOnly",
        "chat.taskCenter.actions.scopeAllActive",
    ):
        assert key in zh and key in en, key
        assert zh[key] != en[key], f"{key} 两侧必须真的本地化"
