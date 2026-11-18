"""草稿导入：压缩包成员解出规则与归属/媒体类型边界。"""

from __future__ import annotations

import io
import uuid
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from muad_agent_runtime.application import draft_import as draft_import_module
from muad_agent_runtime.application.draft_import import (
    MAX_PACKAGE_BYTES,
    ArtifactPackageReader,
    PackageImportError,
    extract_package_members,
)
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import Artifact, Conversation, RunRecord

from .conftest import TenantContext


def _zip(members: list[tuple[str, bytes]], *, symlink: str | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in members:
            archive.writestr(name, data)
        if symlink is not None:
            info = zipfile.ZipInfo(symlink)
            info.external_attr = (0o120777 << 16)
            archive.writestr(info, b"../target")
    return buffer.getvalue()


def test_members_keep_bytes_and_skip_directories() -> None:
    data = _zip(
        [
            ("muad.skill.json", b'{"name":"weak-pwd-manager"}'),
            ("scripts/run.py", "print('中文')\n".encode()),
            ("assets/logo.png", bytes(range(256))),
            ("empty/", b""),
        ]
    )
    members = extract_package_members(data)
    assert [member.path for member in members] == [
        "muad.skill.json",
        "scripts/run.py",
        "assets/logo.png",
    ]
    assert members[1].data == "print('中文')\n".encode()
    # 二进制成员照原样保留，不被文本化
    assert members[2].data == bytes(range(256))


def test_empty_archive_yields_no_members() -> None:
    assert extract_package_members(_zip([])) == ()


@pytest.mark.parametrize("name", ["../escape.py", "/abs.py", "a\\b.py", "a//b.py", "a/./b.py"])
def test_illegal_member_paths_reject_the_whole_package(name: str) -> None:
    with pytest.raises(PackageImportError) as raised:
        extract_package_members(_zip([("ok.py", b"x"), (name, b"y")]))
    assert raised.value.code == "DRAFT_PATH_INVALID"


def test_symlink_member_rejects_the_whole_package() -> None:
    with pytest.raises(PackageImportError) as raised:
        extract_package_members(_zip([("ok.py", b"x")], symlink="link.py"))
    assert raised.value.code == "DRAFT_PATH_INVALID"
    assert "符号链接" in raised.value.message


def test_duplicate_member_path_rejects_the_whole_package() -> None:
    with pytest.raises(PackageImportError) as raised:
        extract_package_members(_zip([("run.py", b"a"), ("run.py", b"b")]))
    assert raised.value.code == "DRAFT_PATH_INVALID"


def test_corrupted_archive_is_an_explicit_error() -> None:
    with pytest.raises(PackageImportError) as raised:
        extract_package_members(b"not a zip")
    assert raised.value.code == "ATTACHMENT_EXTRACT_FAILED"


def test_cumulative_extract_limit_keeps_the_too_large_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """读取途中的累计上限不能被通用读失败包装成“解析失败”。"""
    monkeypatch.setattr(draft_import_module, "MAX_ARCHIVE_MEMBER_BYTES", 64)
    monkeypatch.setattr(draft_import_module, "MAX_ARCHIVE_TOTAL_BYTES", 100)
    with pytest.raises(PackageImportError) as raised:
        extract_package_members(_zip([("a.bin", b"a" * 60), ("b.bin", b"b" * 60)]))
    assert raised.value.code == "ATTACHMENT_TOO_LARGE"
    assert "累计" in raised.value.message


async def test_reader_rejects_non_archive_attachments() -> None:
    reader = ArtifactPackageReader(lambda: None, "/tmp/artifacts")

    async def _row(*args: object, **kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(media_type="image/png", size=10, storage_key="k")

    reader._row = _row  # type: ignore[method-assign]
    with pytest.raises(PackageImportError) as raised:
        await reader.read("a", tenant_id="t", owner_user_id="u")
    assert raised.value.code == "COMMON_VALIDATION_ERROR"
    assert "read_attachment" in raised.value.message


async def test_reader_rejects_oversize_archives() -> None:
    reader = ArtifactPackageReader(lambda: None, "/tmp/artifacts")

    async def _row(*args: object, **kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(
            media_type="application/zip", size=MAX_PACKAGE_BYTES + 1, storage_key="k"
        )

    reader._row = _row  # type: ignore[method-assign]
    with pytest.raises(PackageImportError) as raised:
        await reader.read("a", tenant_id="t", owner_user_id="u")
    assert raised.value.code == "ATTACHMENT_TOO_LARGE"


async def test_reader_treats_unresolvable_storage_key_as_missing(tmp_path: Path) -> None:
    """损坏的 storage_key 是可读性失败，不是未处理异常。"""
    reader = ArtifactPackageReader(lambda: None, tmp_path)

    async def _row(*args: object, **kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(
            media_type="application/zip", size=10, storage_key="../escape"
        )

    reader._row = _row  # type: ignore[method-assign]
    with pytest.raises(PackageImportError) as raised:
        await reader.read("a", tenant_id="t", owner_user_id="u")
    assert raised.value.code == "ATTACHMENT_NOT_FOUND"


@pytest.fixture
def artifact_root(tmp_path: Path) -> Path:
    root = tmp_path / "artifacts"
    root.mkdir()
    return root


async def _seed_artifact(
    tenant: TenantContext,
    root: Path,
    data: bytes,
    *,
    owner_user_id: uuid.UUID | None = None,
    run_owned: bool = False,
    deleted: bool = False,
) -> uuid.UUID:
    owner = owner_user_id or tenant.platform_user_id
    async with get_session_factory()() as session:
        conversation = Conversation(
            tenant_id=tenant.tenant_id,
            user_id=owner,
            agent_id=tenant.agent_id,
            status="ACTIVE",
            last_seq=0,
        )
        session.add(conversation)
        await session.flush()
        run = RunRecord(
            tenant_id=tenant.tenant_id,
            conversation_id=conversation.id,
            user_id=owner,
            agent_id=tenant.agent_id,
            status="COMPLETED",
            input_text="draft import fixture",
            trace_id=uuid.uuid4().hex,
            cancel_requested=False,
        )
        session.add(run)
        await session.flush()
        storage_key = f"inbound/{uuid.uuid4().hex}/0"
        target = root / storage_key
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        artifact = Artifact(
            id=uuid.uuid4(),
            tenant_id=tenant.tenant_id,
            run_id=run.id,
            conversation_id=None if run_owned else conversation.id,
            artifact_type="INBOUND_ARCHIVE",
            storage_key=storage_key,
            media_type="application/zip",
            size=len(data),
            checksum="sha256:" + uuid.uuid4().hex,
            metadata_json={"filename": "fixture.zip"},
            is_deleted=deleted,
        )
        session.add(artifact)
        await session.commit()
        return artifact.id


async def test_reader_reads_conversation_and_run_owned_artifacts(
    tenant: TenantContext, artifact_root
) -> None:
    """真实 PG + 真实文件：会话归属与 Run 归属两条路径都能取到字节。"""
    data = _zip([("SKILL.md", b"x")])
    reader = ArtifactPackageReader(get_session_factory, artifact_root)
    for artifact_id in (
        await _seed_artifact(tenant, artifact_root, data),
        await _seed_artifact(tenant, artifact_root, data, run_owned=True),
    ):
        assert await reader.read(
            str(artifact_id),
            tenant_id=tenant.tenant_id,
            owner_user_id=str(tenant.platform_user_id),
        ) == data


async def test_reader_hides_cross_tenant_other_user_and_soft_deleted_artifacts(
    tenant: TenantContext, artifact_root
) -> None:
    """越权与软删一律按“不存在”处理，错误信息不回显标识。"""
    data = _zip([("secret.txt", b"secret")])
    artifact_id = await _seed_artifact(tenant, artifact_root, data)
    other_user_id = await _seed_artifact(
        tenant, artifact_root, data, owner_user_id=uuid.uuid4()
    )
    deleted_id = await _seed_artifact(tenant, artifact_root, data, deleted=True)
    reader = ArtifactPackageReader(get_session_factory, artifact_root)
    for target, tenant_id, owner_user_id in (
        (artifact_id, f"other-{uuid.uuid4()}", tenant.platform_user_id),
        (other_user_id, tenant.tenant_id, tenant.platform_user_id),
        (deleted_id, tenant.tenant_id, tenant.platform_user_id),
    ):
        with pytest.raises(PackageImportError) as raised:
            await reader.read(
                str(target), tenant_id=tenant_id, owner_user_id=str(owner_user_id)
            )
        assert raised.value.code == "ATTACHMENT_NOT_FOUND"
        assert str(target) not in raised.value.message
