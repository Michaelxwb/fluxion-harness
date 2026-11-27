from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from muad_agent_runtime.application.attachments.output_service import (
    OutputArtifactError,
    OutputArtifactWriter,
    OutputScope,
    SkillOutputArtifactRegistrar,
)
from muad_agent_runtime.application.attachments.tools import resolve_workspace_file
from muad_agent_runtime.infrastructure.db import get_session_factory
from sqlalchemy.exc import DBAPIError

from agent_runtime.conftest import TenantContext
from agent_runtime.test_attachment_tools import _seed_run


async def test_database_failure_removes_written_artifact(tenant: TenantContext, tmp_path: Path) -> None:
    run_id, conversation_id = await _seed_run(tenant)
    # A real VARCHAR constraint failure, after the file has been saved.
    writer = OutputArtifactWriter(
        artifact_root=tmp_path,
        session_factory=get_session_factory,
        scope=OutputScope("x" * 1000, run_id, conversation_id),
    )
    with pytest.raises(DBAPIError):
        await writer.save(b"payload", filename="a.zip", media_type="application/zip", kind="OTHER")
    assert not any(path.is_file() for path in tmp_path.rglob("*"))


async def test_missing_run_cannot_create_output(tmp_path: Path) -> None:
    writer = OutputArtifactWriter(
        artifact_root=tmp_path,
        session_factory=get_session_factory,
        scope=OutputScope("tenant", None, uuid.uuid4()),
    )
    with pytest.raises(OutputArtifactError) as error:
        await writer.save(b"x", filename="a.zip", media_type="application/zip", kind="OTHER")
    assert error.value.code == "ATTACHMENT_WRITE_UNAVAILABLE"
    assert not any(tmp_path.iterdir())


async def test_storage_failure_is_explicit(tmp_path: Path) -> None:
    blocked_root = tmp_path / "not-a-directory"
    blocked_root.write_text("occupied", encoding="utf-8")
    writer = OutputArtifactWriter(
        artifact_root=blocked_root,
        session_factory=get_session_factory,
        scope=OutputScope("tenant", uuid.uuid4(), uuid.uuid4()),
    )
    with pytest.raises(OutputArtifactError) as error:
        await writer.save(b"x", filename="a.zip", media_type="application/zip", kind="OTHER")
    assert error.value.code == "ARTIFACT_WRITE_FAILED"
    assert blocked_root.read_text(encoding="utf-8") == "occupied"


async def test_registrar_does_not_publish_bytes_changed_after_inspection(tmp_path: Path) -> None:
    path = tmp_path / "report.txt"
    path.write_bytes(b"original")
    output = resolve_workspace_file(tmp_path, path.name)
    path.write_bytes(b"modified")
    artifact_root = tmp_path / "artifacts"
    registrar = SkillOutputArtifactRegistrar(OutputArtifactWriter(
        artifact_root=artifact_root,
        session_factory=get_session_factory,
        scope=OutputScope("tenant", uuid.uuid4(), uuid.uuid4()),
    ))
    with pytest.raises(OutputArtifactError, match="已变化"):
        await registrar.register(output)
    assert not artifact_root.exists()
