"""进程级 `SharedSettings`（Console 热路径唯一构造点）。

`SharedSettings()` 每次构造都会读环境与 `.env`（实测 ~1.5ms）；放在每请求路径上时，
单 Console 进程在 800 并发下会被固定开销吃掉可观的事件循环时间。这里按进程缓存一份，
lifespan 仍可注入 `app.state.settings` 覆盖（测试/多实例各自隔离）；未跑 lifespan 的
ASGI 测试回落到这份缓存。
"""

from __future__ import annotations

from functools import lru_cache

from muad_common import SharedSettings


@lru_cache(maxsize=1)
def process_settings() -> SharedSettings:
    """本进程唯一的启动设置；调用方只读，不在这里改环境。"""
    return SharedSettings()


def reset_settings_cache() -> None:
    """测试专用：环境变更后重建缓存（生产不调用）。"""
    process_settings.cache_clear()
