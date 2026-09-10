from framework.contracts.auth import AuthProvider, InvocationCredential
from framework.contracts.context import TrustedExecutionContext


class AuthRuntime:
    def __init__(self, providers: dict[str, AuthProvider]):
        self.providers = providers

    async def get_credential(
        self,
        *,
        provider_key: str,
        context: TrustedExecutionContext,
    ) -> InvocationCredential:
        return await self.providers[provider_key].get_invocation_credential(context=context)
