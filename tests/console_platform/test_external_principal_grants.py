"""`/identity` 逐入口裁决的纯单元测试（API-01，不依赖数据库）。

`_grant_views` 是「入口能不能用」的唯一判定处：四个 `reason` 的优先级、`NO_AGENT_GRANT`
（Agent 被删）与 `AGENT_DISABLED`（Agent 被停用）必须分开。这里用真实模型对象喂它，
不 mock 判定本身——这些优先级搞错的话，外部平台会把「Agent 被删」显示成「Agent 停用」，
运维排查方向就错了。
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

from muad_console_platform.application.external_assertion.principal import _grant_views


def _bot(*, enabled: bool = True, agent_id: uuid.UUID | None = None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        agent_id=agent_id or uuid.uuid4(),
        name="MSSW 入口",
        channel="MSSWWEB",
        enabled=enabled,
        bot_id="mssw-entry",
    )


def _grant():
    return SimpleNamespace(id=uuid.uuid4())


def test_enabled_entry_with_enabled_agent_is_allowed():
    (view,) = _grant_views([(_grant(), _bot(), True)])
    assert view.allowed is True
    assert view.reason is None


def test_disabled_entry_reports_entry_reason():
    (view,) = _grant_views([(_grant(), _bot(enabled=False), True)])
    assert view.allowed is False
    assert view.reason == "NO_ENTRY_GRANT"


def test_deleted_agent_is_distinct_from_disabled_agent():
    """Agent 被删（None）与 Agent 被停用（False）必须报不同原因。"""
    (deleted,) = _grant_views([(_grant(), _bot(), None)])
    (disabled,) = _grant_views([(_grant(), _bot(), False)])
    assert (deleted.allowed, deleted.reason) == (False, "NO_AGENT_GRANT")
    assert (disabled.allowed, disabled.reason) == (False, "AGENT_DISABLED")


def test_disabled_entry_wins_over_disabled_agent():
    """入口自身停用优先级最高：不让 Agent 状态把原因说成别的。"""
    (view,) = _grant_views([(_grant(), _bot(enabled=False), False)])
    assert view.reason == "NO_ENTRY_GRANT"


def test_views_keep_grant_order_and_one_row_per_entry():
    """同一 Agent 挂两个入口时，`grants[]` 是**两行**（与 `agents` 的去重视角不同）。"""
    agent_id = uuid.uuid4()
    first = _bot(agent_id=agent_id)
    second = _bot(agent_id=agent_id)
    views = _grant_views([(_grant(), first, True), (_grant(), second, True)])
    assert [view.entry_id for view in views] == [first.id, second.id]
    assert all(view.agent_id == agent_id for view in views)


def test_agents_from_grants_excludes_unusable_entries():
    """`agents` 是「当前能操作的 Agent」——停用/删除的必须排除，否则资源接口会放行。

    这是行为变更的落点：网关用 `agents` 判定能否提交消息。若把停用 Agent 留在集合里，
    「机器人停了但用户还能发消息」就会复现。
    """
    from muad_console_platform.application.external_assertion.principal import (
        _agents_from_grants,
    )

    usable = _bot()
    same_agent = _bot(agent_id=usable.agent_id)
    disabled_entry = _bot(enabled=False)
    disabled_agent = _bot()
    deleted_agent_id = uuid.uuid4()
    deleted_agent = SimpleNamespace(
        id=uuid.uuid4(),
        agent_id=deleted_agent_id,
        name="已删 Agent 的入口",
        channel="MSSWWEB",
        enabled=True,
        bot_id="gone",
    )

    agents = _agents_from_grants(
        [
            (_grant(), usable, True),
            (_grant(), same_agent, True),
            (_grant(), disabled_entry, True),
            (_grant(), disabled_agent, False),
            (_grant(), deleted_agent, None),
        ]
    )

    assert [agent.agent_id for agent in agents] == [usable.agent_id], (
        "只保留可用入口的 Agent，且同一 Agent 去重"
    )
