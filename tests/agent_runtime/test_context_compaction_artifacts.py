"""[E-02 / RULE-04 / RULE-06] 共享产物落盘与 transcript 的真实边界（FEAT-02/04）。

真实边界：真实 PostgreSQL（`runtime.artifact`）+ 真实共享产物根（临时目录，文件系统真写）。
本文件只断言 TASK-004 交付的落盘面；"整轮判定接进回合循环"与"压缩失败不让 Run 失败"的
端到端那条腿随 TASK-006 的接线落地。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient  # noqa: F401  (保持与同目录用例一致的测试依赖面)
from muad_agent_core.model import ModelMessage, ModelRole, ModelToolCall
from muad_agent_runtime.application.attachments.tool_results import (
    ArtifactResultWriter,
    preview_head_tail,
)
from muad_agent_runtime.application.attachments.transcripts import (
    TRANSCRIPT_ARTIFACT_TYPE,
    TranscriptWriter,
)
from muad_agent_runtime.infrastructure.db import get_session_factory
from muad_agent_runtime.infrastructure.models.runtime import Artifact

from agent_runtime.conftest import TenantContext

RUN_ID = uuid.UUID("33333333-3333-3333-3333-333333333333")
CONVERSATION_ID = uuid.UUID("44444444-4444-4444-4444-444444444444")


def _messages(count: int = 4) -> list[ModelMessage]:
    messages: list[ModelMessage] = [ModelMessage(role=ModelRole.USER, content="最初的诉求：别忘了我")]
    for index in range(count):
        messages.append(
            ModelMessage(
                role=ModelRole.ASSISTANT,
                content=f"回合 {index}",
                tool_calls=(ModelToolCall(id=f"c{index}", name=f"tool_{index}", arguments={"q": "值"}),),
                reasoning_content=f"思考 {index}",
            )
        )
        messages.append(ModelMessage(role=ModelRole.TOOL, tool_call_id=f"c{index}", content=f"结果 {index}"))
    return messages


class _BrokenSession:
    def add(self, row: Any) -> None:
        return None

    async def commit(self) -> None:
        raise RuntimeError("db down")


async def test_e02_round_persist_keeps_head_and_tail_preview(
    tenant: TenantContext, database_guard: None, tmp_path: Path
) -> None:
    """整轮超预算时大结果落盘，且**预览头尾都在**（真实 PG 逐列回读）。"""
    writer = ArtifactResultWriter(tmp_path / "artifacts")
    body = "头" * 1000 + "中" * 5000 + "尾" * 1000
    async with get_session_factory()() as session:
        reference = await writer.persist_tool_result_with_session(
            session,
            tenant_id=tenant.tenant_id,
            conversation_id=CONVERSATION_ID,
            run_id=RUN_ID,
            task_id=None,
            tool_call_id="c1",
            tool_name="big_tool",
            result_text=body,
            user_id=tenant.platform_user_id,
        )
        row = await session.get(Artifact, uuid.UUID(reference["artifact_id"]))

    assert row is not None
    assert row.preview_text is not None
    assert row.preview_text.startswith("头"), "头部预览必须在"
    assert row.preview_text.rstrip().endswith("尾"), "尾部预览必须在（只留头部会让模型看不到结尾）"
    assert "已省略" in row.preview_text
    assert reference["storage_key"].startswith(f"tools/{tenant.tenant_id}/")
    on_disk = (tmp_path / "artifacts" / reference["storage_key"]).read_bytes()
    assert on_disk.decode("utf-8") == body, "落盘的是原文，不是预览"


async def test_e02_transcript_lands_in_shared_store_and_is_readable(
    tenant: TenantContext, database_guard: None, tmp_path: Path
) -> None:
    """transcript 落共享产物（类型 `TRANSCRIPT`），运维可按 id 直读原文。"""
    root = tmp_path / "artifacts"
    writer = TranscriptWriter(root)
    messages = _messages()
    async with get_session_factory()() as session:
        reference = await writer.persist_with_session(
            session,
            tenant_id=tenant.tenant_id,
            conversation_id=CONVERSATION_ID,
            run_id=RUN_ID,
            task_id=None,
            messages=messages,
        )
        assert reference is not None
        row = await session.get(Artifact, uuid.UUID(reference["artifact_id"]))

    assert row is not None
    assert row.artifact_type == TRANSCRIPT_ARTIFACT_TYPE
    # 钉**字面**前缀而不是 `TRANSCRIPT_PREFIX` 常量：后者会让断言跟着常量一起漂移，
    # 而这条断言要挡的正是"顺手复用 skills/ 被别人回收"。
    assert row.storage_key.startswith(f"transcripts/{tenant.tenant_id}/"), row.storage_key
    assert not row.storage_key.startswith("skills/"), "不得复用 skills/ 前缀（会被孤儿清理回收）"
    assert row.preview_text is None, "transcript 不落预览——它不该在任何读面被顺带回显"
    assert row.size == reference["size"]
    on_disk = (root / row.storage_key).read_bytes()
    assert "sha256:" + hashlib.sha256(on_disk).hexdigest() == row.checksum
    lines = [json.loads(line) for line in on_disk.decode("utf-8").splitlines()]
    assert len(lines) == len(messages)
    assert lines[0]["content"] == "最初的诉求：别忘了我", "逐字原文按序落盘"
    assert lines[1]["tool_calls"][0]["name"] == "tool_0"
    assert lines[1]["reasoning_content"] == "思考 0", "带 tool_calls 的回合必须连思维链一起存档"


async def test_rule04_transcript_failure_degrades_without_raising(tmp_path: Path) -> None:
    """RULE-04：transcript 落库失败 ⇒ 清掉已写文件、返回 None（**不抛**，调用方保持原历史）。"""
    root = tmp_path / "artifacts"
    writer = TranscriptWriter(root)

    result = await writer.persist_with_session(
        _BrokenSession(),  # type: ignore[arg-type]
        tenant_id="t",
        conversation_id=CONVERSATION_ID,
        run_id=RUN_ID,
        task_id=None,
        messages=_messages(2),
    )

    assert result is None
    assert list(root.rglob("*.jsonl")) == [], "失败时不得留下半截 transcript"


async def test_rule04_empty_history_produces_no_transcript(tmp_path: Path) -> None:
    writer = TranscriptWriter(tmp_path / "artifacts")
    assert await writer.persist_with_session(
        _BrokenSession(),  # type: ignore[arg-type]
        tenant_id="t",
        conversation_id=CONVERSATION_ID,
        run_id=RUN_ID,
        task_id=None,
        messages=[],
    ) is None


def test_rule06_console_exposes_no_transcript_endpoint() -> None:
    """RULE-06：transcript 不新增任何对外明文出口——Console 不得有读取/下载它的路由。"""
    from muad_console_platform.main import app as console_app

    paths = [getattr(route, "path", "") for route in console_app.routes]
    offenders = [path for path in paths if "transcript" in path.lower()]
    assert offenders == [], f"Console 出现了 transcript 的对外出口：{offenders}"


def test_rule06_preview_helper_is_shared_by_both_writers() -> None:
    """两个写入方共用同一套预览口径（避免只改一处导致行为分叉）。"""
    text = "a" * 5000
    preview = preview_head_tail(text, head_bytes=10, tail_bytes=10)
    assert preview.startswith("a" * 10)
    assert preview.rstrip().endswith("a" * 10)
    assert "已省略" in preview


@pytest.mark.parametrize("head,tail", [(0, 0), (1, 0), (0, 1)])
def test_rule06_preview_bounds_are_honoured(head: int, tail: int) -> None:
    preview = preview_head_tail("中" * 100, head_bytes=head, tail_bytes=tail)
    assert "已省略" in preview
    assert "�" not in preview
