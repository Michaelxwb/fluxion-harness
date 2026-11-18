"""MEDIA: 兼容层：只解析最终助手文本的独立行，登记为当前 Run 的 OUTPUT 产物。"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from muad_agent_runtime.application.attachments.final_media import (
    MAX_MEDIA_ITEMS,
    FinalMediaRegistrar,
    parse_final_media,
)
from muad_agent_runtime.application.attachments.output_service import (
    OUTPUT_ARTIFACT_TYPE,
    OutputArtifactWriter,
    OutputScope,
    SkillOutputArtifactRegistrar,
)
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import Artifact, Conversation, RunRecord

from .conftest import TenantContext


@pytest.fixture
def artifact_root(tmp_path):
    root = tmp_path / "artifacts"
    root.mkdir()
    return root


async def _seed_run(tenant: TenantContext) -> tuple[uuid.UUID, uuid.UUID]:
    async with get_session_factory()() as session:
        conversation = Conversation(
            tenant_id=tenant.tenant_id,
            user_id=tenant.platform_user_id,
            agent_id=tenant.agent_id,
            title="media",
        )
        session.add(conversation)
        await session.flush()
        run = RunRecord(
            tenant_id=tenant.tenant_id,
            conversation_id=conversation.id,
            user_id=tenant.platform_user_id,
            agent_id=tenant.agent_id,
            status="RUNNING",
            input_text="x",
            trace_id=uuid.uuid4().hex,
            cancel_requested=False,
        )
        session.add(run)
        await session.commit()
        return run.id, conversation.id


def _registrar(
    tenant: TenantContext,
    artifact_root,
    run_id: uuid.UUID,
    conversation_id: uuid.UUID,
    workspace,
) -> FinalMediaRegistrar:
    writer = OutputArtifactWriter(
        artifact_root=artifact_root,
        session_factory=get_session_factory,
        scope=OutputScope(
            tenant_id=tenant.tenant_id,
            run_id=run_id,
            conversation_id=conversation_id,
            agent_id=tenant.agent_id,
            user_id=tenant.platform_user_id,
        ),
    )
    return FinalMediaRegistrar(
        workspace_dir=workspace,
        registrar=SkillOutputArtifactRegistrar(writer),
    )


async def _artifacts(tenant: TenantContext, run_id: uuid.UUID) -> list[Artifact]:
    async with get_session_factory()() as session:
        rows = await session.scalars(
            sa.select(Artifact).where(
                Artifact.tenant_id == tenant.tenant_id,
                Artifact.run_id == run_id,
                Artifact.is_deleted.is_(False),
            )
        )
        return list(rows)


def test_parses_only_standalone_media_lines() -> None:
    text = "\n".join(
        [
            "已生成报告。",
            "MEDIA: outputs/run-1/report.md",
            "工具输出 MEDIA: not-a-directive",
            "```",
            "MEDIA: outputs/run-1/inside-code.md",
            "```",
            'MEDIA: "/workspace/data/附件 1.pdf"',
        ]
    )

    parsed = parse_final_media(text)

    assert parsed.paths == ("outputs/run-1/report.md", "data/附件 1.pdf")
    assert parsed.errors == ()
    assert "MEDIA: outputs/run-1/report.md" not in parsed.text
    assert "工具输出 MEDIA: not-a-directive" in parsed.text
    assert "inside-code.md" in parsed.text


def test_media_inside_indented_code_block_is_not_a_directive() -> None:
    parsed = parse_final_media("示例：\n    MEDIA: data/inside.md\n")
    assert parsed.paths == ()
    assert "data/inside.md" in parsed.text


def test_too_many_references_produce_a_visible_note() -> None:
    lines = [f"MEDIA: data/{index}.md" for index in range(MAX_MEDIA_ITEMS + 1)]
    parsed = parse_final_media("\n".join(lines))
    assert len(parsed.paths) == MAX_MEDIA_ITEMS
    assert parsed.errors
    assert str(MAX_MEDIA_ITEMS) in parsed.errors[0]


@pytest.mark.asyncio
async def test_registers_workspace_file_and_dedupes_same_attachment(
    tenant: TenantContext, artifact_root, tmp_path
) -> None:
    run_id, conversation_id = await _seed_run(tenant)
    workspace = tmp_path / "workspace"
    (workspace / "outputs" / "run-1").mkdir(parents=True)
    target = workspace / "outputs" / "run-1" / "report.md"
    target.write_text("# 报告\n", encoding="utf-8")
    registrar = _registrar(tenant, artifact_root, run_id, conversation_id, workspace)

    first = await registrar.apply("结论\n\nMEDIA: outputs/run-1/report.md")
    second = await registrar.apply("结论\n\nMEDIA: /workspace/outputs/run-1/report.md")

    assert first == "结论"
    assert second == "结论"
    rows = await _artifacts(tenant, run_id)
    assert len(rows) == 1
    assert rows[0].artifact_type == OUTPUT_ARTIFACT_TYPE
    assert rows[0].metadata_json["relative_path"] == "outputs/run-1/report.md"


@pytest.mark.asyncio
async def test_invalid_reference_is_visible_and_does_not_block_the_answer(
    tenant: TenantContext, artifact_root, tmp_path
) -> None:
    run_id, conversation_id = await _seed_run(tenant)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("secret", encoding="utf-8")
    (workspace / "escape.md").symlink_to(outside)
    registrar = _registrar(tenant, artifact_root, run_id, conversation_id, workspace)

    result = await registrar.apply(
        "结论\n\nMEDIA: ../outside.md\nMEDIA: escape.md\nMEDIA: missing.md"
    )

    assert "结论" in result
    assert result.count("未能附加文件") == 3
    assert "MEDIA:" not in result
    assert await _artifacts(tenant, run_id) == []


@pytest.mark.asyncio
async def test_only_valid_media_line_gets_a_plain_fallback_text(
    tenant: TenantContext, artifact_root, tmp_path
) -> None:
    run_id, conversation_id = await _seed_run(tenant)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "report.md").write_text("ok", encoding="utf-8")
    registrar = _registrar(tenant, artifact_root, run_id, conversation_id, workspace)

    result = await registrar.apply("MEDIA: report.md")

    assert result == "已生成附件。"
    assert "MEDIA:" not in result
