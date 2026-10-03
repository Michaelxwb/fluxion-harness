from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path
from uuid import UUID

import pytest
from muad_agent_core.tools import ToolEffect, ToolRegistry
from muad_agent_runtime.application.attachments import archive_tools
from muad_agent_runtime.application.attachments.archive_tools import (
    CREATE_ARCHIVE_TOOL,
    ArchiveToolError,
    ArchiveToolSet,
    archive_files,
    build_archive,
    validate_member_path,
)
from muad_agent_runtime.application.attachments.output_service import OutputArtifactWriter, OutputScope
from muad_agent_runtime.application.attachments.tools import AttachmentToolError, AttachmentToolSet
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import Artifact
from muad_console_platform.infrastructure.skill_validator import validated_package

from agent_runtime.conftest import TenantContext
from agent_runtime.test_attachment_tools import _seed_run

FILES = [
    {"path": "SKILL.md", "content": "---\nname: greeting\ndescription: fixed greeting\n---\nHello"},
    {"path": "muad.skill.json", "content": '{"runtime":"script","entrypoint":"scripts/run.mjs"}'},
    {"path": "scripts/run.mjs", "content": 'console.log("你好，见到你很高兴");'},
]


def test_archive_contains_only_explicit_files_and_preserves_bytes() -> None:
    data = build_archive(archive_files(FILES))
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert archive.namelist() == [item["path"] for item in FILES]
        assert archive.testzip() is None
        for item in FILES:
            assert archive.read(item["path"]) == item["content"].encode("utf-8")
    with validated_package(data) as package:
        assert package.default_key == "greeting"


@pytest.mark.parametrize(
    "path", ["/absolute", "../outside", "a/../b", "a//b", "./a", "a/", "C:/a", "a\\b", "a\x00b", "a\nb"]
)
def test_invalid_archive_paths_are_rejected(path: str) -> None:
    with pytest.raises(ArchiveToolError, match="路径"):
        archive_files([{"path": path, "content": "x"}])


@pytest.mark.parametrize("paths", [["a", "a"], ["a", "a/b"], ["a/b", "a"]])
def test_duplicate_or_conflicting_archive_paths_are_rejected(paths: list[str]) -> None:
    with pytest.raises(ArchiveToolError) as error:
        archive_files([{"path": path, "content": "x"} for path in paths])
    assert error.value.code == "ARCHIVE_PATH_CONFLICT"


@pytest.mark.parametrize(
    "files",
    [
        [],
        [{"path": "a", "artifact_id": "foreign"}],
        [{"path": "a", "content": 1}],
        [{"path": "a", "content": "\ud800"}],
    ],
)
def test_invalid_file_inputs_are_rejected(files: object) -> None:
    with pytest.raises(ArchiveToolError):
        archive_files(files)


