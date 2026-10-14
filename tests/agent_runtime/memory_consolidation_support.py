"""Completed source Run, frozen snapshot, durable job and real HTTP providers."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi import FastAPI, Request
from muad_agent_core.model import OpenAICompatibleProvider
from muad_agent_runtime.infrastructure.console_client import ConsoleCredentialsClient
from muad_agent_runtime.infrastructure.models.runtime import (
    MemoryConsolidationJob,
    RunRecord,
    RuntimeSnapshot,
)
from muad_contracts.platform_settings import MemoryPolicySettings

from tests.agent_runtime.memory_confirmation_resume_support import frozen_snapshot, serve
from tests.agent_runtime.memory_confirmation_support import source


@asynccontextmanager
async def model_stack(
    engine, *, delay=0, credential_delay=0, output=None, policy=None, tenant=None, user=None
):
    import asyncio

    factory, run, _, _ = await source(engine, tenant=tenant, user=user)
    requests, credentials, live_key = [], [], ["first-live-credential"]
    model, console = FastAPI(), FastAPI()

    @model.post("/v1/chat/completions")
    async def completion(request: Request):
        requests.append((await request.json(), request.headers.get("Authorization")))
        await asyncio.sleep(delay)
        return {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": (output(requests[-1][0]) if callable(output) else output)
                        if output is not None
                        else '{"candidates":[],"task_completed":false}',
                    },
                }
            ]
        }

    @console.post("/internal/runtime/resolve-credentials")
    async def resolve(request: Request):
        payload = await request.json()
        assert request.headers["X-Tenant-Id"] == run.tenant_id
        assert payload["execution_ref"] == {"type": "RUN", "id": str(run.id)}
        assert payload["actor_user_id"] == str(run.user_id)
        credentials.append(payload)
        await asyncio.sleep(credential_delay)
        return {"code": "0", "data": {"model": {"api_key": live_key[0]}, "mcp_servers": []}}

    async with serve(model) as model_url, serve(console) as console_url:
        await frozen_snapshot(factory, run, model_url + "/v1", policy or MemoryPolicySettings())
        job, snapshot = await completed_job(factory, run)
        client = ConsoleCredentialsClient(console_url)
        try:
            yield factory, run, job, snapshot, client, requests, credentials, live_key
        finally:
            await client.aclose()


async def completed_job(factory, run):
    async with factory.begin() as session:
        row = await session.get(RunRecord, run.id)
        row.status = "COMPLETED"
        row.deadline_at = datetime.now(UTC) - timedelta(days=1)
        snapshot = await session.get(RuntimeSnapshot, run.snapshot_id)
        snapshot.model_json = {**snapshot.model_json, "model_id": "frozen-main-model"}
        job = MemoryConsolidationJob(
            id=uuid4(),
            tenant_id=run.tenant_id,
            run_id=run.id,
            snapshot_id=snapshot.id,
            conversation_id=run.conversation_id,
            user_id=run.user_id,
            through_seq=1,
            enqueued_scope_revision=0,
            claim_epoch=1,
            attempt=1,
            status="RUNNING",
            lease_owner="consolidation-owner",
            lease_until=datetime.now(UTC) + timedelta(seconds=60),
        )
        session.add(job)
    return job, snapshot


class TrackedProvider(OpenAICompatibleProvider):
    instances = []

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.closed = False
        self.instances.append(self)

    async def aclose(self):
        await super().aclose()
        self.closed = True


def provider_factory(model, key, timeout):
    return TrackedProvider(base_url=model.base_url, model=model.model_id, api_key=key, timeout_sec=timeout)
