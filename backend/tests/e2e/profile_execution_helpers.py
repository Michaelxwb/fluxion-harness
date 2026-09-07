"""TASK-012 发布→执行闭环 helpers：profile 版本发布 + 定版本执行。"""

from __future__ import annotations

from typing import Any

from fluxion.resources import ResourceKind
from fluxion.services.runtime_app import RunRuntimeResult, RuntimeApplicationService
from tests.console_helpers import ConsoleTestStack, create_resource, publish_resource
from tests.runtime_helpers import seed_agent_definition


def profile_spec(*, max_rounds: int) -> dict[str, object]:
    return {
        "request_timeout_ms": 30_000,
        "max_retries": 1,
        "max_rounds": max_rounds,
        "concurrency": 1,
        "memory_budget_mb": 512,
        "default": True,
    }


async def publish_profile_version(
    stack: ConsoleTestStack, version: str, *, max_rounds: int
) -> None:
    await create_resource(
        stack.client,
        kind=ResourceKind.RUNTIME_PROFILE,
        resource_id="assistant",
        version=version,
        spec=profile_spec(max_rounds=max_rounds),
    )
    published = await publish_resource(
        stack.client,
        kind=ResourceKind.RUNTIME_PROFILE,
        resource_id="assistant",
        version=version,
        # base 语义：构建所基于的当前版本（v2 基于已发布的 v1）。
        expected_base_version="1",
    )
    assert published.status_code == 200, published.text


async def seed_execution_agent(stack: ConsoleTestStack) -> None:
    await seed_agent_definition(stack.store, provider_id="dev.echo", model_name="dev")


def _ids(char: str) -> tuple[str, str, str]:
    return (f"req_{char * 32}", f"trace_{char * 32}", f"exec_{char * 32}")


async def run_latest(
    service: RuntimeApplicationService, char: str
) -> RunRuntimeResult:
    from fluxion.services.runtime_app import RunRuntimeRequest

    request_id, trace_id, execution_id = _ids(char)
    return await service.run(
        RunRuntimeRequest(
            tenant_id="tenant-a",
            user_id="user-a",
            runtime_profile_id="assistant",
            agent_definition_id="assistant",
            session_id="session-effect",
            input_message="hello",
            request_id=request_id,
            trace_id=trace_id,
            execution_id=execution_id,
        )
    )


async def spec_json_of(stack: ConsoleTestStack, version: str) -> dict[str, Any]:
    row = await stack.store.get(
        ResourceKind.RUNTIME_PROFILE, "assistant", tenant_id="tenant-a", version=version
    )
    assert row is not None
    return dict(row.spec_json)
