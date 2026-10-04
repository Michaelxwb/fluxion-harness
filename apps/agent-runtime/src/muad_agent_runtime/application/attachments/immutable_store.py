"""共享产物存储的不可变写原语（`harness-skill#RULE-skill-001`）。

工具结果（`tools/`）与压缩 transcript（`transcripts/`）走同一套：temp + `os.replace` 原子写，
同 `storage_key` 二次写入抛 `FileExistsError`；整批中途失败按 key 回滚，不留半批。
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Sequence
from pathlib import Path


def write_immutable(path: Path, data: bytes) -> None:
    """不可变写。产物一旦落盘就是证据，覆盖写会让"当时到底看到了什么"永远查不回来。"""
    if path.exists():
        raise FileExistsError(f"artifact already exists and is immutable: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.parent / f".tmp-{uuid.uuid4().hex}"
    temp.write_bytes(data)
    os.replace(temp, path)


def discard_written(root: Path, keys: Sequence[str]) -> None:
    """回滚已写文件（整批中途失败时用）。删不掉也不抛——回滚是尽力而为，不能掩盖原始异常。"""
    for key in keys:
        try:
            (root / key).unlink(missing_ok=True)
        except OSError:
            pass


__all__ = ["discard_written", "write_immutable"]
