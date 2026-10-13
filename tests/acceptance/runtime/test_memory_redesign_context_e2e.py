"""E-01 deferred: actual Runtime/Console HTTP, PostgreSQL, Redis and model HTTP."""

import pytest

from tests.acceptance.runtime.conftest import _Server
from tests.acceptance.runtime.memory_context_e2e_support import (
    configure,
    fired_layers,
    model_probe,
    restore,
    seed_history,
)
from tests.acceptance.runtime.test_run_lifecycle import _fresh_conversation, _parse_sse, _payload
from tests.agent_runtime.memory_context_support import memory_text

pytestmark = pytest.mark.e2e


@pytest.mark.parametrize("layer", ["micro", "snip", "summary"])
def test_e01_memory_redesign_real_runtime_protects_prefix_and_refreshes_only_component(
    live_stack, http, layer
):
    from uuid import uuid4

    stack = live_stack
    key = "e01." + uuid4().hex
    conversation = _fresh_conversation(http, stack)
    seed_history(stack, conversation, key)
    probe, bodies, primary = model_probe(stack, key)
    server = _Server(probe)
    endpoint = server.start()
    original = configure(stack, endpoint, layer)
    try:
        response = http.post(
            stack.runtime_url + "/v1/runs",
            json=_payload(stack, "现在继续", conversation_id=conversation),
            headers=stack.runtime_headers(),
        )
        assert response.status_code == 200, response.text
        assert _parse_sse(response.text)[-1]["type"] == "run.completed"
        assert len(primary) == 2
        assert key in memory_text(primary[0]) and memory_text(primary[1]) is None
        for body in primary:
            prefix = []
            for item in body["messages"]:
                if item["role"] != "system":
                    break
                prefix.append(item["content"])
            assert "acceptance agent" in prefix[0]
            assert any("[历史摘要]" in text for text in prefix)
        assert layer in fired_layers(stack, conversation)
        if layer == "summary":
            assert len(bodies) > len(primary)
    finally:
        restore(stack, original)
        server.stop()
