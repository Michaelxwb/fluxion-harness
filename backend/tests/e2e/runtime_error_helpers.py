"""TASK-022 真实服务错误契约 helpers：受控模型故障 + 跨层断言。"""

from __future__ import annotations

from typing import Any


def error_triple(payload: dict[str, Any]) -> tuple[int, str | None, str]:
    """错误三元组（code/slug/request_id），HTTP envelope 与 SSE 帧通用。"""
    return (int(payload["code"]), payload.get("error"), str(payload["request_id"]))


class CountingProvider:
    """受控模型故障 Adapter：计数调用，可配行为（timeout/error/slow）。"""

    def __init__(self, behavior: str = "error") -> None:
        self.behavior = behavior
        self.calls = 0

    async def complete(self, request):  # type: ignore[no-untyped-def]
        from fluxion.plugins.contracts import ModelProviderError, ModelProviderTimeoutError

        self.calls += 1
        if self.behavior == "timeout":
            raise ModelProviderTimeoutError("model provider dev.echo timed out")
        raise ModelProviderError("model provider dev.echo failed")

    async def stream(self, request):  # type: ignore[no-untyped-def]
        from fluxion.plugins.contracts import ModelProviderError, ModelProviderTimeoutError

        self.calls += 1
        if self.behavior == "timeout":
            raise ModelProviderTimeoutError("model provider dev.echo timed out")
        raise ModelProviderError("model provider dev.echo failed")
        yield  # pragma: no cover - 使其成为异步生成器
