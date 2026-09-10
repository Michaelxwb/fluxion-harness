from framework.contracts.capability import CapabilityContract, CapabilityProvider


class CapabilityRegistry:
    def __init__(self) -> None:
        self._contracts: dict[str, CapabilityContract] = {}
        self._providers: dict[str, CapabilityProvider] = {}

    def register(self, contract: CapabilityContract, provider: CapabilityProvider) -> None:
        self._contracts[contract.name] = contract
        self._providers[contract.name] = provider

    def resolve(self, name: str) -> tuple[CapabilityContract, CapabilityProvider]:
        return self._contracts[name], self._providers[name]
