"""草稿 Runtime 工具（skill_draft_*）：身份注入、契约透传与错误映射。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from muad_agent_core.skill.draft_shell import DraftShellError
from muad_agent_runtime.application.draft_tools import DRAFT_TOOL_NAMES, SkillDraftTools
from muad_agent_runtime.application.skill_draft_client import ConsoleSkillDraftClient


@dataclass(frozen=True)
class _Context:
    tenant_id: str = "tenant-a"
    owner_user_id: str = "4f2b6f2e-6c0a-4a3d-9f35-3f0f2f9cbe11"
    agent_id: str = "d0f0d0e0-1b1b-4c4c-8d8d-2a2a2a2a2a2a"
    run_id: str = "run-1"


class _FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def _record(self, name: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((name, args, kwargs))
        return {"ok": True, "call": name}

    async def create(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._record("create", args, kwargs)

    async def metadata(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._record("metadata", args, kwargs)

    async def read(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._record("read", args, kwargs)

    async def write(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._record("write", args, kwargs)

    async def delete(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._record("delete", args, kwargs)

    async def delete_file(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._record("delete_file", args, kwargs)

    async def review(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._record("review", args, kwargs)

    async def status(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._record("status", args, kwargs)

    async def submit(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._record("submit", args, kwargs)


def _tools(client: Any) -> SkillDraftTools:
    return SkillDraftTools(client, _Context())


def test_definitions_expose_one_action_tool() -> None:
    definitions = _tools(_FakeClient()).definitions()
    assert {item.name for item in definitions} == DRAFT_TOOL_NAMES == {"skill_draft"}
    schema = definitions[0].input_schema
    assert schema["required"] == ["action"]
    assert set(schema["properties"]["action"]["enum"]) == {
        "create",
        "get",
        "read",
        "write",
        "delete",
        "review",
        "status",
        "submit",
    }
    assert schema["additionalProperties"] is False


async def test_dispatch_routes_actions_to_the_console_client() -> None:
    client = _FakeClient()
    tools = _tools(client)
    await tools.dispatch({"action": "create", "key": "my-skill"}, call_id="c1")
    await tools.dispatch(
        {"action": "read", "draft_id": "draft-1", "path": "SKILL.md"}, call_id="c2"
    )
    assert [call[0] for call in client.calls] == ["create", "read"]


async def test_create_uses_run_identity_and_ignores_model_identity() -> None:
    client = _FakeClient()
    tools = _tools(client)
    payload = json.loads(
        await tools.create(
            {"key": "my-skill", "tenant_id": "other", "owner_user_id": "other"},
            call_id="c1",
        )
    )
    assert payload["call"] == "create"
    _, args, kwargs = client.calls[0]
    assert args == ()
    assert kwargs == {
        "tenant_id": "tenant-a",
        "owner_user_id": _Context().owner_user_id,
        "agent_id": _Context().agent_id,
        "key": "my-skill",
    }


async def test_write_forwards_revision_cas() -> None:
    client = _FakeClient()
    tools = _tools(client)
    await tools.write(
        {
            "draft_id": "draft-1",
            "path": "scripts/run.py",
            "content": "print('x')\n",
            "expected_revision": 7,
        },
        call_id="c1",
    )
    _, args, kwargs = client.calls[0]
    assert args == ("draft-1",)
    assert kwargs["path"] == "scripts/run.py"
    assert kwargs["expected_revision"] == 7
    assert kwargs["tenant_id"] == "tenant-a"


async def test_submit_binds_agent_and_passes_version() -> None:
    client = _FakeClient()
    tools = _tools(client)
    await tools.submit(
        {"draft_id": "draft-1", "expected_revision": 3, "version": "1.2.0"}, call_id="c1"
    )
    _, args, kwargs = client.calls[0]
    assert args == ("draft-1",)
    assert kwargs["expected_revision"] == 3
    assert kwargs["version"] == "1.2.0"
    assert kwargs["submitted_agent_id"] == _Context().agent_id
    assert kwargs["submitted_channel"] is None


async def test_draft_errors_surface_as_stable_error_results() -> None:
    class _Conflict(_FakeClient):
        async def write(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            raise DraftShellError("DRAFT_REVISION_CONFLICT", "stale revision")

    payload = json.loads(
        await _tools(_Conflict()).write(
            {"draft_id": "d", "path": "SKILL.md", "content": "x", "expected_revision": 1},
            call_id="c1",
        )
    )
    assert payload["error"]["code"] == "DRAFT_REVISION_CONFLICT"


async def test_write_requires_expected_revision() -> None:
    payload = json.loads(
        await _tools(_FakeClient()).write(
            {"draft_id": "d", "path": "SKILL.md", "content": "x"}, call_id="c1"
        )
    )
    assert payload["error"]["code"] == "COMMON_VALIDATION_ERROR"


async def test_client_uses_internal_service_header_and_contract_paths() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"data": {"revision": 2, "draft_id": "d-1"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = ConsoleSkillDraftClient("http://console:8000", service_token="tok", client=http)
        await client.write(
            "d-1",
            tenant_id="t",
            owner_user_id="u",
            path="SKILL.md",
            content="hello",
            expected_revision=1,
        )
        await client.status("d-1", tenant_id="t", owner_user_id="u")

    write, status = seen
    assert write.method == "PUT"
    assert write.url.path == "/internal/skills/drafts/d-1/files"
    assert write.headers["X-Internal-Service"] == "tok"
    assert json.loads(write.content)["expected_revision"] == 1
    assert status.method == "GET"
    assert status.url.path == "/internal/skills/drafts/d-1/status"
    assert status.url.params["owner_user_id"] == "u"


async def test_client_maps_console_error_envelope() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            409, json={"code": "DRAFT_REVISION_CONFLICT", "msg": "stale", "data": {}}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = ConsoleSkillDraftClient("http://console:8000", client=http)
        with pytest.raises(DraftShellError) as caught:
            await client.submit(
                "d-1",
                tenant_id="t",
                owner_user_id="u",
                expected_revision=1,
                version=None,
                default_script=None,
                submitted_agent_id=None,
                submitted_channel=None,
            )
    assert caught.value.code == "DRAFT_REVISION_CONFLICT"
