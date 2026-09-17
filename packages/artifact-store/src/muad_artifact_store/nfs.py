from __future__ import annotations

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
