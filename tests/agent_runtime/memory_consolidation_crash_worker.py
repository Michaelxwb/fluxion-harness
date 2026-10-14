"""SIGKILL target: execute the production batch transaction before/after commit."""

import asyncio
import json
import os
import sys
from uuid import UUID

from muad_agent_runtime.application.memory_consolidation_output import ConsolidationBatch
from muad_agent_runtime.application.memory_job_effects import apply_batch
from muad_agent_runtime.application.memory_jobs import JobClaim
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


async def main():
    data = json.loads(sys.stdin.readline())
    fields = data["claim"]
    for key in ("id", "run_id", "conversation_id"):
        fields[key] = UUID(fields[key])
    claim = JobClaim(**fields)
    engine = create_async_engine(os.environ["MUAD_CRASH_DATABASE_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory.begin() as session:
            result = await apply_batch(
                session, claim, ConsolidationBatch.model_validate(data["batch"]), credential_canaries=()
            )
            assert result["written"] == 1
            if data["phase"] == "after_commit":
                await session.commit()
            print("READY", flush=True)
            await asyncio.Event().wait()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
