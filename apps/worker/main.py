import asyncio
import logging
import os

from adapters.postgres.repositories import SqlAlchemyExecutionRepository
from apps.platform_api.dependencies import get_session_factory
from framework.domain.execution import ExecutionStatus, ServiceExecution
from framework.execution.worker_engine import StepOutcome, WorkerEngine
from framework.observability.logging import configure_logging
from framework.settings import get_settings


class NoopStepHandler:
    """Placeholder handler: succeeds immediately without touching the world.

    Real step execution (capability/agent/human/delivery steps) is module 05/06
    development; the engine loop and lease contract are already production code.
    """

    async def handle(self, execution: ServiceExecution) -> StepOutcome:
        logging.getLogger("worker.steps").info(
            "noop_step_completed",
            extra={"event": "noop_step_completed", "execution_id": str(execution.id)},
        )
        return StepOutcome(terminal=ExecutionStatus.SUCCEEDED)


async def main() -> None:
    settings = get_settings()
    configure_logging(service_name="worker", level=settings.log_level)
    repository = SqlAlchemyExecutionRepository(get_session_factory())
    engine = WorkerEngine(
        repository,
        NoopStepHandler(),
        worker_id=os.environ.get("WORKER_ID", "worker-1"),
    )
    await engine.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
