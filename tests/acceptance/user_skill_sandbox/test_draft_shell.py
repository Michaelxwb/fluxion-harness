"""TASK-009 规则验证：草稿 Shell 的包边界、变更白名单与工具唯一名。

纯逻辑/契约层（unit）；真实 bwrap + Console 回写由 S-02 覆盖，session 注入由 S-03 覆盖。
"""

from __future__ import annotations

import io
import json
import uuid
import zipfile
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient, MockTransport, Request, Response
from muad_agent_core.skill.draft_shell import (
    DRAFT_SHELL_ALLOWED_EXTENSIONS,
    DraftShellArchive,
    DraftShellError,
    DraftShellRequest,
    extract_draft_archive,
)
from muad_agent_runtime.application.skill_draft_client import ConsoleDraftShellClient
from muad_agent_runtime.application.skill_tools import (
    EXECUTE_TOOL,
    DraftShellContext,
    SkillToolSet,
)
from muad_common.draft_testing import draft_test_data_dir, draft_test_lock_key

from .draft_shell_support import MemoryDraftStore, build_runner

pytestmark = pytest.mark.unit


def _archive(files: dict[str, bytes], *, revision: int = 1) -> DraftShellArchive:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path, content in files.items():
            archive.writestr(path, content)
    return DraftShellArchive(
        draft_id="draft", revision=revision, zip_bytes=buffer.getvalue(), agent_id="agent"
    )


def _toolset(tmp_path: Path) -> tuple[SkillToolSet, MemoryDraftStore]:
    store = MemoryDraftStore(_archive({"SKILL.md": b"---\nname: probe\ndescription: probe\n---\n"}).zip_bytes)
    runner = build_runner(store, temp_root=str(tmp_path), runner_factory=_never_runner_factory)
    context = DraftShellContext(
        tenant_id="tenant",
        owner_user_id=str(uuid.uuid4()),
        agent_id=str(uuid.uuid4()),
        run_id=str(uuid.uuid4()),
    )
    return SkillToolSet(
        cache=object(),  # type: ignore[arg-type]
        skills=(),
        timeout_sec=10.0,
        draft_shell=runner,
        draft_shell_context=context,
    ), store


def _never_runner_factory() -> Any:
    class _Never:
        name = "never"

        async def run(self, spec: Any) -> Any:
            raise AssertionError("validation must happen before the sandbox runs")

    return _Never()


def test_allowed_extensions_match_console_validator() -> None:
    """变更白名单与 Console 导入校验同源，避免「能执行但不能回写」的规则错位。"""
    from muad_console_platform.infrastructure.skill_validator import ALLOWED_EXTENSIONS

    assert DRAFT_SHELL_ALLOWED_EXTENSIONS == ALLOWED_EXTENSIONS


def test_archive_rejects_path_traversal(tmp_path: Path) -> None:
    archive = _archive({"../escape.txt": b"nope"})
    with pytest.raises(DraftShellError) as captured:
        extract_draft_archive(
            archive,
            tmp_path / "draft",
            max_files=100,
            max_total_bytes=10 * 1024 * 1024,
            max_file_bytes=1024 * 1024,
        )
    assert captured.value.code == "DRAFT_PATH_INVALID"
    assert not (tmp_path / "escape.txt").exists()


def test_archive_rejects_symlink_entry(tmp_path: Path) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        info = zipfile.ZipInfo("link")
        info.external_attr = 0o120777 << 16
        archive.writestr(info, b"/etc/passwd")
        archive.writestr("SKILL.md", "---\nname: probe\n---\n")
    archive = DraftShellArchive(draft_id="draft", revision=1, zip_bytes=buffer.getvalue(), agent_id="agent")

    with pytest.raises(DraftShellError) as captured:
        extract_draft_archive(
            archive,
            tmp_path / "draft",
            max_files=100,
            max_total_bytes=10 * 1024 * 1024,
            max_file_bytes=1024 * 1024,
        )
    assert captured.value.code == "DRAFT_PATH_INVALID"


