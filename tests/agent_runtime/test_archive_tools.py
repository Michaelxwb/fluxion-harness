from __future__ import annotations

import hashlib
import io
import json
import uuid
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
from muad_agent_runtime.application.attachments.tool_results import (
    TOOL_RESULT_ARTIFACT_BYTES,
    ArtifactResultWriter,
)
from muad_agent_runtime.application.attachments.tools import AttachmentToolError, AttachmentToolSet
from muad_agent_runtime.application.executor import ExecutorRunContext, ToolCallRecorder
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
    with pytest.raises(ArchiveToolError):
        archive_files([{"path": "a", "content": ""}, {"path": "b", "content": ""}], max_files=1)


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


async def test_receipt_is_trimmed_to_the_effective_threshold(
    tenant: TenantContext, tmp_path: Path
) -> None:
    """回执裁剪线必须跟着**本次 Run 生效的**阈值走，而不是模块里的 8KB 常量。

    反例现场（TASK-011 之后）：外置判定读冻结配置、回执裁剪读常量 —— 一旦把
    `tool_result.persist_threshold_bytes` 调低，回执就会**自己**超过生效阈值被外置成引用，
    模型拿不到"已生成 ZIP…请调用 deliver_artifact"这句指引（`harness-skill` 的
    RULE-skill-001 警告的正是这个失效方式）。
    """
    run_id, conversation_id = await _seed_run(tenant)
    writer = OutputArtifactWriter(
        artifact_root=tmp_path,
        session_factory=get_session_factory,
        scope=OutputScope(tenant.tenant_id, run_id, conversation_id),
    )
    files = [{"path": str(index) + "文" * 300, "content": ""} for index in range(5)]

    tight = await ArchiveToolSet(writer, receipt_limit_bytes=1200).create_archive(
        {"filename": "tight.zip", "files": files}, call_id="tight"
    )
    default = await ArchiveToolSet(writer).create_archive(
        {"filename": "default.zip", "files": files}, call_id="default"
    )

    assert len(tight.encode("utf-8")) < 1200, "回执必须裁到生效阈值以下"
    assert json.loads(tight)["files"] == [], "阈值收紧 ⇒ 预览整段让位给那句 deliver_artifact 指引"
    assert len(default.encode("utf-8")) < 8192
    assert json.loads(default)["files"], "默认阈值下应当还能留几条预览（对照组非空转）"


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


# ------------------------------------------------------- 编码口径与错误类型（2026-10-03 review）


def test_utf8_capped_reassembles_multi_chunk_content(monkeypatch: pytest.MonkeyPatch) -> None:
    """分块编码不得改变结果。

    按**字符**切块，切片永远落在码位边界上，所以多字节字符与代理对（非 BMP）都不会被切坏。
    """
    monkeypatch.setattr(archive_tools, "_ENCODE_CHUNK_CHARS", 2)

    files = archive_files([{"path": "a", "content": "你好世界\U0001f389"}])

    assert files[0].data == "你好世界\U0001f389".encode("utf-8")


def test_oversized_content_is_not_fully_encoded(monkeypatch: pytest.MonkeyPatch) -> None:
    """超限内容必须**边编码边判**，不能先整段编码再拒。

    按字符数预检挡不住 CJK：字符数没超、编码后却可能有 3–4× 的字节。旧实现会先物化整个
    字节对象**再**被累计检查拒绝——纯放大，而这段编码跑在 `asyncio.to_thread` 的线程里，
    照样吃 worker 的内存。
    """
    encoded = 0
    real_utf8 = archive_tools._utf8

    def counting_utf8(value: str) -> bytes:
        nonlocal encoded
        encoded += len(value)
        return real_utf8(value)

    monkeypatch.setattr(archive_tools, "_utf8", counting_utf8)
    monkeypatch.setattr(archive_tools, "_ENCODE_CHUNK_CHARS", 10)
    monkeypatch.setattr(archive_tools, "ARCHIVE_BYTES_LIMIT", 300)

    # 字符 200（逃过快路径）但字节 600（远超上限）
    with pytest.raises(ArchiveToolError) as error:
        archive_files([{"path": "a", "content": "文" * 200}])

    assert error.value.code == "ARCHIVE_TOO_LARGE"
    assert 0 < encoded < 200, "必须真的编码了，但要在把整段编码完之前就停下"


