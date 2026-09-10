from framework.contracts.context import TrustedExecutionContext
from framework.contracts.knowledge import KnowledgeHit, KnowledgeProvider


class KnowledgeRuntime:
    def __init__(self, providers: dict[str, KnowledgeProvider]):
        self.providers = providers

    async def search(
        self,
        *,
        provider_key: str,
        source_ids: list[str],
        query: str,
        context: TrustedExecutionContext,
    ) -> list[KnowledgeHit]:
        provider = self.providers[provider_key]
        return await provider.search(query=query, source_ids=source_ids, context=context)
