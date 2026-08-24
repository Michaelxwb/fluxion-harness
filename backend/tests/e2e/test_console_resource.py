from __future__ import annotations

import pytest
from tests.console_helpers import console_stack, create_resource, publish_resource

from fluxion.resources import ResourceKind, ResourceStatus


@pytest.mark.asyncio
async def test_S_C101_create_runtime_profile_publishes_without_pod_action() -> None:
    async with console_stack() as stack:
        draft = await create_resource(
            stack.client,
            kind=ResourceKind.RUNTIME_PROFILE,
            resource_id="assistant",
            request_id="req-S-C101-create",
        )
        published = await publish_resource(
            stack.client,
            kind=ResourceKind.RUNTIME_PROFILE,
            resource_id="assistant",
            request_id="req-S-C101-publish",
        )

        assert draft.status_code == 200
        assert draft.json()["code"] == 0
        assert published.status_code == 200
        payload = published.json()
        assert payload["code"] == 0
        assert payload["data"]["resource_id"] == "assistant"
        assert payload["data"]["version"] == "1"
        assert payload["data"]["status"] == "published"
        assert payload["data"]["kubernetes_workload_created"] is False
        stored = await stack.store.get(
            ResourceKind.RUNTIME_PROFILE,
            "assistant",
            tenant_id="tenant-a",
            version="1",
        )
        assert stored is not None
        assert stored.status is ResourceStatus.PUBLISHED
        assert stack.service.deployment_actions == ()
