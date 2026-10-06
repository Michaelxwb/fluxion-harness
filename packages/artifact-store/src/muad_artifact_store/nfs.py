from __future__ import annotations

from pathlib import Path

from .immutable import publish_if_absent


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

        发布走共享原语 `publish_if_absent`：临时文件 + `os.link`（目标已存在即失败），
        读方永远看不到半截内容，**同键只有一个赢家**。不可变不是防呆而是**契约**：需要
        "改内容"的调用方只能写新 key（runtime 的长文档追加写正是靠"每次换一个新 key"做
        版本演进，见 `append_artifact`）。
        """
        target = self.resolve(storage_key)
        try:
            publish_if_absent(target, data)
        except FileExistsError as exc:
            raise FileExistsError(f"artifact already exists and is immutable: {storage_key}") from exc
        return target
