"""S-11 管理面混合负载骨架（TASK-013 实现真实容量验证）。

计划阶段只保证可收集；容量 profile、管理查询与回放编排由实现任务补齐。
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.capacity, pytest.mark.capacity_load]


@pytest.mark.skip(reason="plan skeleton; implemented by TASK-013")
def test_learning_management_mixed_load() -> None:
    """1000 用户负载下的管理查询与回放不回归用户侧。"""
