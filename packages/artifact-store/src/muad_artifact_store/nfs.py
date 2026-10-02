from __future__ import annotations

import os
import uuid
from pathlib import Path


class NfsArtifactStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    def resolve(self, storage_key: str) -> Path:
        path = (self.root / storage_key).resolve()
        if path == self.root or self.root not in path.parents:
            raise ValueError("invalid storage_key")
        return path

    def exists(self, storage_key: str) -> bool:
        return self.resolve(storage_key).exists()

    def write(self, storage_key: str, data: bytes) -> Path:
        """**不可变**写入：同一 `storage_key` 二次写入抛 `FileExistsError`（`RULE-skill-001`）。

        临时文件 + `os.replace` 保证读方永远看不到半截内容，失败时清掉临时文件。
        不可变不是防呆而是**契约**：需要"改内容"的调用方只能写新 key（runtime 的长文档
        追加写正是靠"每次换一个新 key"做版本演进，见 `append_artifact`）。
        """
        target = self.resolve(storage_key)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise FileExistsError(f"artifact already exists and is immutable: {storage_key}")
        temp = target.parent / f".tmp-{uuid.uuid4().hex}"
        try:
            temp.write_bytes(data)
            os.replace(temp, target)
        except BaseException:
            temp.unlink(missing_ok=True)
            raise
        return target
