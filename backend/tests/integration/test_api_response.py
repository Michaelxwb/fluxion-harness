from __future__ import annotations

import pytest
from tests.console_helpers import console_stack, create_resource, publish_resource, tenant_headers

from fluxion.resources import ResourceKind


@pytest.mark.asyncio
async def test_S_C111_response_factory_sets_envelope_and_request_id() -> None:
    async with console_stack() as stack:
        await create_resource(
            stack.client,
            kind=ResourceKind.RUNTIME_PROFILE,
            resource_id="assistant",
            request_id="req-S-C111-create",
        )
        await publish_resource(
            stack.client,
            kind=ResourceKind.RUNTIME_PROFILE,
            resource_id="assistant",
            request_id="req-S-C111-publish",
        )
        response = await stack.client.get(
            "/api/v1/resources/runtime_profile/assistant",
            headers=tenant_headers(request_id="req-S-C111-detail"),
        )

        payload = response.json()
        assert response.status_code == 200
        assert response.headers["X-Request-ID"] == "req-S-C111-detail"
        assert set(payload) == {"code", "message", "data", "request_id"}
        assert payload["code"] == 0
        assert payload["message"] == "success"
        assert payload["request_id"] == "req-S-C111-detail"
        assert payload["data"]["resource_id"] == "assistant"


@pytest.mark.asyncio
async def test_E_C110_exception_mapper_keeps_stable_envelope_without_stack() -> None:
    async with console_stack() as stack:
        response = await stack.client.get(
            "/api/v1/resources/runtime_profile/missing",
            headers=tenant_headers(request_id="req-E-C110-missing"),
        )

        payload = response.json()
        assert response.status_code == 404
        assert response.headers["X-Request-ID"] == "req-E-C110-missing"
        assert set(payload) == {"code", "message", "data", "request_id"}
        assert isinstance(payload["code"], int)
        assert payload["code"] != 0
        assert payload["request_id"] == "req-E-C110-missing"
        assert payload["data"] is None
        response_text = response.text.lower()
        assert "traceback" not in response_text
        assert "stack" not in response_text
