"""共享"不可变发布"原语：`publish_if_absent`（2026-10-06）。

这条原语存在的理由就是**并发**：这个形状在本仓曾有三份实现，其中两份写成 `path.exists()` 再
`os.replace` —— 检查与发布之间的窗口让两个写者都能"成功"，后者静默覆盖前者。所以这里钉的不是
"文件写出来了"，而是"同键只有一个赢家、读方永远看不到半截"。
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from muad_artifact_store import NfsArtifactStore, publish_if_absent


def test_publishes_complete_content_and_leaves_no_temporary_file(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "dir" / "artifact.bin"

    publish_if_absent(target, b"payload")

    assert target.read_bytes() == b"payload"
    assert [path.name for path in target.parent.iterdir()] == ["artifact.bin"]


def test_refuses_to_publish_over_existing_bytes(tmp_path: Path) -> None:
    target = tmp_path / "artifact.bin"
    publish_if_absent(target, b"first")

    with pytest.raises(FileExistsError):
        publish_if_absent(target, b"second")

    assert target.read_bytes() == b"first", "既有字节必须一字不动"


def test_concurrent_publishers_have_exactly_one_winner(tmp_path: Path) -> None:
    """两个写者同时发布同一个键：一个成功、另一个 `FileExistsError`，磁盘内容属于赢家。

    用线程屏障把两者**同时**推到发布点：若实现是"先判存在再替换"，两边都会成功、后者覆盖前者，
    这条用例就会红。
    """
    target = tmp_path / "artifact.bin"
    barrier = threading.Barrier(2)
    outcomes: list[object] = []

    def attempt(data: bytes) -> None:
        barrier.wait(timeout=5)
        try:
            publish_if_absent(target, data)
            outcomes.append(data)
        except FileExistsError as exc:
            outcomes.append(exc)

    with ThreadPoolExecutor(2) as pool:
        list(pool.map(attempt, [b"first", b"second"]))

    winners = [item for item in outcomes if not isinstance(item, FileExistsError)]
    assert len(winners) == 1, outcomes
    assert target.read_bytes() == winners[0]


def test_store_write_keeps_naming_the_storage_key_in_its_error(tmp_path: Path) -> None:
    """`NfsArtifactStore.write` 委托给原语，但**保留自己的诊断信息**（报的是 storage_key）。"""
    store = NfsArtifactStore(tmp_path)
    store.write("skills/s1/a1/skill.zip", b"zip")

    with pytest.raises(FileExistsError, match="skills/s1/a1/skill.zip"):
        store.write("skills/s1/a1/skill.zip", b"other")
