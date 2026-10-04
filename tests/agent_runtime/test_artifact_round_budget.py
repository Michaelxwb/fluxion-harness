"""[B-02] 工具结果的整轮批次预算与共享产物落盘原语（FEAT-02）。

真实边界：整轮选取是**纯函数**（`select_round_persists`）；落盘侧以真实临时产物根 + 真实
文件系统取证（不可变写、整批回滚、头尾预览）。
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest
from muad_agent_runtime.application.attachments.tool_results import (
    ArtifactResultWriter,
    RoundCandidate,
    preview_head_tail,
    select_round_persists,
)

TENANT = "test-round-budget"
RUN_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
CONVERSATION_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")


def _select(sizes: dict[str, int], *, threshold: int = 8192, budget: int = 200_000) -> tuple[str, ...]:
    return select_round_persists(
        [RoundCandidate(tool_call_id=key, size_bytes=value) for key, value in sizes.items()],
        persist_threshold_bytes=threshold,
        round_budget_bytes=budget,
    )


def test_b02_single_item_over_threshold_is_always_persisted() -> None:
    assert _select({"a": 8193}) == ("a",)


def test_b02_threshold_boundary_is_strict() -> None:
    """严格大于：恰好等于阈值**不**落盘（RULE-02 的边界口径）。"""
    assert _select({"a": 8192}) == ()


def test_b02_within_round_budget_persists_nothing() -> None:
    assert _select({"a": 1000, "b": 2000, "c": 3000}, budget=6000) == ()


def test_b02_round_budget_persists_largest_first_and_stops_in_budget() -> None:
    sizes = {"a": 5000, "b": 4000, "c": 3000, "d": 1000}
    assert sum(sizes.values()) == 13_000
    # 预算 6000：合计 13000 > 6000 ⇒ 先落最大的一批，一进预算就停。
    assert _select(sizes, budget=6000) == ("a", "b")  # 13000-5000-4000 = 4000 ≤ 6000
    assert _select(sizes, budget=9000) == ("a",)
    assert _select(sizes, budget=12_999) == ("a",)


def test_b02_over_threshold_items_come_first_and_are_never_second_guessed() -> None:
    sizes = {"big-1": 9000, "big-2": 20_000, "small": 3000}
    # 两条超阈值必落盘；剩余 3000 ≤ 200000 ⇒ 不再补落。
    assert _select(sizes) == ("big-1", "big-2")


def test_b02_selection_is_deterministic_on_equal_sizes() -> None:
    assert _select({"b": 5000, "a": 5000, "c": 5000}, budget=2000) == ("a", "b", "c")
    assert _select({"c": 5000, "b": 5000, "a": 5000}, budget=2000) == ("a", "b", "c")


def test_b02_oversized_round_never_drops_below_budget_when_it_cannot() -> None:
    """即使把能落的都落完仍超预算，也只能到此为止——**不得**越界去动未选中的结果。"""
    assert _select({"a": 3000, "b": 3000}, budget=0) == ("a", "b")


# ---- 头尾预览 ----


def test_b02_preview_keeps_head_and_tail() -> None:
    text = "头" * 100 + "中" * 100 + "尾" * 100
    preview = preview_head_tail(text, head_bytes=300, tail_bytes=300)
    assert preview.startswith("头" * 100)
    assert preview.endswith("尾" * 100), "尾部（错误栈末行/汇总行/JSON 闭合结构）必须留下"
    assert "已省略" in preview


def test_b02_preview_is_byte_based_and_never_splits_a_character() -> None:
    text = "中" * 100  # 300 字节
    preview = preview_head_tail(text, head_bytes=10, tail_bytes=10)
    assert "已省略" in preview
    head, _, tail = preview.partition("\n…（中间")
    assert len(head.encode("utf-8")) <= 10
    assert "�" not in preview, "截断处不得产生替换字符（非法 UTF-8 残字节）"
    assert tail, "尾部预览不得为空"


def test_b02_preview_returns_text_verbatim_when_it_fits() -> None:
    assert preview_head_tail("短文本", head_bytes=2000, tail_bytes=2000) == "短文本"


# ---- 不可变写 + 整批回滚 ----


class _StubSession:
    """只实现落盘路径真正用到的两个方法；`fail_after` 控制第几次 commit 起失败。"""

    def __init__(self, *, fail_after: int | None = None) -> None:
        self.commits = 0
        self._fail_after = fail_after

    def add(self, row: Any) -> None:
        return None

    async def commit(self) -> None:
        self.commits += 1
        if self._fail_after is not None and self.commits >= self._fail_after:
            raise RuntimeError("db down")


async def _persist(writer: ArtifactResultWriter, session: Any, text: str = "x" * 100) -> dict[str, Any]:
    return await writer.persist_tool_result_with_session(
        session,
        tenant_id=TENANT,
        conversation_id=CONVERSATION_ID,
        run_id=RUN_ID,
        task_id=None,
        tool_call_id="call-1",
        tool_name="t",
        result_text=text,
        user_id=uuid.uuid4(),
    )


async def test_rule_artifact_write_is_immutable(tmp_path: Path) -> None:
    """同 `storage_key` 二次写入必须抛 `FileExistsError`（RULE-skill-001），不得静默覆盖。"""
    writer = ArtifactResultWriter(tmp_path, session_factory=None)
    target = tmp_path / "tools/t/x/y/result.bin"
    writer._write_immutable(target, b"first")

    with pytest.raises(FileExistsError):
        writer._write_immutable(target, b"second")
    assert target.read_bytes() == b"first", "既有产物必须一字不动"


async def test_db_failure_removes_the_written_file(tmp_path: Path) -> None:
    writer = ArtifactResultWriter(tmp_path, session_factory=None)
    with pytest.raises(RuntimeError):
        await _persist(writer, _StubSession(fail_after=1))
    assert list(tmp_path.rglob("*.bin")) == [], "DB 失败必须清掉已写文件，不留孤儿"


async def test_round_batch_rolls_back_every_file_it_wrote(tmp_path: Path) -> None:
    """整批第 2 条失败 ⇒ 第 1 条已写的文件也要回滚，不留半批。"""
    writer = ArtifactResultWriter(tmp_path, session_factory=None)
    session = _StubSession(fail_after=2)
    with pytest.raises(RuntimeError):
        await writer.persist_round_results_with_session(
            session,
            tenant_id=TENANT,
            conversation_id=CONVERSATION_ID,
            run_id=RUN_ID,
            task_id=None,
            results=[("call-1", "t1", "a" * 100), ("call-2", "t2", "b" * 100)],
        )
    assert session.commits == 2, "确实写到了第 2 条才失败（否则本用例空转）"
    assert list(tmp_path.rglob("*.bin")) == [], "半批产物必须被清掉"


async def test_round_batch_persists_every_selected_result(tmp_path: Path) -> None:
    writer = ArtifactResultWriter(tmp_path, session_factory=None)
    session = _StubSession()
    references = await writer.persist_round_results_with_session(
        session,
        tenant_id=TENANT,
        conversation_id=CONVERSATION_ID,
        run_id=RUN_ID,
        task_id=None,
        results=[("call-1", "t1", "a" * 100), ("call-2", "t2", "b" * 100)],
    )
    assert set(references) == {"call-1", "call-2"}
    assert len(list(tmp_path.rglob("*.bin"))) == 2
    for key in ("call-1", "call-2"):
        assert references[key]["storage_key"].startswith(f"tools/{TENANT}/"), "产物前缀不得复用 skills/"
