"""TASK-002：Console 进程级 settings 单例（S-02 / RULE-03 的固定 CPU 项）。

真实边界：本文件只验证构造次数，不连服务；`SharedSettings` 的真实字段仍由既有
settings/authorization 测试覆盖。构造次数是热路径事实——每请求构造一次就无法满足
1000 × 15s 空闲轮询的 CPU 预算。
"""

from __future__ import annotations

import pytest
from muad_console_platform.infrastructure import settings as settings_module


def test_process_settings_is_constructed_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0
    original = settings_module.SharedSettings

    class CountingSettings(original):  # type: ignore[misc, valid-type]
        def __init__(self) -> None:
            nonlocal calls
            calls += 1
            super().__init__()

    monkeypatch.setattr(settings_module, "SharedSettings", CountingSettings)
    settings_module.reset_settings_cache()
    try:
        first = settings_module.process_settings()
        second = settings_module.process_settings()
        assert first is second
        assert calls == 1, "同一进程只允许构造一次 SharedSettings"
    finally:
        settings_module.reset_settings_cache()
