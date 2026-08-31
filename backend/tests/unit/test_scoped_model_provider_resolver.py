"""TASK-010：execution-scoped Provider Resolver——不 mutate service-level registry。"""

from __future__ import annotations

from fluxion.plugins.model_provider import ModelProviderRegistry
from fluxion.runtime.model_providers import ScopedModelProviderResolver


class _StubProvider:
    async def complete(self, request):
        raise NotImplementedError


def test_T010_scoped_resolver_overlays_without_mutating_base() -> None:
    base = ModelProviderRegistry()
    base.register("dev.echo", _StubProvider())

    resolver = ScopedModelProviderResolver(base)
    resolver.register_scoped("store.provider", _StubProvider())

    # scoped 命中：store-backed provider 只在本执行可见
    assert isinstance(resolver.resolve("store.provider"), _StubProvider)
    # 未 scoped：回退 base
    assert isinstance(resolver.resolve("dev.echo"), _StubProvider)
    # base 未被 mutate：service-level registry 不累积 store-backed provider
    assert "store.provider" not in base.provider_ids()
    assert base.provider_ids() == ["dev.echo"]
