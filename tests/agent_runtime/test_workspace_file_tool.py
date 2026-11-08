"""deliver_artifact(path=...)：workspace 文件校验与交付工具契约。"""

from __future__ import annotations

from pathlib import Path

import pytest
from muad_agent_core.tools import ToolRegistry
from muad_agent_runtime.application.attachments.tools import (
    ATTACHMENT_NOT_FOUND,
    ATTACHMENT_PATH_INVALID,
    DELIVER_ARTIFACT_TOOL,
    AttachmentToolError,
    AttachmentToolSet,
    resolve_workspace_file,
)


def _workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "ws"
    for relative in ("outputs/call-1/report.pdf", "skill-drafts/my-skill/SKILL.md", "data/state.json"):
        target = workspace / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("content", encoding="utf-8")
    return workspace


def test_resolves_files_under_outputs_and_skill_drafts(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    for relative in ("outputs/call-1/report.pdf", "skill-drafts/my-skill/SKILL.md", "data/state.json"):
        output = resolve_workspace_file(workspace, relative)
        assert output.relative_path == relative
        assert output.path.is_file()


@pytest.mark.parametrize("path", ["../outside.txt", "/etc/passwd", "..\\outside.txt", ""])
def test_rejects_paths_outside_the_agent_workspace(tmp_path: Path, path: str) -> None:
    workspace = _workspace(tmp_path)
    with pytest.raises(AttachmentToolError) as caught:
        resolve_workspace_file(workspace, path)
    assert caught.value.code == ATTACHMENT_PATH_INVALID


def test_rejects_symlink_escape_and_missing_file(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    (workspace / "link.txt").symlink_to(outside)

    with pytest.raises(AttachmentToolError) as escaped:
        resolve_workspace_file(workspace, "link.txt")
    assert escaped.value.code == ATTACHMENT_PATH_INVALID

    with pytest.raises(AttachmentToolError) as missing:
        resolve_workspace_file(workspace, "nope.txt")
    assert missing.value.code == ATTACHMENT_NOT_FOUND


def test_deliver_artifact_accepts_either_artifact_id_or_path() -> None:
    registry = ToolRegistry()
    AttachmentToolSet(
        session_factory=lambda: None,  # type: ignore[arg-type]
        artifact_root="/tmp/artifacts",
        tenant_id="tenant-a",
    ).register(registry)

    schema = registry.get(DELIVER_ARTIFACT_TOOL).input_schema
    assert set(schema["properties"]) == {"artifact_id", "path"}
    assert "required" not in schema
