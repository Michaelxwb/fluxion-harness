from __future__ import annotations

import pytest
from tests.runtime_helpers import runtime_context

from fluxion.protocols.a2a import A2AAdapter, A2AAuth, A2AError, StubA2APeer


@pytest.mark.asyncio
async def test_S_R11_minimal_a2a_request_response_trace_auth_contract_interoperates() -> None:
    context, _runtime = await runtime_context()
    peer = StubA2APeer(expected_token="token-a", response_body={"answer": "pong"})
    adapter = A2AAdapter(peer=peer, auth=A2AAuth(bearer_token="token-a"), timeout_ms=30_000)

    response = await adapter.request(context, target_agent_id="agent-b", body={"prompt": "ping"})

    assert response.ok is True
    assert response.body == {"answer": "pong"}
    assert peer.requests[0].tenant_id == context.snapshot.tenant_id
    assert peer.requests[0].trace_id == context.snapshot.trace_id
    assert peer.requests[0].auth == {"type": "bearer", "token": "token-a"}


@pytest.mark.asyncio
async def test_S_R11_a2a_auth_error_maps_to_stable_error_code() -> None:
    context, _runtime = await runtime_context()
    peer = StubA2APeer(expected_token="token-a", response_body={})
    adapter = A2AAdapter(peer=peer, auth=A2AAuth(bearer_token="wrong"), timeout_ms=30_000)

    with pytest.raises(A2AError) as exc_info:
        await adapter.request(context, target_agent_id="agent-b", body={})

    assert exc_info.value.code == "a2a_auth_failed"
