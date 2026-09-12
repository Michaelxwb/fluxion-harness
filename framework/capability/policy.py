from framework.contracts.capability import CapabilityContract

_DURABLE_CHARACTERISTICS = {"worker_only", "worker_preferred", "async"}


def requires_durable_execution(contract: CapabilityContract) -> bool:
    if contract.execution_characteristic in _DURABLE_CHARACTERISTICS:
        return True
    return contract.risk_level.lower() == "high"
