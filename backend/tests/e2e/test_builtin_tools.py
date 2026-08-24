from __future__ import annotations

from pathlib import Path

import pytest
from tests.runtime_helpers import runtime_context

from fluxion.runtime.builtin_tools import BuiltinToolConfig, register_builtin_tools
from fluxion.runtime.tools import ToolAuthorizationError, ToolResultStatus, ToolRuntime


@pytest.mark.asyncio
async def test_S_R15_builtin_tools_use_common_chain_and_enforce_file_allowlist(
    tmp_path: Path,
) -> None:
    allowed_root = tmp_path / "workspace"
    allowed_root.mkdir()
    (allowed_root / "note.txt").write_text("hello", encoding="utf-8")
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    context, _runtime = await runtime_context()
    tool_runtime = ToolRuntime()
    register_builtin_tools(
        tool_runtime,
        BuiltinToolConfig(allowed_roots=[allowed_root], write_approved=False),
    )

    allowed_tools = {
        "time.now",
        "calc.eval",
        "http.get",
        "file.read",
        "file.write",
        "file.list",
        "file.search",
    }
    common = {
        "user_grants": allowed_tools,
        "agent_allowlist": allowed_tools,
        "tenant_policy": allowed_tools,
    }
    now = await tool_runtime.call(context, "time.now", {}, **common)
    calc = await tool_runtime.call(context, "calc.eval", {"expression": "1 + 2 * 3"}, **common)
    read = await tool_runtime.call(
        context,
        "file.read",
        {"path": str(allowed_root / "note.txt")},
        **common,
    )

    assert now.status is ToolResultStatus.COMPLETED
    assert now.result is not None
    assert now.result["timezone"] == "UTC"
    assert calc.result == {"value": 7}
    assert read.result == {"content": "hello"}
    assert tool_runtime.descriptor("time.now").external_dependency is False
    assert tool_runtime.descriptor("calc.eval").external_dependency is False

    with pytest.raises(ToolAuthorizationError) as outside_error:
        await tool_runtime.call(context, "file.read", {"path": str(outside)}, **common)
    assert outside_error.value.code == "path_not_allowed"

    with pytest.raises(ToolAuthorizationError) as write_error:
        await tool_runtime.call(
            context,
            "file.write",
            {"path": str(allowed_root / "note.txt"), "content": "new"},
            **common,
        )
    assert write_error.value.code == "approval_required"


async def _granted_tool_runtime(
    tmp_path: Path | None,
) -> tuple[ToolRuntime, dict[str, set[str]]]:
    tool_runtime = ToolRuntime()
    register_builtin_tools(
        tool_runtime,
        BuiltinToolConfig(
            allowed_roots=[tmp_path] if tmp_path is not None else [],
            write_approved=False,
        ),
    )
    granted = {"http.get", "calc.eval"}
    common = {
        "user_grants": granted,
        "agent_allowlist": granted,
        "tenant_policy": granted,
    }
    return tool_runtime, common


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/file",
        "gopher://example.com/",
    ],
)
async def test_E_R16_http_get_rejects_non_http_schemes(tmp_path: Path, url: str) -> None:
    tool_runtime, common = await _granted_tool_runtime(tmp_path)
    context, _runtime = await runtime_context()
    with pytest.raises(ToolAuthorizationError) as exc:
        await tool_runtime.call(context, "http.get", {"url": url}, **common)
    assert exc.value.code == "scheme_not_allowed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000/health",
        "http://localhost:8000/health",
        "http://10.0.0.1/admin",
        "http://192.168.1.1/config",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]:8000/health",
        "http://[fc00::1]/admin",
    ],
)
async def test_E_R16_http_get_rejects_loopback_and_private_hosts(
    tmp_path: Path, url: str
) -> None:
    tool_runtime, common = await _granted_tool_runtime(tmp_path)
    context, _runtime = await runtime_context()
    with pytest.raises(ToolAuthorizationError) as exc:
        await tool_runtime.call(context, "http.get", {"url": url}, **common)
    assert exc.value.code == "host_not_allowed"


@pytest.mark.asyncio
async def test_E_R16_calc_eval_rejects_division_by_zero_and_boolean() -> None:
    tool_runtime, common = await _granted_tool_runtime(tmp_path=None)
    context, _runtime = await runtime_context()
    with pytest.raises(ValueError, match="division by zero"):
        await tool_runtime.call(context, "calc.eval", {"expression": "1 / 0"}, **common)
    with pytest.raises(ValueError, match="unsupported expression"):
        await tool_runtime.call(context, "calc.eval", {"expression": "True + 1"}, **common)
