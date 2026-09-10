from framework.capability.runtime import CapabilityRuntime
from framework.contracts.capability import CapabilityResult
from framework.contracts.context import TrustedExecutionContext


class CapabilityStepExecutor:
    def __init__(self, runtime: CapabilityRuntime):
        self.runtime = runtime

    async def execute(
        self,
        *,
        capability_name: str,
        input: dict[str, object],
        context: TrustedExecutionContext,
    ) -> CapabilityResult:
        return await self.runtime.invoke(
            name=capability_name,
            input=input,
            context=context,
            invocation_mode="worker",
        )
