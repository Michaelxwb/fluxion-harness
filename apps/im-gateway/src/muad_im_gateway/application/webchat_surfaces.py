"""Chat surface 解析（WebChat / MSSWWEB 多挂载面）。

同一套 webchat router 按前缀挂到多个 surface 上，请求落在哪个前缀就属于哪个 surface。
本模块独立于 `api/webchat.py` 与 `api/deps.py`，避免两者互相 import 形成环。
"""

from __future__ import annotations

from fastapi import Request

from .webchat_core import WEBCHAT_CHANNEL, ChatSurface

#: `app.state` 上前缀 → surface 的挂载表（由 `main.mount_webchat_surfaces` 写入）。
SURFACES_STATE = "webchat_surfaces"


def get_webchat_surface(request: Request) -> ChatSurface:
    """按实际挂载前缀解析当前 surface（由挂载写入 handler）。"""
    surfaces: dict[str, ChatSurface] = getattr(request.app.state, SURFACES_STATE, {})
    path = request.scope.get("path", "")
    for prefix, surface in surfaces.items():
        if path == prefix or path.startswith(prefix + "/"):
            return surface
    return ChatSurface(channel=WEBCHAT_CHANNEL)
