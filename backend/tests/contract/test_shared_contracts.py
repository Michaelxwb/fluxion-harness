from __future__ import annotations

import pytest
from tests.console_helpers import console_stack, create_resource, publish_resource

from fluxion.resources import ResourceKind
from fluxion.services.runtime_app import RuntimeApplicationService
from fluxion.services.runtime_contracts import RunRuntimeRequest


@pytest.mark.asyncio
async def test_S_C109_console_resource_contract_is_runtime_compatible() -> None:
    async with console_stack() as stack:
        await create_resource(
            stack.client,
            kind=ResourceKind.RUNTIME_PROFILE,
            resource_id="assistant",
            request_id="req-S-C109-create",
        )
        await publish_resource(
            stack.client,
            kind=ResourceKind.RUNTIME_PROFILE,
            resource_id="assistant",
            request_id="req-S-C109-publish",
        )

        runtime = RuntimeApplicationService.create_dev_bundle(stack.store)
        result = await runtime.run(
            RunRuntimeRequest(
                tenant_id="tenant-a",
                user_id="user-a",
                runtime_profile_id="assistant",
                session_id="session-a",
                input_message="hello",
                request_id="req-S-C109-run",
            )
        )

        assert result.runtime_profile_version == "1"
        assert result.output == "console: hello"
