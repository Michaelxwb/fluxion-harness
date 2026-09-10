from adapters.sandbox.provider import SandboxCapabilityProvider
from framework.capability.registry import CapabilityRegistry
from framework.contracts.capability import CapabilityContract


def register_builtin_sandbox_capabilities(
    registry: CapabilityRegistry,
    provider: SandboxCapabilityProvider,
) -> None:
    contracts = [
        CapabilityContract(
            name="filesystem.read",
            description="Read a UTF-8 text file inside the assigned workspace.",
            side_effect=False,
            risk_level="low",
            execution_characteristic="sync",
        ),
        CapabilityContract(
            name="filesystem.glob",
            description="Find paths inside the assigned workspace using a relative glob.",
            side_effect=False,
            risk_level="low",
            execution_characteristic="sync",
        ),
        CapabilityContract(
            name="filesystem.grep",
            description="Search UTF-8 text files inside the assigned workspace.",
            side_effect=False,
            risk_level="low",
            execution_characteristic="sync",
        ),
        CapabilityContract(
            name="filesystem.write",
            description="Write a UTF-8 text file inside the assigned workspace.",
            side_effect=True,
            risk_level="medium",
            execution_characteristic="sync",
        ),
        CapabilityContract(
            name="filesystem.edit",
            description="Apply an exact text replacement inside the assigned workspace.",
            side_effect=True,
            risk_level="medium",
            execution_characteristic="sync",
        ),
        CapabilityContract(
            name="shell.execute",
            description="Execute an allowlisted argv command inside an isolated workspace.",
            side_effect=True,
            risk_level="high",
            execution_characteristic="worker_preferred",
        ),
    ]
    for contract in contracts:
        registry.register(contract, provider)
