"""IM Gateway 的就绪诊断：注册到 api-kit 的 `/healthz` + `/readyz` 之上。

原语（路由注册与就绪判定）由 `muad_api.install_health_probes` 提供，本模块只描述
Gateway 自己的依赖（通道适配器、Console Bot 快照、去重存储）。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import FastAPI
from muad_api.probes import ReadinessCheck

from ..infrastructure.dedupe import DedupeStore, InMemoryDedupeStore, RedisDedupeStore

DEDUPE_MODE_REDIS = "redis"
DEDUPE_MODE_MEMORY = "memory"
DEDUPE_MODE_DISABLED = "disabled"


def dedupe_mode(store: DedupeStore) -> str:
    if isinstance(store, RedisDedupeStore):
        return DEDUPE_MODE_REDIS
    if isinstance(store, InMemoryDedupeStore):
        return DEDUPE_MODE_MEMORY
    return DEDUPE_MODE_DISABLED


def readiness_checks(app: FastAPI) -> Mapping[str, ReadinessCheck]:
    def adapters_ready() -> bool:
        """必要 Bot connection manager 已初始化即就绪（设计 §4.1）。

        不要求全部 bot CONNECTED：单 bot 故障只体现在 detail 的 `degraded_bots`。
        """
        registry = getattr(app.state, "registry", None)
        return bool(registry is not None and registry.started_adapters)

    def console_ready() -> bool:
        snapshot = getattr(app.state, "bot_snapshot", None)
        if snapshot is None:
            return False
        return bool(snapshot.console_reachable or snapshot.is_ready())

    return {"adapters": adapters_ready, "console": console_ready}


def readiness_detail(app: FastAPI) -> Mapping[str, Any]:
    registry = getattr(app.state, "registry", None)
    dedupe = getattr(app.state, "dedupe_store", None)
    snapshot = getattr(app.state, "bot_snapshot", None)
    return {
        "adapters": registry.adapter_states if registry is not None else {},
        "degraded_bots": registry.degraded_bots if registry is not None else {},
        "dedupe": dedupe_mode(dedupe) if dedupe is not None else DEDUPE_MODE_DISABLED,
        "bots_revision": snapshot.revision if snapshot is not None else None,
    }