def test_archive_entry_limit_is_enforced(tmp_path: Path) -> None:
    archive = _archive({"a.txt": b"1", "b.txt": b"2", "SKILL.md": b"---\nname: probe\n---\n"})
    with pytest.raises(DraftShellError) as captured:
        extract_draft_archive(
            archive,
            tmp_path / "draft",
            max_files=2,
            max_total_bytes=10 * 1024 * 1024,
            max_file_bytes=1024 * 1024,
        )
    assert captured.value.code == "DRAFT_QUOTA_EXCEEDED"


async def test_tool_name_is_unique_and_skill_key_is_rejected(tmp_path: Path) -> None:
    toolset, _store = _toolset(tmp_path)
    names = {definition.name for definition in toolset.registry().list()}
    assert names == {EXECUTE_TOOL}

    result = json.loads(
        await toolset.execute(
            {"target": "draft", "skill_key": "legacy", "command": "echo nope"}, call_id="call-legacy"
        )
    )
    assert result["error"]["code"] == "COMMON_VALIDATION_ERROR"


def test_draft_shell_request_defaults_are_one_shot() -> None:
    request = DraftShellRequest(
        tenant_id="tenant", owner_user_id="user", draft_id="draft", command="python scripts/run.py"
    )
    assert request.cwd == "."
    assert request.timeout_sec == 60.0


@pytest.mark.parametrize("part", ["draft-tests", "draft", "data"])
def test_test_data_mount_rejects_symlinked_ancestors(tmp_path: Path, part: str) -> None:
    draft_id = str(uuid.uuid4())
    workspace, outside = tmp_path / "workspace", tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    target = {
        "draft-tests": workspace / "draft-tests",
        "draft": workspace / "draft-tests" / draft_id,
        "data": workspace / "draft-tests" / draft_id / "data",
    }[part]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="regular directory"):
        draft_test_data_dir(workspace, draft_id)


def test_test_data_identity_is_canonical_and_scoped(tmp_path: Path) -> None:
    user, draft = str(uuid.uuid4()), str(uuid.uuid4())
    key = draft_test_lock_key("tenant", user, draft)
    assert key == draft_test_lock_key("tenant", user.upper(), draft.upper())
    assert (
        len(
            {
                key,
                draft_test_lock_key("other-tenant", user, draft),
                draft_test_lock_key("tenant", str(uuid.uuid4()), draft),
                draft_test_lock_key("tenant", user, str(uuid.uuid4())),
            }
        )
        == 4
    )
    assert draft_test_data_dir(tmp_path, draft).parent.name == draft
    with pytest.raises(ValueError):
        draft_test_data_dir(tmp_path, "../escape")


@pytest.mark.parametrize("agent_header", [None, "", "not-an-agent"])
async def test_archive_requires_trusted_agent_metadata(agent_header: str | None) -> None:
    def response(request: Request) -> Response:
        headers = {"X-Draft-Revision": "1"}
        if agent_header is not None:
            headers["X-Draft-Agent-Id"] = agent_header
        return Response(200, content=b"zip", headers=headers)

    async with AsyncClient(transport=MockTransport(response)) as http:
        client = ConsoleDraftShellClient("http://test", service_token="test", client=http)
        request = DraftShellRequest(
            tenant_id="tenant",
            owner_user_id=str(uuid.uuid4()),
            draft_id=str(uuid.uuid4()),
            command="unused",
        )
        with pytest.raises(DraftShellError) as captured:
            await client.fetch_archive(request)
    assert captured.value.code == "COMMON_INTERNAL_ERROR"


@pytest.mark.parametrize("reset_data", ["true", 1, None])
async def test_reset_data_requires_boolean(tmp_path: Path, reset_data: object) -> None:
    toolset, _ = _toolset(tmp_path)
    definition = toolset.registry().get(EXECUTE_TOOL)
    assert definition.input_schema["properties"]["reset_data"]["type"] == "boolean"
    result = json.loads(
        await toolset.execute(
            {"target": "draft", "draft_id": str(uuid.uuid4()), "command": "echo unused",
             "reset_data": reset_data},
            call_id="invalid-reset",
        )
    )
    assert result["error"]["code"] == "COMMON_VALIDATION_ERROR"
