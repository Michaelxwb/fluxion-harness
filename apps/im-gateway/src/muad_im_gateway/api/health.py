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
        """**每个已注册的**通道适配器都已启动才算就绪（设计 §4.1）。

        注册表就是本部署声明的通道面：某个通道整体起不来（例如企微 bot 全部无可用凭据）
        等于那个通道整体必需条件缺失——不能让另一个通道起来了就把它盖过去。
        不要求全部 bot CONNECTED：单 bot 故障只体现在 detail 的 `degraded_bots`。
        """
        registry = getattr(app.state, "registry", None)
        if registry is None:
            return False
        states = registry.adapter_states
        return bool(states) and all(states.values())

    def console_ready() -> bool:
        snapshot = getattr(app.state, "bot_snapshot", None)
        if snapshot is None:
            return False
        return bool(snapshot.console_reachable or snapshot.is_ready())

    async def streams_ready() -> bool:
        """声明了经 Redis Stream 的通道面（远程 Connector / webhook）时，Redis 是硬依赖。

        WebChat-only 部署（`GATEWAY_ROUTED_CHANNELS`/`GATEWAY_WEBHOOK_CHANNELS` 均为空）不
        经过 Stream，Redis 抖动不该把请求面摘出负载均衡。`delivery_streams` 未装配时同样
        没有 Stream 依赖（装配根两者一起发布；测试替身只替换探针状态）。
        """
        streams = getattr(app.state, "delivery_streams", None)
        if not getattr(app.state, "stream_reliant", False) or streams is None:
            return True
        return bool(await streams.ping())

    return {"adapters": adapters_ready, "console": console_ready, "streams": streams_ready}


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
