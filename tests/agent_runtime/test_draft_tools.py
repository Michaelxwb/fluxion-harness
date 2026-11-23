"""草稿 Runtime 工具（skill_draft_*）：身份注入、契约透传与错误映射。"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from muad_agent_core.skill.draft_shell import DraftShellChange, DraftShellError
from muad_agent_core.tools import ToolCall, ToolError
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
        # Console 的 draft_view 用 `id`；工具层再补 `draft_id` 别名
        return {"ok": True, "call": name, "id": "draft-1", "revision": 4}

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

    async def apply(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("apply", args, kwargs))
        changes = kwargs.get("changes", ())
        return {
            "revision": 5,
            "file_count": len(changes),
            "total_size": sum(len(change.content) for change in changes),
            "changed_files": [change.path for change in changes],
        }


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
        "import",
    }
    assert "artifact_id" in schema["properties"]
    assert schema["additionalProperties"] is False


async def _payload(coro):
    """Parse action output, including typed ToolError bodies raised by the action."""
    try:
        value = await coro
    except ToolError as exc:
        return json.loads(exc.content())
    return json.loads(value)


async def test_dispatch_routes_actions_to_the_console_client() -> None:
    client = _FakeClient()
    tools = _tools(client)
    await tools.dispatch(
        {"action": "create", "key": "my-skill"},
        call=ToolCall(call_id="c1", name="skill_draft"),
    )
    await tools.dispatch(
        {"action": "read", "draft_id": "draft-1", "path": "SKILL.md"},
        call=ToolCall(call_id="c2", name="skill_draft"),
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


async def test_key_addresses_the_draft_without_a_uuid() -> None:
    """模型只给 key（skill 名）时：先幂等解析 draft_id，再执行目标动作。"""
    client = _FakeClient()
    tools = _tools(client)
    payload = json.loads(
        await tools.status({"action": "status", "key": "weather-query"}, call_id="c1")
    )
    assert [call[0] for call in client.calls] == ["create", "status"]
    _, args, kwargs = client.calls[1]
    assert args == ("draft-1",)
    assert kwargs["tenant_id"] == "tenant-a"
    assert payload["call"] == "status"


async def test_key_resolution_supplies_the_current_revision_for_write_and_submit() -> None:
    """按 key 写入/提交时，revision 默认取解析到的当前值，模型不必先猜数字。"""
    client = _FakeClient()
    tools = _tools(client)
    await tools.write(
        {"key": "weather-query", "path": "SKILL.md", "content": "x"}, call_id="c1"
    )
    await tools.submit({"key": "weather-query", "version": "1.0.3"}, call_id="c2")
    write = client.calls[1]
    submit = client.calls[3]
    assert write[1] == ("draft-1",) and write[2]["expected_revision"] == 4
    assert submit[1] == ("draft-1",) and submit[2]["expected_revision"] == 4


async def test_create_and_get_expose_the_draft_id_alias() -> None:
    """Console 返回 `id`；工具输出同时给 `draft_id`，模型不用猜键名。"""
    client = _FakeClient()
    tools = _tools(client)
    created = json.loads(await tools.create({"key": "weather-query"}, call_id="c1"))
    fetched = json.loads(await tools.get({"key": "weather-query"}, call_id="c2"))
    assert created["draft_id"] == "draft-1" and created["id"] == "draft-1"
    assert fetched["draft_id"] == "draft-1"


async def test_missing_draft_id_and_key_names_both_options() -> None:
    payload = await _payload(
        _tools(_FakeClient()).status({"action": "status"}, call_id="c1")
    )
    assert payload["error"]["code"] == "COMMON_VALIDATION_ERROR"
    assert "draft_id" in payload["error"]["message"] and "key" in payload["error"]["message"]


def test_description_states_path_base_and_submit_keeps_the_draft() -> None:
    description = _tools(_FakeClient()).definitions()[0].description
    assert "relative to the draft root" in description
    assert 'path=""' in description
    assert "does not release or delete" in description


async def test_read_defaults_to_the_draft_root_listing() -> None:
    client = _FakeClient()
    await _tools(client).read({"draft_id": "draft-1"}, call_id="c1")
    _, _, kwargs = client.calls[0]
    assert kwargs["path"] == ""
    assert kwargs["agent_id"] == _Context().agent_id


async def test_get_and_status_use_the_run_agent_scope() -> None:
    client = _FakeClient()
    tools = _tools(client)
    await tools.get({"draft_id": "draft-1"}, call_id="c1")
    await tools.status({"draft_id": "draft-1"}, call_id="c2")
    assert [call[0] for call in client.calls] == ["metadata", "status"]
    for _, _, kwargs in client.calls:
        assert kwargs["tenant_id"] == "tenant-a"
        assert kwargs["owner_user_id"] == _Context().owner_user_id
        assert kwargs["agent_id"] == _Context().agent_id


async def test_draft_id_and_key_must_identify_the_same_draft() -> None:
    """同时给出 draft_id 与 key 时先校验归属；不一致显式报错，不静默选一个。"""

    class _Keyed(_FakeClient):
        async def metadata(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            self.calls.append(("metadata", args, kwargs))
            draft_id = str(args[0]) if args else ""
            key = "weather-query" if draft_id == "draft-1" else "other-skill"
            return {"id": draft_id, "key": key, "revision": 3}

    client = _Keyed()
    payload = await _payload(
        _tools(client).get(
            {"draft_id": "draft-2", "key": "weather-query"}, call_id="c1"
        )
    )
    assert payload["error"]["code"] == "COMMON_VALIDATION_ERROR"
    assert "draft-2" in payload["error"]["message"]
    assert "weather-query" in payload["error"]["message"]
    assert [call[0] for call in client.calls] == ["metadata"]

    client = _Keyed()
    matched = json.loads(
        await _tools(client).get(
            {"draft_id": "draft-1", "key": "weather-query"}, call_id="c2"
        )
    )
    assert matched["draft_id"] == "draft-1"
    assert [call[0] for call in client.calls] == ["metadata", "metadata"]


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
            raise DraftShellError(
                "DRAFT_FILE_NOT_FOUND",
                "draft file not found",
                data={"path": "SKILL.md", "draft_id": "d", "repair": "read the root"},
            )

    payload = await _payload(
        _tools(_Conflict()).write(
            {"draft_id": "d", "path": "SKILL.md", "content": "x", "expected_revision": 1},
            call_id="c1",
        )
    )
    assert payload["error"]["code"] == "DRAFT_FILE_NOT_FOUND"
    assert payload["error"]["detail"] == {
        "path": "SKILL.md",
        "draft_id": "d",
        "repair": "read the root",
    }


async def test_write_requires_expected_revision() -> None:
    payload = await _payload(
        _tools(_FakeClient()).write(
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
            agent_id="a",
            path="SKILL.md",
            content="hello",
            expected_revision=1,
        )
        await client.status("d-1", tenant_id="t", owner_user_id="u", agent_id="a")

    write, status = seen
    assert write.method == "PUT"
    assert write.url.path == "/internal/skills/drafts/d-1/files"
    assert write.headers["X-Internal-Service"] == "tok"
    assert json.loads(write.content)["expected_revision"] == 1
    assert status.method == "GET"
    assert status.url.path == "/internal/skills/drafts/d-1/status"
    assert status.url.params["owner_user_id"] == "u"
    assert status.url.params["agent_id"] == "a"


async def test_client_apply_encodes_binary_members_and_keeps_text_members() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"data": {"revision": 2}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = ConsoleSkillDraftClient("http://console:8000", client=http)
        await client.apply(
            "d-1",
            tenant_id="t",
            owner_user_id="u",
            agent_id="a",
            expected_revision=1,
            changes=(
                DraftShellChange(path="SKILL.md", content="中文".encode()),
                DraftShellChange(path="assets/logo.bin", content=bytes(range(256))),
            ),
        )

    body = json.loads(seen[0].content)
    assert body["changes"][0] == {"path": "SKILL.md", "content": "中文"}
    assert "content" not in body["changes"][1]
    assert base64.b64decode(body["changes"][1]["content_base64"]) == bytes(range(256))


async def test_client_maps_console_error_envelope() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            409,
            json={
                "code": "DRAFT_REVISION_CONFLICT",
                "msg": "stale",
                "data": {"draft_id": "d-1", "repair": "refresh and retry"},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = ConsoleSkillDraftClient("http://console:8000", client=http)
        with pytest.raises(DraftShellError) as caught:
            await client.submit(
                "d-1",
                tenant_id="t",
                owner_user_id="u",
                agent_id="a",
                expected_revision=1,
                version=None,
                default_script=None,
                submitted_agent_id=None,
                submitted_channel=None,
            )
    assert caught.value.code == "DRAFT_REVISION_CONFLICT"
    assert caught.value.data == {"draft_id": "d-1", "repair": "refresh and retry"}


class _FakePackages:
    """导入用的附件读取器替身：直接返回构造好的压缩包字节。"""

    def __init__(self, data: bytes | Exception) -> None:
        self._data = data
        self.calls: list[tuple[Any, dict[str, Any]]] = []

    async def read(self, artifact_id: object, **kwargs: Any) -> bytes:
        self.calls.append((artifact_id, kwargs))
        if isinstance(self._data, Exception):
            raise self._data
        return self._data


def _zip_bytes(members: list[tuple[str, bytes]]) -> bytes:
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in members:
            archive.writestr(name, data)
    return buffer.getvalue()


def _import_tools(client: Any, packages: Any) -> SkillDraftTools:
    return SkillDraftTools(client, _Context(), packages=packages)


async def test_import_writes_every_member_in_one_cas() -> None:
    """导入 = 一次 apply：包内文件逐个成 change，revision 走 key 解析到的当前值。"""
    client = _FakeClient()
    packages = _FakePackages(
        _zip_bytes([("SKILL.md", "# 用这个包写的 skill\n".encode()), ("scripts/run.py", b"print(1)\n")])
    )
    payload = json.loads(
        await _import_tools(client, packages).import_package(
            {"key": "weak-pwd-manager", "artifact_id": "artifact-1"}, call_id="c1"
        )
    )
    assert [call[0] for call in client.calls] == ["create", "apply"]
    _, args, kwargs = client.calls[1]
    assert args == ("draft-1",)
    assert kwargs["expected_revision"] == 4
    assert kwargs["tenant_id"] == "tenant-a"
    assert [change.path for change in kwargs["changes"]] == ["SKILL.md", "scripts/run.py"]
    assert packages.calls == [
        ("artifact-1", {"tenant_id": "tenant-a", "owner_user_id": _Context().owner_user_id})
    ]
    assert payload["draft_id"] == "draft-1"
    assert payload["artifact_id"] == "artifact-1"
    assert payload["imported_files"] == ["SKILL.md", "scripts/run.py"]
    assert payload["revision"] == 5


async def test_import_with_draft_id_fetches_the_current_revision() -> None:
    """按 draft_id 寻址时解析不出 revision —— 先取元信息，再由 apply 的 CAS 兜并发。"""
    client = _FakeClient()
    tools = _import_tools(client, _FakePackages(_zip_bytes([("SKILL.md", b"x")])))
    await tools.import_package({"draft_id": "draft-1", "artifact_id": "artifact-1"}, call_id="c1")
    assert [call[0] for call in client.calls] == ["metadata", "apply"]
    assert client.calls[1][2]["expected_revision"] == 4


async def test_import_surfaces_reader_errors_without_writing() -> None:
    from muad_agent_runtime.application.draft_import import PackageImportError

    client = _FakeClient()
    tools = _import_tools(
        client,
        _FakePackages(PackageImportError("COMMON_VALIDATION_ERROR", "只有压缩包能导入草稿")),
    )
    payload = await _payload(
        tools.import_package(
            {"key": "weak-pwd-manager", "artifact_id": "artifact-1"}, call_id="c1"
        )
    )
    assert payload["error"]["code"] == "COMMON_VALIDATION_ERROR"
    assert "压缩包" in payload["error"]["message"]
    assert [call[0] for call in client.calls] == ["create"]


async def test_import_rejects_an_illegal_member_before_any_write() -> None:
    client = _FakeClient()
    tools = _import_tools(client, _FakePackages(_zip_bytes([("../escape.py", b"x")])))
    payload = await _payload(
        tools.import_package(
            {"key": "weak-pwd-manager", "artifact_id": "artifact-1"}, call_id="c1"
        )
    )
    assert payload["error"]["code"] == "DRAFT_PATH_INVALID"
    assert [call[0] for call in client.calls] == ["create"]


async def test_import_without_reader_is_an_explicit_error() -> None:
    payload = await _payload(
        _tools(_FakeClient()).import_package(
            {"key": "weak-pwd-manager", "artifact_id": "artifact-1"}, call_id="c1"
        )
    )
    assert payload["error"]["code"] == "COMMON_INTERNAL_ERROR"
