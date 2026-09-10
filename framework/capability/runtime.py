from framework.capability.policy import requires_durable_execution
from framework.capability.registry import CapabilityRegistry
from framework.contracts.capability import CapabilityResult
from framework.contracts.context import TrustedExecutionContext
from framework.web.errors import AppError


class CapabilityRuntime:
    def __init__(self, registry: CapabilityRegistry):
        self.registry = registry

    async def invoke(
        self,
        *,
        name: str,
        input: dict[str, object],
        context: TrustedExecutionContext,
        invocation_mode: str = "realtime",
    ) -> CapabilityResult:
        if name not in context.effective_capability_set:
            raise AppError(code="CAPABILITY_FORBIDDEN", message="capability is not authorized", status_code=403)

        contract, provider = self.registry.resolve(name)
        if invocation_mode == "realtime" and requires_durable_execution(contract):
            raise AppError(
                code="CAPABILITY_REQUIRES_EXECUTION",
                message="capability must run through ServiceExecution/Worker",
                status_code=409,
            )

        return await provider.invoke(contract=contract, input=input, context=context)
