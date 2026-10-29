"""进程级启动设置缓存的契约：热路径同一实例，环境变更需显式 reset。

`SharedSettings()` 仍保留为“每次重读”的构造器（测试/CLI 用）；生产热路径只允许
`process_settings()`，否则 100 并发下会把 1.5–3ms 的 `.env` 解析放大成 CPU 瓶颈。
"""

from __future__ import annotations

import pytest
from muad_common import process_settings, reset_settings_cache


def test_process_settings_is_cached_until_explicit_reset() -> None:
    # 顶层 conftest 给 `monkeypatch.setenv` 包了「改环境即清缓存」的测试卫生逻辑；
    # 本用例验证的正是缓存本身，必须用未包装的上下文，否则每次 setenv 都会清掉。
    with pytest.MonkeyPatch.context() as monkeypatch:
        reset_settings_cache()
        monkeypatch.setenv("DEFAULT_TENANT_ID", "tenant-cache-a")
        first = process_settings()
        assert first.default_tenant_id == "tenant-cache-a"

        monkeypatch.setenv("DEFAULT_TENANT_ID", "tenant-cache-b")
        assert process_settings() is first
        assert process_settings().default_tenant_id == "tenant-cache-a"

        reset_settings_cache()
        rebuilt = process_settings()
        assert rebuilt is not first
        assert rebuilt.default_tenant_id == "tenant-cache-b"


def test_shared_settings_stays_uncached_for_explicit_reads(monkeypatch) -> None:
    """`SharedSettings()` 不得被缓存：调用方需要“当前环境”时必须还能拿到新值。"""
    from muad_common import SharedSettings

    monkeypatch.setenv("DEFAULT_TENANT_ID", "tenant-fresh-a")
    assert SharedSettings(_env_file=None).default_tenant_id == "tenant-fresh-a"
    monkeypatch.setenv("DEFAULT_TENANT_ID", "tenant-fresh-b")
    assert SharedSettings(_env_file=None).default_tenant_id == "tenant-fresh-b"