def test_archive_limits_use_utf8_bytes_and_count(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(archive_tools, "ARCHIVE_BYTES_LIMIT", 5)
    with pytest.raises(ArchiveToolError) as error:
        archive_files([{"path": "a", "content": "你好"}])
    assert error.value.code == "ARCHIVE_TOO_LARGE"
    with pytest.raises(ArchiveToolError):
        build_archive(archive_files([{"path": "a", "content": ""}]))
    monkeypatch.setattr(archive_tools, "MAX_ARCHIVE_FILES", 1)
    with pytest.raises(ArchiveToolError):
        archive_files([{"path": "a", "content": ""}, {"path": "b", "content": ""}])


async def test_archive_tool_persists_a_real_run_owned_zip(tenant: TenantContext, tmp_path: Path) -> None:
    run_id, conversation_id = await _seed_run(tenant)
    writer = OutputArtifactWriter(
        artifact_root=tmp_path,
        session_factory=get_session_factory,
        scope=OutputScope(tenant.tenant_id, run_id, conversation_id),
    )
    registry = ToolRegistry()
    ArchiveToolSet(writer).register(registry)
    tool = registry.get(CREATE_ARCHIVE_TOOL)
    assert tool.effect is ToolEffect.WRITE
    assert tool.handler is not None
    result = json.loads(await tool.handler({"filename": "greeting.zip", "files": FILES}, call_id="archive"))
    assert result["media_type"] == "application/zip"
    assert "deliver_artifact" in result["message"]
    paths = [path for path in tmp_path.rglob("*") if path.is_file()]
    assert len(paths) == 1
    data = paths[0].read_bytes()
    assert result["checksum"] == "sha256:" + hashlib.sha256(data).hexdigest()
    assert result["size"] == len(data)
    async with get_session_factory()() as session:
        row = await session.get(Artifact, UUID(result["artifact_id"]))
        assert row is not None
        assert (row.tenant_id, row.run_id, row.conversation_id) == (tenant.tenant_id, run_id, conversation_id)
        assert row.metadata_json["filename"] == "greeting.zip"
    with validated_package(data) as package:
        assert package.default_key == "greeting"
    other = AttachmentToolSet(
        session_factory=get_session_factory,
        artifact_root=str(tmp_path),
        tenant_id="other-tenant",
        run_id=run_id,
        conversation_id=conversation_id,
    )
    with pytest.raises(AttachmentToolError) as error:
        await other.read_attachment({"artifact_id": result["artifact_id"]}, call_id="foreign")
    assert error.value.code == "ATTACHMENT_NOT_FOUND"


@pytest.mark.parametrize("padding,filename", [("文" * 300, "many.zip"), ('"' * 1000, '"' * 1000 + ".zip")])
async def test_many_files_keep_a_compact_receipt(
    tenant: TenantContext, tmp_path: Path, padding: str, filename: str
) -> None:
    run_id, conversation_id = await _seed_run(tenant)
    writer = OutputArtifactWriter(
        artifact_root=tmp_path,
        session_factory=get_session_factory,
        scope=OutputScope(tenant.tenant_id, run_id, conversation_id),
    )
    files = [{"path": str(index) + padding, "content": ""} for index in range(20)]
    receipt = await ArchiveToolSet(writer).create_archive(
        {"filename": filename, "files": files}, call_id="many"
    )
    result = json.loads(receipt)
    assert len(receipt.encode("utf-8")) < 8192
    assert result["file_count"] == 20
    assert result["files_truncated"] is True
    assert 1 <= len(result["files"]) <= 3
    assert result["files"] == [item["path"] for item in files[: len(result["files"])]]
    path = next(path for path in tmp_path.rglob("*") if path.is_file())
    with zipfile.ZipFile(path) as archive:
        assert archive.namelist() == [item["path"] for item in files]


@pytest.mark.parametrize("filename", ["greeting.txt", "../greeting.zip", "folder/greeting.zip", ".zip"])
def test_archive_filename_must_be_a_zip_basename(filename: str) -> None:
    with pytest.raises(ArchiveToolError):
        archive_tools.archive_filename(filename)


# ------------------------------------------------------- 路径规范化与跨平台冲突（2026-10-03）

def test_member_path_is_normalized_to_nfc() -> None:
    """NFC 与 NFD 是**同一个名字**，必须收敛，否则在 macOS/Windows 上解压会互相覆盖。

    不规范化的话 `é`(U+00E9) 与 `é`(e + U+0301) 会作为**两个成员**写进 ZIP；解压时谁覆盖谁
    取决于平台，而包作者完全看不出问题。规范化后二者撞成"重复路径"，在**创建时**就被拒。
    """
    nfc = "\u00e9.txt"  # é（单码位）
    nfd = "e\u0301.txt"  # e + 组合重音

    assert validate_member_path(nfd) == validate_member_path(nfc) == nfc

    with pytest.raises(ArchiveToolError) as exc:
        archive_files([{"path": nfc, "content": "a"}, {"path": nfd, "content": "b"}])

    assert exc.value.code == "ARCHIVE_PATH_CONFLICT"


def test_case_colliding_member_paths_are_rejected() -> None:
    """`A.txt` 与 `a.txt` 在大小写不敏感的文件系统上是**同一个文件** —— 解压时静默丢一份。

    这不是"严格与否"的取舍：允许它意味着包在 Linux 上正常、在 macOS/Windows 上**少一个文件**，
    而作者无从察觉。在创建时报错，作者改个名字即可。
    """
    with pytest.raises(ArchiveToolError) as exc:
        archive_files([{"path": "README.md", "content": "a"}, {"path": "readme.md", "content": "b"}])

    assert exc.value.code == "ARCHIVE_PATH_CONFLICT"
    assert "大小写冲突" in exc.value.message


@pytest.mark.parametrize(
    "path",
    [
        " ",  # 纯空白段
        "a/ /b",  # 中间的空格段
        "a\u200bb.txt",  # 零宽空格（Cf）：显示上不可见
        "gnp\u202epng",  # 双向覆盖符（Cf）：渲染出来的顺序与字节顺序相反 —— 显示欺骗
        "a\u0085b.txt",  # C1 控制符（Cc）：不在原来的 ord<32 / ==127 检查里
    ],
)
def test_invisible_and_blank_path_segments_are_rejected(path: str) -> None:
    """不可见字符不得进成员名。

    原来只挡 `ord < 32` 与 127（纯 ASCII），漏掉了 C1 与 `Cf`（零宽 / 双向覆盖）。带 U+202E 的
    文件名**看起来是** `gnp.png`、字节顺序其实是 `gnp`，是典型的显示欺骗；纯空白段则是异常成员名。
    """
    with pytest.raises(ArchiveToolError) as exc:
        archive_files([{"path": path, "content": "x"}])

    assert exc.value.code == "ARCHIVE_PATH_INVALID"
