"""TASK-006: v2 Console client transport paths/headers."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from muad_console_platform.infrastructure.runtime_memory_client import RuntimeMemoryClient
from muad_contracts.memory_admin import MemoryCommandRequest


def _item():
    now = datetime(2026, 11, 14, tzinfo=UTC).isoformat()
    return {
        "id": str(uuid4()),
        "memory_key": "v1.slot.preference.report_format.x",
        "memory_kind": "PREFERENCE",
        "predicate": "report_format",
        "scope_state": "ABSENT",
        "scope": {},
        "content": {"format": "PDF"},
        "title": "报告",
        "summary": "",
        "tags": [],
        "cardinality": "SINGLE",
        "priority": "REQUIRED",
        "author": "USER",
        "authority": "USER_EXPLICIT",
        "version": 1,
        "enabled": True,
        "expires_at": None,
        "last_confirmed_at": None,
        "status": "ACTIVE",
        "create_time": now,
        "update_time": now,
    }


@pytest.mark.asyncio
async def test_list_v2_and_command_paths_are_bounded():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"code": 0, "data": {"items": [_item()], "total": 1}})
        return httpx.Response(200, json={"code": 0, "data": {"status": "APPLIED"}})

    client = RuntimeMemoryClient("http://runtime", transport=httpx.MockTransport(handler))
    tenant, user = "tenant-v2", uuid4()
    items, total = await client.list_memory(tenant_id=tenant, user_id=user, page=2, page_size=10)
    assert total == 1 and items[0].memory_key.startswith("v1.slot.preference.")
    request = MemoryCommandRequest(actor_id=uuid4(), action="DELETE", memory_id=uuid4(), expected_version=1)
    await client.command(tenant_id=tenant, user_id=user, command_id="cmd-v2", request=request)
    await client.aclose()
    assert seen[0].url.path.endswith(f"/internal/admin/users/{user}/memory")
    assert seen[0].url.params["page"] == "2"
    assert seen[1].headers["Idempotency-Key"] == "cmd-v2"
    assert json.loads(seen[1].content)["action"] == "DELETE"
