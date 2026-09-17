import inspect
import logging

import muad_platform_sdk
from muad_platform_sdk import PlatformRequest, PlatformTarget
from muad_skill_sdk import HttpClient, PlatformClient, SkillContext, SkillUser, TaskClient


def test_skill_context_exposes_all_ports(skill_context: SkillContext) -> None:
    assert skill_context.user == SkillUser(user_id="user-1", tenant_id="tenant-1", display_name="User One")
    assert isinstance(skill_context.logger, logging.Logger)

    for port in ("artifact", "platform", "mcp", "task", "http"):
        assert getattr(skill_context, port) is not None


def test_skill_sdk_reuses_platform_sdk_client_protocol() -> None:
    assert PlatformClient is muad_platform_sdk.PlatformClient


def test_client_protocols_are_not_instantiable() -> None:
    for protocol in (PlatformClient, HttpClient, TaskClient):
        assert getattr(protocol, "_is_protocol", False) is True


def _http_timeout_parameter(method: str) -> inspect.Parameter:
    return inspect.signature(getattr(HttpClient, method)).parameters["timeout_sec"]


def test_http_methods_require_timeout_sec() -> None:
    for method in ("get", "post", "request"):
        parameter = _http_timeout_parameter(method)
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
        assert parameter.default is inspect.Parameter.empty


def test_task_map_requires_items_script_and_concurrency() -> None:
    parameters = inspect.signature(TaskClient.map).parameters

    for name in ("items", "script", "max_concurrency"):
        assert name in parameters
        assert parameters[name].kind is inspect.Parameter.KEYWORD_ONLY


async def test_context_platform_call(skill_context: SkillContext) -> None:
    result = await skill_context.platform.call(
        "mss",
        PlatformRequest(
            target=PlatformTarget(service="customer-service-mgr", operation="get_customer"),
            payload={"customer_id": "C-1001"},
        ),
    )

    assert result == {"platform_key": "mss", "payload": {"customer_id": "C-1001"}}


async def test_context_platform_request(skill_context: SkillContext) -> None:
    result = await skill_context.platform.request(
        "mss",
        "customer-service-mgr",
        "get_customer",
        {"customer_id": "C-1001"},
    )

    assert result == {
        "platform_key": "mss",
        "service": "customer-service-mgr",
        "operation": "get_customer",
        "payload": {"customer_id": "C-1001"},
    }


async def test_context_artifact_read_write(skill_context: SkillContext) -> None:
    await skill_context.artifact.write("skills/s1/a1/skill.zip", b"zip-bytes")

    assert await skill_context.artifact.read("skills/s1/a1/skill.zip") == b"zip-bytes"


async def test_context_mcp_call(skill_context: SkillContext) -> None:
    hits = await skill_context.mcp.call("knowledge", "search", {"q": "策略偏差"})

    assert hits == {"server": "knowledge", "tool": "search", "arguments": {"q": "策略偏差"}}


async def test_context_task_map(skill_context: SkillContext) -> None:
    children = await skill_context.task.map(
        items=[{"id": 1}, {"id": 2}],
        script="scripts/check_one.py",
        max_concurrency=2,
    )

    assert list(children) == [
        {"script": "scripts/check_one.py", "item": {"id": 1}},
        {"script": "scripts/check_one.py", "item": {"id": 2}},
    ]


async def test_context_http_get_post_request(skill_context: SkillContext) -> None:
    url = "https://mss-internal.example/api/devices"

    get_response = await skill_context.http.get(url, timeout_sec=10.0)
    assert get_response.status_code == 200
    assert get_response.body == url.encode()

    post_response = await skill_context.http.post(url, timeout_sec=10.0, body='{"a": 1}')
    assert post_response.body == b'{"a": 1}'

    request_response = await skill_context.http.request("GET", url, timeout_sec=10.0)
    assert request_response.body == f"GET:{url}".encode()
