"""共享产物存储的不可变写原语（`harness-skill#RULE-skill-001`）。

工具结果（`tools/`）与压缩 transcript（`transcripts/`）走同一套：原子发布、同 `storage_key`
二次写入抛 `FileExistsError`；整批中途失败按 key 回滚，不留半批。

**发布本身委托给共享原语 `muad_artifact_store.publish_if_absent`**（2026-10-06）：这里原先自带一份
`path.exists()` + `os.replace` 的实现，而那个形状**检查和发布之间有窗口**（两个写者都能通过检查、
都能"成功"，后者静默覆盖前者）。原子判定交给内核（`os.link`），本模块只保留产物侧的语义与回滚。
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from muad_artifact_store import publish_if_absent


def write_immutable(path: Path, data: bytes) -> None:
    """不可变写。产物一旦落盘就是证据，覆盖写会让"当时到底看到了什么"永远查不回来。"""
    publish_if_absent(path, data)


def discard_written(root: Path, keys: Sequence[str]) -> None:
    """回滚已写文件（整批中途失败时用）。删不掉也不抛——回滚是尽力而为，不能掩盖原始异常。"""
    for key in keys:
        try:
            (root / key).unlink(missing_ok=True)
        except OSError:
            pass


__all__ = ["discard_written", "write_immutable"]
