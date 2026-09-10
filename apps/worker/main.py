import asyncio

from framework.execution.worker_engine import WorkerEngine
from framework.observability.logging import configure_logging
from framework.settings import get_settings


async def main() -> None:
    settings = get_settings()
    configure_logging(service_name="worker", level=settings.log_level)
    await WorkerEngine().run_forever()


if __name__ == "__main__":
    asyncio.run(main())