async def test_archive_storage_failure_surfaces_the_archive_error(tmp_path: Path) -> None:
    """存储失败必须翻成 `ArchiveToolError` —— 与 `attachments.tools.write_artifact` **同口径**。

    直接放 `OutputArtifactError` 逸出的话，调用方拿到的是一个既不属于本工具、也没带本工具
    错误码的异常类型（`ARTIFACT_WRITE_FAILED` 这类码就丢了）。这里用**真实**存储失败触发：
    artifact 根被一个普通文件占住。
    """
    blocked_root = tmp_path / "not-a-directory"
    blocked_root.write_text("occupied", encoding="utf-8")
    writer = OutputArtifactWriter(
        artifact_root=blocked_root,
        session_factory=get_session_factory,
        scope=OutputScope("tenant", uuid.uuid4(), uuid.uuid4()),
    )

    with pytest.raises(ArchiveToolError) as error:
        await ArchiveToolSet(writer).create_archive(
            {"filename": "greeting.zip", "files": FILES}, call_id="archive"
        )

    assert error.value.code == "ARTIFACT_WRITE_FAILED"
    assert blocked_root.read_text(encoding="utf-8") == "occupied"


async def test_receipt_is_not_externalized_by_the_tool_result_wrapper(
    tenant: TenantContext, tmp_path: Path
) -> None:
    """回执**恰好不被外置**：模型看到的必须是完整回执，而不是 `{"artifact": {...}}` 预览。

    这条保证只有在**经过 executor 包装**之后才成立或可证伪 —— 直接调 handler 的用例绕过了
    `ToolCallRecorder` 的外置判定，于是"回执裁剪到外置阈值以下"与"外置阈值"同源这件事
    （提交 `720bffaf` ⑥）在测试里一直是断的。
    """
    run_id, conversation_id = await _seed_run(tenant)
    writer = OutputArtifactWriter(
        artifact_root=tmp_path / "artifacts",
        session_factory=get_session_factory,
        scope=OutputScope(tenant.tenant_id, run_id, conversation_id),
    )
    registry = ToolRegistry()
    ArchiveToolSet(writer).register(registry)
    definition = registry.get(CREATE_ARCHIVE_TOOL)
    handler = definition.handler
    assert handler is not None

    # JSON 会把引号转义成两个字符 ⇒ 三个预览就足以越过外置阈值，逼出回执裁剪
    files = [{"path": str(index) + '"' * 1000, "content": ""} for index in range(20)]
    arguments = {"filename": '"' * 1000 + ".zip", "files": files}

    tool_results_root = tmp_path / "tool-results"
    recorder = ToolCallRecorder(
        context=ExecutorRunContext(
            tenant_id=tenant.tenant_id,
            run_id=run_id,
            conversation_id=conversation_id,
            user_id=uuid.uuid4(),
        ),
        audit_writer=None,
        artifact_writer=ArtifactResultWriter(tool_results_root),
    )

    content = await recorder(definition, arguments, handler=handler, call_id="archive")

    receipt = json.loads(content)
    # 前置条件：**没裁剪**的回执确实越过了外置阈值 —— 否则这条用例什么也没证明
    untrimmed = {**receipt, "files": [item["path"] for item in files[:3]]}
    assert len(json.dumps(untrimmed, ensure_ascii=False).encode("utf-8")) >= TOOL_RESULT_ARTIFACT_BYTES
    assert len(receipt["files"]) < 3, "回执没有被裁剪过，说明前置条件已经不成立"

    assert len(content.encode("utf-8")) < TOOL_RESULT_ARTIFACT_BYTES, (
        "回执必须落在外置阈值之内，否则模型只会拿到预览"
    )
    assert "artifact" not in receipt, "回执被外置成了预览，模型看不到 artifact_id"
    assert receipt["file_count"] == 20
    assert not tool_results_root.exists(), "没有外置就不该落任何 tool result 文件"
