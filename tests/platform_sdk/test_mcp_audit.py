"""MCP logical audit model is a plain, transport-independent fact."""

from __future__ import annotations

from muad_platform_sdk.mcp_audit import McpAuditEvent


def test_audit_event_carries_attempt_and_result_status() -> None:
    event = McpAuditEvent(
        server_key="crm",
        tool_name="lookup",
        call_id="c1",
        rpc_method="tools/call",
        request_id="2",
        http_status=200,
        rpc_error_code=None,
        is_error=False,
        sent=True,
        outcome_known=True,
        result_status="OK",
        error_code=None,
        latency_ms=3,
        attempt_id="a1",
    )
    assert event.result_status == "OK"
    assert event.attempt_id == "a1"
