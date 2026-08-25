from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from fluxion.services.runtime_app import (
    RunRuntimeRequest,
    RunRuntimeResult,
    RuntimeStreamEvent,
)


@dataclass(slots=True)
class RecordingRuntime:
    requests: list[RunRuntimeRequest] = field(default_factory=list)

    async def run(self, request: RunRuntimeRequest) -> RunRuntimeResult:
        self.requests.append(request)
        return RunRuntimeResult(
            request_id=request.request_id,
            trace_id=request.trace_id,
            execution_id=request.execution_id,
            service_instance_id="runtime-test",
            runtime_profile_id=request.runtime_profile_id,
            runtime_profile_version="1",
            output=f"echo: {request.input_message}",
            latency_ms=1.0,
            model_provider_id="test.echo",
        )

    async def stream(
        self, request: RunRuntimeRequest
    ) -> AsyncIterator[RuntimeStreamEvent]:
        yield RuntimeStreamEvent(
            event="started",
            data={"request_id": request.request_id, "message_id": request.request_id},
        )
        result = await self.run(request)
        yield RuntimeStreamEvent(event="completed", data=result.to_payload())
