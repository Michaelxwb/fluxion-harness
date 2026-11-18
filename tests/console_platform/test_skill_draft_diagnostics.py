"""[TASK-015] Skill 草稿诊断、版本与 agent 作用域。

真实边界：`SkillDraftService` + `SkillDraftStore` + 真实 PostgreSQL；制品恢复走
`ARTIFACT_ROOT` 下的真实 zip，不 mock 文件系统或 ORM。
"""

from __future__ import annotations

import io
import shutil
import uuid
import zipfile
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from muad_api import AppError
from muad_common.workspaces import DRAFT_ACTIVITY_MARKER
from muad_console_platform.application.skill_draft_service import SkillDraftService
from muad_console_platform.infrastructure.db import get_session_factory
from muad_console_platform.infrastructure.models.control import Skill, SkillArtifact
from muad_console_platform.infrastructure.repositories.skill_draft_repository import (
    SkillDraftRepository,
)
from muad_console_platform.infrastructure.skill_artifact_store import (
    artifact_storage_key,
    write_artifact,
)
from muad_console_platform.infrastructure.skill_validator import checksum_of
from muad_contracts import SkillReviewStatus, SkillSource
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


@pytest.fixture()
def draft_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    monkeypatch.setenv("ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    return tmp_path


@pytest.fixture()
async def session(isolated_datastore: object) -> AsyncIterator[AsyncSession]:
    factory = get_session_factory()
    async with factory() as active:
        yield active


def _zip_bytes(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path, content in files.items():
            archive.writestr(path, content)
    return buffer.getvalue()


async def _seed_effective_artifact(
    session: AsyncSession,
    *,
    tenant_id: str,
    owner_user_id: uuid.UUID,
    key: str,
    files: dict[str, str],
    version: str = "1.0.0",
) -> SkillArtifact:
    skill = Skill(
        tenant_id=tenant_id,
        key=key,
        name=key,
        description="diagnostics",
        user_scope="SELECTED",
        enabled=True,
        source=SkillSource.USER,
        owner_user_id=owner_user_id,
    )
    session.add(skill)
    await session.flush()
    artifact_id = uuid.uuid4()
    storage_key = artifact_storage_key(skill.id, artifact_id)
    data = _zip_bytes(files)
    write_artifact(storage_key, data)
    artifact = SkillArtifact(
        id=artifact_id,
        skill_id=skill.id,
        version=version,
        checksum=checksum_of(data),
        storage_key=storage_key,
        frontmatter_json={"name": key},
        manifest_json={"files": list(files), "file_count": len(files)},
        execution_mode="SYNC",
        instructions="",
        package_size=len(data),
        validation_status="READY",
        created_by=owner_user_id,
        review_status=SkillReviewStatus.APPROVED,
        source=SkillSource.USER,
        submitted_by_user_id=owner_user_id,
    )
    session.add(artifact)
    await session.flush()
    skill.current_artifact_id = artifact.id
    await session.flush()
    return artifact


async def _create_draft(
    session: AsyncSession,
    *,
    tenant_id: str | None = None,
    owner_user_id: uuid.UUID | None = None,
    agent_id: uuid.UUID | None = None,
    key: str = "diag-skill",
) -> tuple[SkillDraftService, str, uuid.UUID, uuid.UUID, uuid.UUID]:
    service = SkillDraftService(session)
    tenant = tenant_id or f"tenant-{uuid.uuid4()}"
    owner = owner_user_id or uuid.uuid4()
    agent = agent_id or uuid.uuid4()
    created = await service.create(tenant, owner, agent, key)
    await session.flush()
    return service, tenant, owner, agent, uuid.UUID(created["id"])


async def test_get_and_status_report_changes_and_effective_artifact(
    session: AsyncSession, draft_roots: Path
) -> None:
    service, tenant, owner, agent, draft_id = await _create_draft(session)

    created = await service.metadata(tenant, owner, agent, draft_id)
    assert created["has_unsubmitted_changes"] is True
    assert created["effective_artifact_id"] is None
    assert created["effective_artifact_version"] is None

    await service.write(
        tenant,
        owner,
        agent,
        draft_id,
        path="SKILL.md",
        content="# draft\n",
        expected_revision=1,
    )
    await session.flush()

    artifact = await _seed_effective_artifact(
        session,
        tenant_id=tenant,
        owner_user_id=owner,
        key="diag-skill",
        files={"SKILL.md": "# submitted\n"},
        version="2.3.4",
    )
    status = await service.status(tenant, owner, agent, draft_id)
    assert status["has_unsubmitted_changes"] is True
    assert status["effective_artifact_id"] == str(artifact.id)
    assert status["effective_artifact_version"] == "2.3.4"

    repository = SkillDraftRepository(session)
    stored = await repository.mark_submitted(
        tenant,
        owner,
        agent,
        draft_id,
        revision=2,
        artifact_id=artifact.id,
    )
    assert stored is not None
    await session.flush()
    settled = await service.status(tenant, owner, agent, draft_id)
    assert settled["has_unsubmitted_changes"] is False


async def test_read_empty_path_lists_the_root_and_missing_file_is_explicit(
    session: AsyncSession, draft_roots: Path
) -> None:
    service, tenant, owner, agent, draft_id = await _create_draft(session)

    empty = await service.read(tenant, owner, agent, draft_id, "")
    assert empty == {"type": "directory", "path": "", "entries": []}

    await service.write(
        tenant,
        owner,
        agent,
        draft_id,
        path="SKILL.md",
        content="# draft\n",
        expected_revision=1,
    )
    await service.write(
        tenant,
        owner,
        agent,
        draft_id,
        path="scripts/run.py",
        content="print('x')\n",
        expected_revision=2,
    )
    root = await service.read(tenant, owner, agent, draft_id, "")
    assert root["path"] == ""
    assert [entry["path"] for entry in root["entries"]] == ["SKILL.md", "scripts"]
    scripts = await service.read(tenant, owner, agent, draft_id, "scripts")
    assert scripts["path"] == "scripts"
    assert [entry["path"] for entry in scripts["entries"]] == ["scripts/run.py"]

    with pytest.raises(AppError) as missing:
        await service.read(tenant, owner, agent, draft_id, "scripts/missing.py")
    assert missing.value.code == "DRAFT_FILE_NOT_FOUND"
    assert missing.value.data["draft_id"] == str(draft_id)
    assert missing.value.data["path"] == "scripts/missing.py"
    assert missing.value.data["repair"]


async def test_get_and_get_for_update_are_agent_scoped(session: AsyncSession, draft_roots: Path) -> None:
    service, tenant, owner, agent, draft_id = await _create_draft(session)
    other_agent = uuid.uuid4()

    repository = SkillDraftRepository(session)
    assert await repository.get(tenant, owner, other_agent, draft_id) is None
    assert await repository.get_for_update(tenant, owner, other_agent, draft_id) is None

    with pytest.raises(AppError) as hidden:
        await service.metadata(tenant, owner, other_agent, draft_id)
    assert hidden.value.code == "DRAFT_NOT_FOUND"
    assert hidden.value.data["draft_id"] == str(draft_id)
    assert hidden.value.data["repair"]

    with pytest.raises(AppError) as missing:
        await service.metadata(tenant, owner, agent, uuid.uuid4())
    assert missing.value.code == "DRAFT_NOT_FOUND"
    assert missing.value.data["repair"]


async def test_missing_directory_recovers_from_the_effective_artifact(
    session: AsyncSession, draft_roots: Path
) -> None:
    service, tenant, owner, agent, draft_id = await _create_draft(session)
    await service.write(
        tenant,
        owner,
        agent,
        draft_id,
        path="SKILL.md",
        content="# draft only\n",
        expected_revision=1,
    )
    await _seed_effective_artifact(
        session,
        tenant_id=tenant,
        owner_user_id=owner,
        key="diag-skill",
        files={"SKILL.md": "# from artifact\n", "scripts/run.py": "print('artifact')\n"},
        version="1.2.0",
    )

    store = service._store
    draft = await SkillDraftRepository(session).get(tenant, owner, agent, draft_id)
    assert draft is not None
    directory = store.draft_dir(draft)
    marker = directory.parent.parent / DRAFT_ACTIVITY_MARKER
    assert directory.is_dir() and marker.exists()
    shutil.rmtree(directory)
    marker.unlink()
    assert not directory.exists()

    metadata = await service.metadata(tenant, owner, agent, draft_id)
    assert metadata["effective_artifact_version"] == "1.2.0"
    assert directory.is_dir()
    assert marker.exists()
    assert (directory / "SKILL.md").read_text() == "# from artifact\n"
    listing = await service.read(tenant, owner, agent, draft_id, "")
    assert [entry["path"] for entry in listing["entries"]] == ["SKILL.md", "scripts"]
    restored = await service.read(tenant, owner, agent, draft_id, "scripts/run.py")
    assert restored["content"] == "print('artifact')\n"


async def test_missing_directory_without_artifact_is_actionable(
    session: AsyncSession, draft_roots: Path
) -> None:
    service, tenant, owner, agent, draft_id = await _create_draft(session)
    await service.write(
        tenant,
        owner,
        agent,
        draft_id,
        path="SKILL.md",
        content="# draft\n",
        expected_revision=1,
    )
    draft = await SkillDraftRepository(session).get(tenant, owner, agent, draft_id)
    assert draft is not None
    directory = service._store.draft_dir(draft)
    shutil.rmtree(directory)

    with pytest.raises(AppError) as missing:
        await service.read(tenant, owner, agent, draft_id, "")
    assert missing.value.code == "DRAFT_STORAGE_MISSING"
    assert missing.value.data["draft_id"] == str(draft_id)
    assert missing.value.data["path"] == ""
    assert missing.value.data["reason"] == "NO_RECOVERY_ARTIFACT"
    assert missing.value.data["key"] == "diag-skill"
    assert "delete the draft" in missing.value.data["repair"]


async def test_empty_draft_directory_is_recreated_on_get(
    session: AsyncSession, draft_roots: Path
) -> None:
    service, tenant, owner, agent, draft_id = await _create_draft(session)
    draft = await SkillDraftRepository(session).get(tenant, owner, agent, draft_id)
    assert draft is not None
    directory = service._store.draft_dir(draft)
    directory.rmdir()

    metadata = await service.metadata(tenant, owner, agent, draft_id)
    assert metadata["has_unsubmitted_changes"] is True
    assert directory.is_dir()
    listing = await service.read(tenant, owner, agent, draft_id, "")
    assert listing["entries"] == []


async def test_get_and_read_refresh_the_draft_activity_marker(
    session: AsyncSession, draft_roots: Path
) -> None:
    service, tenant, owner, agent, draft_id = await _create_draft(session)
    draft = await SkillDraftRepository(session).get(tenant, owner, agent, draft_id)
    assert draft is not None
    marker = service._store.draft_dir(draft).parent.parent / DRAFT_ACTIVITY_MARKER

    marker.unlink()
    assert not marker.exists()
    await service.metadata(tenant, owner, agent, draft_id)
    assert marker.exists()

    marker.unlink()
    await service.read(tenant, owner, agent, draft_id, "")
    assert marker.exists()
