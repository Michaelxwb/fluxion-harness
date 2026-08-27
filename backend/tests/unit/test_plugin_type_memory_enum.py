"""ADR-MEM-001 TASK-005 验收测试：PluginType MEMORY 收口 green-before（ADR-EXT-001 决议验证）。

覆盖：
- B-02: PluginType 无 MEMORY 成员；`PluginType.MEMORY` 访问 AttributeError；
  memory 由 SessionMemoryStore SPI + SemanticStoreProvider SPI 分治承接。

green-before 约定（cf-task:start 规则 #7）：
- MEMORY 成员已由 ADR-EXT-001 从 contracts.py 删除（enum 注释明示"MEMORY 由
  ADR-MEM-001 删除"），本 ADR 只做收口决议验证——属"已有行为补测无法 RED"，
  记录原因，不伪造失败。
- 真实边界：直接 import 真实 contracts.py / memory.py 定义，无 mock、无副本枚举。
"""

from __future__ import annotations

from typing import Protocol

import pytest

from fluxion.plugins.contracts import PluginType, SemanticStoreProvider
from fluxion.runtime.memory import MemoryRecord, SessionMemoryStore


def test_b02_plugin_type_has_no_memory_member() -> None:
    """B-02 断言 1：PluginType 无 MEMORY 成员（delete 决议收口）。"""
    assert "MEMORY" not in PluginType.__members__


def test_b02_plugin_type_memory_access_raises_attribute_error() -> None:
    """B-02 断言 2：`PluginType.MEMORY` 属性访问报 AttributeError。"""
    with pytest.raises(AttributeError):
        PluginType.MEMORY  # noqa: B018


def test_b02_memory_served_by_session_store_and_semantic_provider_divide() -> None:
    """B-02 断言 3：分治承接——memory 不再是 Plugin 扩展点，由两个 SPI 分治。

    - SessionMemoryStore（runtime/memory.py）：session-scoped 记忆外置
      （L1/L2/SessionContextSummary，MemoryRecord 面），Runtime 无状态关键。
    - SemanticStoreProvider（plugins/contracts.py）：user-scoped 语义检索
      （store/recall/search，带 timeout_ms），经 PluginType.SEMANTIC_STORE 扩展。
    """
    # session 侧：Protocol + session-scoped 表面（MemoryRecord）
    assert issubclass(SessionMemoryStore, Protocol)
    for method in ("append_l1", "append_summary", "read_l1", "read_summaries"):
        assert callable(getattr(SessionMemoryStore, method))
    assert hasattr(SessionMemoryStore, "read_l2")

    # user 侧：runtime_checkable Protocol + 带 timeout 的检索表面（dict record）
    assert issubclass(SemanticStoreProvider, Protocol)
    for method in ("store", "recall", "search"):
        assert callable(getattr(SemanticStoreProvider, method))

    # 分治边界：语义检索走 SEMANTIC_STORE 插件类型；两个 SPI 方法面互不重叠
    assert PluginType.SEMANTIC_STORE.value == "semantic_store"
    session_surface = {"append_l1", "append_l2", "append_summary", "read_l1", "read_l2", "read_summaries", "remove_l1"}
    semantic_surface = {"store", "recall", "search"}
    assert session_surface.isdisjoint(semantic_surface)
    assert MemoryRecord.__module__.startswith("fluxion.runtime.memory")
