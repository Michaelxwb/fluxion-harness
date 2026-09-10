from framework.contracts.capability import CapabilityContract, CapabilityResult
from framework.contracts.context import TrustedExecutionContext
from framework.contracts.sandbox import SandboxExecutor
from framework.web.errors import AppError


class SandboxCapabilityProvider:
    def __init__(self, executor: SandboxExecutor):
        self.executor = executor

    async def invoke(
        self,
        *,
        contract: CapabilityContract,
        input: dict[str, object],
        context: TrustedExecutionContext,
    ) -> CapabilityResult:
        if context.workspace_id is None:
            raise AppError(
                code="WORKSPACE_REQUIRED",
                message=f"capability '{contract.name}' requires a workspace",
                status_code=409,
            )
        result = await self.executor.execute(
            workspace_id=context.workspace_id,
            operation=contract.name,
            arguments=input,
            context=context,
        )
        return CapabilityResult(data=result.data)
