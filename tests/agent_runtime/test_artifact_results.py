"""[B-111] 大结果 Artifact 落盘与引用（真实共享文件系统 + 真实 PostgreSQL）。"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import Artifact, Conversation

from muad_agent_runtime.application.artifacts import ArtifactResultWriter

TENANT = f"art-{uuid.uuid4()}"
CONV_ID = uuid.uuid4()
RUN_ID = uuid.uuid4()
USER_ID = uuid.uuid4()


@pytest.fixture()
async def _conversation():
    async with get_session_factory()() as session:
        session.add(
            Conversation(
                id=CONV_ID,
                tenant_id=TENANT,
                user_id=USER_ID,
                agent_id=uuid.uuid4(),
                status="ACTIVE",
                last_seq=0,
            )
        )
        await session.commit()
    yield
    async with get_session_factory()() as session:
        await session.execute(Artifact.__table__.delete().where(Artifact.tenant_id == TENANT))
        await session.execute(Conversation.__table__.delete().where(Conversation.id == CONV_ID))
        await session.commit()


@pytest.fixture()
def writer(tmp_path):
    return ArtifactResultWriter(artifact_root=tmp_path)


async def test_b111_large_result_persisted_not_in_prompt(
    writer, _conversation
) -> None:
    """[B-111] 大结果落盘为 Artifact，Prompt 仅收 preview 引用。"""
    result = "x" * 200_000
    ref = await writer.persist_tool_result(
        tenant_id=TENANT,
        conversation_id=CONV_ID,
        run_id=RUN_ID,
        task_id=None,
        tool_call_id="c1",
        tool_name="big_tool",
        result_text=result,
        user_id=USER_ID,
        preview_limit=200,
    )
    assert ref["artifact_id"]
    assert len(ref["preview"]) == 200
    assert "x" * 200_000 not in ref["preview"]
    assert ref["checksum"].startswith("sha256:")

    async with get_session_factory()() as session:
        row = await session.get(Artifact, uuid.UUID(ref["artifact_id"]))
        assert row is not None
        assert row.size == 200_000
        assert row.preview_text == ref["preview"]


async def test_b111_duplicate_run_task_xor_rejected(writer, _conversation) -> None:
    """[B-111] run_id/task_id XOR 违约被拒。"""
    with pytest.raises(ValueError):
        await writer.persist_tool_result(
            tenant_id=TENANT,
            conversation_id=CONV_ID,
            run_id=RUN_ID,
            task_id=uuid.uuid4(),
            tool_call_id="c2",
            tool_name="t",
            result_text="data",
            user_id=USER_ID,
        )


async def test_b111_db_failure_cleans_file(writer, tmp_path, _conversation) -> None:
    """[B-111] DB 失败时删除本次写入的文件（不留孤儿）。"""
    import sqlalchemy.exc

    class BrokenSession:
        async def execute(self, *a, **k):
            raise sqlalchemy.exc.OperationalError("stmt", {}, Exception("db down"))

        def add(self, *a, **k):
            pass

        async def flush(self):
            raise sqlalchemy.exc.OperationalError("stmt", {}, Exception("db down"))

        async def rollback(self):
            pass

        async def commit(self):
            raise sqlalchemy.exc.OperationalError("stmt", {}, Exception("db down"))

    broken_writer = ArtifactResultWriter(artifact_root=tmp_path, session_factory=lambda: None)
    with pytest.raises(Exception):
        await broken_writer.persist_tool_result_with_session(
            BrokenSession(),
            tenant_id=TENANT,
            conversation_id=CONV_ID,
            run_id=RUN_ID,
            task_id=None,
            tool_call_id="c3",
            tool_name="t",
            result_text="will fail",
            user_id=USER_ID,
        )

    files = list(tmp_path.rglob("*"))
    assert not [f for f in files if f.is_file() and f.suffix == ".bin"]
