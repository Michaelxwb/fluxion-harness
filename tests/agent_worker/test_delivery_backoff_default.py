"""[契约] 投递退避 base 的生产默认值与可注入性。

`BACKOFF_BASE_SEC` 从 Worker 侧模块常量改为设置项 `delivery_backoff_base_sec`。动因是**测试成本**：
原来的 E2E 必须真的等满 `5 + 10 + 20 + 40` 秒才能证明「退避耗尽 → 写审计」（实测单条 97.9s）。
改为设置项后，验收栈注入更小的值，E2E 专注验证**规律**（按几何级数增长、耗尽后写审计且不写已送达），
而「**生产默认就是 5**」这条常量事实由本文件毫秒级钉住 —— 两者合起来证明的事实集合与原来相同。

**接线由 E2E 兜底**：注入 2 之后窗口断言仍成立，就证明了服务确实读的是这个设置项，而不是还留着旧常量。
"""

from __future__ import annotations

import pytest
from muad_common import SharedSettings

DEFAULT_BACKOFF_BASE_SEC = 5


def test_delivery_backoff_base_default_is_five_seconds(monkeypatch: pytest.MonkeyPatch) -> None:
    """不带任何覆盖时，生产默认必须是 5（→ 5/10/20/40…）。"""
    monkeypatch.delenv("DELIVERY_BACKOFF_BASE_SEC", raising=False)
    assert SharedSettings().delivery_backoff_base_sec == DEFAULT_BACKOFF_BASE_SEC


def test_delivery_backoff_base_is_env_overridable(monkeypatch: pytest.MonkeyPatch) -> None:
    """验收栈靠注入压缩等待（`tests/acceptance/dfx/environment.py` 注入 2）：该链路必须真的生效。"""
    monkeypatch.setenv("DELIVERY_BACKOFF_BASE_SEC", "2")
    assert SharedSettings().delivery_backoff_base_sec == 2
