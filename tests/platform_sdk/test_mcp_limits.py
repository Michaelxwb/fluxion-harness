"""MCP receive limits are checked at the boundary, not after unbounded buffering."""

from __future__ import annotations

import pytest
from muad_platform_sdk.mcp_content import McpContentTooLarge, convert_content
from muad_platform_sdk.mcp_limits import McpLimits


async def test_content_item_and_total_limits_are_explicit() -> None:
    with pytest.raises(McpContentTooLarge):
        await convert_content(
            [{"type": "text", "text": "x"}] * 3, limits=McpLimits(max_content_items=2)
        )

    with pytest.raises(McpContentTooLarge):
        await convert_content(
            [{"type": "text", "text": "x" * 10}], limits=McpLimits(max_total_bytes=5)
        )


async def test_unsupported_content_is_visible_not_empty() -> None:
    result = await convert_content([{"type": "audio", "data": "..."}], limits=McpLimits())
    assert "unsupported MCP content: audio" in result.text
