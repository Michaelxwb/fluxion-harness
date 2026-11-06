"""[S-47] Skill 输出文件按边界登记为产物，并复用现有 deliver_artifact/附件机制。"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from muad_agent_core.skill import OutputFile, checksum_file
from muad_agent_runtime.application.attachments.output_service import (
    OutputArtifactWriter,
    OutputScope,
    SkillOutputArtifactRegistrar,
)
from muad_agent_runtime.application.attachments.tools import AttachmentToolSet
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import Artifact, Conversation, RunRecord
from muad_contracts import DeliveryResponse, DeliveryRouteInput, RunStatus
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from tests.acceptance.user_skill_sandbox.sandboxed_support import run_probe

pytestmark = pytest.mark.integration


def test_s47_outputs_are_bounded_relative_paths_and_symlinks_are_skipped() -> None:
    payload = run_probe("s47")

    assert payload["ok"] is True, payload
    result = payload["result"]
    assert result["status"] == "SUCCEEDED", result
    assert payload["registered_paths"] == ["empty.bin", "reports/a.txt"]
    by_path = {item["relative_path"]: item for item in result["outputs"]}
    assert by_path["reports/a.txt"]["size"] == 5
    assert by_path["reports/a.txt"]["checksum"].startswith("sha256:")
    assert by_path["empty.bin"]["size"] == 0
    # symlink（文件与目录）不登记
    assert "passwd-link" not in by_path
    assert "escape-dir" not in by_path
    # 超过文件数上限：只保留已扫描的安全产物，并留下可见标记
    limited = payload["limited"]
    assert limited["status"] == "SUCCEEDED"
    assert limited["output_notice"] == "SANDBOX_OUTPUT_LIMIT"
    assert payload["limited_registered_paths"] == ["one.txt"]


class _RecordingDeliveryClient:
    def __init__(self) -> None:
        self.requests: list[object] = []

    async def deliver(self, request: object) -> DeliveryResponse:
        self.requests.append(request)
        return DeliveryResponse(
            accepted=True,
            delivered=True,
            deduplicated=False,
            duplicate=False,
            outcome="DELIVERED",
        )


async def test_s47_registered_output_is_deliverable_through_existing_artifact_path(
    session: AsyncSession, tmp_path: Path
) -> None:
    tenant = f"acc-s47-{uuid.uuid4()}"
    user_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    conversation = Conversation(tenant_id=tenant, user_id=user_id, agent_id=agent_id)
    session.add(conversation)
    await session.flush()
    run = RunRecord(
        tenant_id=tenant,
        conversation_id=conversation.id,
        user_id=user_id,
        agent_id=agent_id,
        status=RunStatus.RUNNING,
        input_text="skill output",
        trace_id=f"acc-s47-{uuid.uuid4().hex[:12]}",
    )
    session.add(run)
    await session.commit()

    output_path = tmp_path / "reports" / "a.txt"
    output_path.parent.mkdir(parents=True)
    output_path.write_text("alpha", encoding="utf-8")
    output = OutputFile(
        relative_path="reports/a.txt",
        size=output_path.stat().st_size,
        checksum=checksum_file(output_path),
        path=output_path,
    )
    artifact_root = tmp_path / "artifacts"
    registrar = SkillOutputArtifactRegistrar(
        OutputArtifactWriter(
            artifact_root=artifact_root,
            session_factory=get_session_factory,
            scope=OutputScope(tenant_id=tenant, run_id=run.id, conversation_id=conversation.id),
        )
    )

    artifact_id = await registrar.register(output)

    row = await session.get(Artifact, uuid.UUID(artifact_id))
    if row is None:
        await session.rollback()
        row = await session.get(Artifact, uuid.UUID(artifact_id))
    assert row is not None
    assert row.artifact_type == "AGENT_OUTPUT"
    assert row.run_id == run.id
    assert row.size == output.size
    assert row.checksum == output.checksum
    assert row.metadata_json["relative_path"] == "reports/a.txt"
    assert row.metadata_json["filename"] == "a.txt"
    assert (artifact_root / row.storage_key).read_text(encoding="utf-8") == "alpha"

    client = _RecordingDeliveryClient()
    tools = AttachmentToolSet(
        session_factory=get_session_factory,
        artifact_root=str(artifact_root),
        tenant_id=tenant,
        run_id=run.id,
        conversation_id=conversation.id,
        user_id=user_id,
        has_delivery_route=True,
        delivery_route=DeliveryRouteInput(
            channel="WEBCHAT", bot_id="probe-bot", external_user_id="probe-user"
        ),
        delivery_client=client,  # type: ignore[arg-type]
    )
    delivered = await tools.deliver_artifact({"artifact_id": artifact_id}, call_id="s47-deliver")
    assert "已交付" in delivered or artifact_id in delivered
    assert len(client.requests) == 1
    assert client.requests[0].delivery_key == f"run:{run.id}:{artifact_id}"

    await session.execute(delete(Artifact).where(Artifact.run_id == run.id))
    await session.execute(delete(RunRecord).where(RunRecord.id == run.id))
    await session.execute(delete(Conversation).where(Conversation.id == conversation.id))
    await session.commit()
