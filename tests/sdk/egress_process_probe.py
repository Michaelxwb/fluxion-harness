"""Independent Runtime MCP component process used by lifecycle integration tests."""

import asyncio
import json
import os
import sys
import uuid

import httpx
from muad_agent_runtime.application.mcp_runtime_adapter import (
    McpRuntimeAdapter,
    McpServerDefinition,
    McpToolDefinition,
    McpToolError,
)
from muad_contracts.egress_budget import resolve_egress_budget
from muad_platform_sdk.platform_transport import McpBudgetScope
from muad_platform_sdk.request_budget import LocalRateLimitError, create_budget_controller


async def probe(endpoint: str) -> dict[str, object]:
    controller = create_budget_controller(
        resolve_egress_budget({"execution": {"max_requests": 3}}), segment_id=str(uuid.uuid4()),
    )
    assert controller is not None
    server = McpServerDefinition(uuid.uuid4(), "probe", endpoint, tools=[McpToolDefinition("search", "", {})])
    result: dict[str, object] = {"pid": os.getpid(), "segment": controller.segment_id}
    async with httpx.AsyncClient(trust_env=False) as client:
        adapter = McpRuntimeAdapter(audit_writer=None, client=client, budget=McpBudgetScope(controller))
        try:
            await adapter.execute_tool(
                server=server, tool=server.tools[0], arguments={}, policy_decision="ALLOW",
            )
        except McpToolError as exc:
            result.update(origin=exc.reason, status=exc.status_code, retry_after=exc.retry_after)
        try:
            await adapter.execute_tool(
                server=server, tool=server.tools[0], arguments={}, policy_decision="ALLOW",
            )
        except LocalRateLimitError as exc:
            result["second_origin"] = exc.origin
        result["used"] = controller.used
    return result


if __name__ == "__main__":
    print(json.dumps(asyncio.run(probe(sys.argv[1]))))
