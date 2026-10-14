"""Real Gateway client, Runtime HTTP API/default executor and isolated PG."""

import asyncio
import socket
from contextlib import asynccontextmanager
from dataclasses import dataclass
from uuid import UUID, uuid4

import httpx
import sqlalchemy as sa
import uvicorn
from fastapi import FastAPI, Request
from muad_agent_core.agent.continuation import RunnerCheckpoint
from muad_agent_runtime.api.deps import get_run_service
from muad_agent_runtime.api.runs import router
from muad_agent_runtime.application.async_tools.supervisor import ExecutionSupervisor
from muad_agent_runtime.application.ports import NullPlatformSettingsClient
from muad_agent_runtime.application.run_service import RunService, snapshot_policy
from muad_agent_runtime.infrastructure.console_client import ConsoleCredentialsClient
from muad_agent_runtime.infrastructure.models.runtime import RunRecord, RuntimeSnapshot
from muad_api import install_api_foundation
from muad_contracts import (
    ResolveDefinitionRequest,
    ResolveDefinitionResponse,
    ResolveModelRequest,
    ResolveModelResponse,
    RunRequest,
)
from muad_contracts.platform_settings import MemoryPolicySettings
from muad_im_gateway.application.runtime_client import RuntimeClient
from pydantic import JsonValue
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.agent_runtime.memory_confirmation_support import (
    candidate,
    complete_round,
    model_endpoint,
    service,
    source,
)


class FrozenOnlyResolver:
    async def resolve(
        self,
        request: ResolveDefinitionRequest,
        *,
        tenant_id: str,
        trace_id: str = "",
    ) -> ResolveDefinitionResponse:
        raise AssertionError("resuming a frozen Run must not resolve a new definition")

    async def resolve_model(
        self,
        request: ResolveModelRequest,
        *,
        tenant_id: str,
        trace_id: str = "",
    ) -> ResolveModelResponse:
        raise AssertionError("resuming a frozen Run must not resolve a new model")


@dataclass
class ConfirmationStack:
    factory: async_sessionmaker[AsyncSession]
    run: RunRecord
    proposal_id: UUID
    gateway: RuntimeClient
    url: str
    captured: list[dict[str, JsonValue]]

    def request(self, text, *, message_id=None, user=None):
        return RunRequest.model_validate(
            {
                "agent_id": self.run.agent_id,
                "platform_user_id": user or self.run.user_id,
                "conversation_id": self.run.conversation_id,
                "channel": {"type": "WECOM", "bot_id": "fixture", "external_conversation_id": "fixture"},
                "message": {"id": message_id or uuid4().hex, "type": "text", "text": text},
            }
        )

    async def submit(self, text, *, message_id=None, user=None):
        return [
            event
            async for event in self.gateway.create_run(
                self.request(text, message_id=message_id, user=user), tenant_id=self.run.tenant_id
            )
        ]

    async def explicit(self, text, *, user=None, message_id=None, key=None):
        async with httpx.AsyncClient() as client:
            return await client.post(
                f"{self.url}/v1/runs/{self.run.id}/resume",
                headers={"X-Tenant-Id": self.run.tenant_id, **({"Idempotency-Key": key} if key else {})},
                json={
                    "platform_user_id": str(user or self.run.user_id),
                    "input": {"id": message_id or uuid4().hex, "text": text},
                },
            )


@asynccontextmanager
async def serve(app):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
    task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        for _ in range(100):
            if server.started:
                break
            await asyncio.sleep(0.01)
        assert server.started
        yield f"http://127.0.0.1:{listener.getsockname()[1]}"
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, timeout=5)
        listener.close()


def runtime_app(factory, supervisor, monkeypatch, credentials):
    from muad_agent_runtime.application import executor, run_service

    monkeypatch.setattr(run_service, "get_session_factory", lambda: factory)
    monkeypatch.setattr(executor, "get_session_factory", lambda: factory)
    app = FastAPI()
    install_api_foundation(app)
    app.include_router(router)

    async def bound_service():
        async with factory() as session:
            yield RunService(
                session,
                FrozenOnlyResolver(),
                "confirmation-http",
                NullPlatformSettingsClient(),
                supervisor=supervisor,
                credentials_client=credentials,
            )

    app.dependency_overrides[get_run_service] = bound_service
    return app


@asynccontextmanager
async def confirmation_stack(engine, monkeypatch, *, policy=None):
    factory, run, identity, submission = await source(engine)
    policy = policy or MemoryPolicySettings()
    memory = service(factory, identity, submission)
    await memory.propose(candidate(source_quote="记住蓝色"), call_id="remember-1")
    proposal_id = memory.suspend_request.interrupt_id
    await complete_round(factory, run)
    await memory.suspend(RunnerCheckpoint(turns=1, tool_calls=1))
    supervisor = ExecutionSupervisor()
    try:
        async with model_endpoint([]) as (model_url, captured):
            await frozen_snapshot(factory, run, model_url, policy)
            async with serve(credential_probe(run)) as credential_url:
                credentials = ConsoleCredentialsClient(credential_url)
                try:
                    async with serve(runtime_app(factory, supervisor, monkeypatch, credentials)) as url:
                        gateway = RuntimeClient(url)
                        try:
                            yield ConfirmationStack(factory, run, proposal_id, gateway, url, captured)
                        finally:
                            await gateway.aclose()
                finally:
                    await credentials.aclose()
    finally:
        await supervisor.close()


async def frozen_snapshot(factory, run, url, policy):
    from muad_agent_runtime.application.executor import ExecutionDefaults

    async with factory.begin() as session:
        row = await session.get(RuntimeSnapshot, run.snapshot_id)
        row.model_json = {**row.model_json, "base_url": url}
        row.agent_json = {**row.agent_json, "instructions": "fixed instructions"}
        row.skill_catalog_json = []
        row.policy_json = snapshot_policy(execution=ExecutionDefaults(memory_policy=policy))


async def rows(stack, model):
    async with stack.factory() as session:
        return list(await session.scalars(sa.select(model).where(model.tenant_id == stack.run.tenant_id)))


def credential_probe(run):
    app = FastAPI()

    @app.post("/internal/runtime/resolve-credentials")
    async def credentials(request: Request):
        body = await request.json()
        assert request.headers["X-Tenant-Id"] == run.tenant_id
        assert body["actor_user_id"] == str(run.user_id)
        assert body["execution_ref"] == {"type": "RUN", "id": str(run.id)}
        return {"code": "0", "data": {"model": {"api_key": "fixture-credential"}, "mcp_servers": []}}

    return app
