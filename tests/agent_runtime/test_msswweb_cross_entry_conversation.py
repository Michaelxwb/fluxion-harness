"""TASK-010：跨入口共享会话的核属与投影（纯单元，无数据库、无网络）。

覆盖点（设计 §附件 / §通知 / RULE-im-006 / S-06）：

1. `ConversationScope.entry_agnostic`：默认严格按入口核属（跨入口 404）；置 True 时同一
   user + Agent 跨入口共享，入口只作来源元数据。
2. 会话列表项投影注入 `entry_id`（来源）与 `has_unread`（EXISTS 判定结果），不丢字段。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from muad_agent_runtime.application.channel_conversations import (
    ConversationScope,
    assert_entry_attribution,
)
from muad_agent_runtime.application.webchat_history import _conversation_item
from muad_api import AppError
from muad_api.error_codes import ErrorCode


def _scope(*, entry_id: uuid.UUID, entry_agnostic: bool) -> ConversationScope:
    return ConversationScope(
        tenant_id="t1",
        user_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        entry_id=entry_id,
        external_thread_id="",
        entry_agnostic=entry_agnostic,
    )


def _conversation_row(*, channel_account_id: uuid.UUID) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        title="会话",
        create_time=datetime(2026, 11, 16, 8, 0, tzinfo=UTC),
        last_message_at=None,
        last_run_id=None,
        status="ACTIVE",
        channel_account_id=channel_account_id,
    )


def test_strict_scope_rejects_cross_entry_conversation() -> None:
    """严格模式：会话入口与请求入口不同 → 404（入口是硬核属维度）。"""
    webchat_entry = uuid.uuid4()
    msswweb_entry = uuid.uuid4()
    conversation = _conversation_row(channel_account_id=webchat_entry)

    with pytest.raises(AppError) as exc:
        assert_entry_attribution(conversation, _scope(entry_id=msswweb_entry, entry_agnostic=False))
    assert exc.value.code == ErrorCode.COMMON_NOT_FOUND


def test_agnostic_scope_allows_cross_entry_conversation() -> None:
    """跨入口共享：同一 user + Agent 在 MSSWWEB 面可读 WEBCHAT 创建的会话（不 404）。"""
    webchat_entry = uuid.uuid4()
    msswweb_entry = uuid.uuid4()
    conversation = _conversation_row(channel_account_id=webchat_entry)

    # 不抛异常即通过（入口不是可见性断言）。
    assert_entry_attribution(conversation, _scope(entry_id=msswweb_entry, entry_agnostic=True))


def test_conversation_item_carries_source_entry_and_unread() -> None:
    """列表项投影：来源入口与会话级未读都要真实注入，不能回落默认值。"""
    entry = uuid.uuid4()
    row = _conversation_row(channel_account_id=entry)

    item = _conversation_item(row, active_status=None, has_unread=True)

    assert item.entry_id == entry
    assert item.has_unread is True
    assert item.conversation_id == row.id
