from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
import tempfile
import zipfile
from pathlib import Path

from .nfs import NfsArtifactStore


class SkillArtifactCacheError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class SkillArtifactCache:
    def __init__(self, store: NfsArtifactStore, cache_root: str | Path) -> None:
        self.store = store
        self.cache_root = Path(cache_root)
        self.cache_root.mkdir(parents=True, exist_ok=True)
        self._memory_index: dict[str, Path] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._guard = asyncio.Lock()

    async def ensure(self, *, artifact_id: str, storage_key: str, checksum: str) -> Path:
        normalized = checksum.removeprefix("sha256:")

        indexed = self._memory_index.get(normalized)
        if indexed is not None and (indexed / "READY").exists():
            return indexed

        final_dir = self.cache_root / normalized
        if (final_dir / "READY").exists():
            self._memory_index[normalized] = final_dir
            return final_dir

        lock = await self._lock_for(normalized)
        async with lock:
            if (final_dir / "READY").exists():
                self._memory_index[normalized] = final_dir
                return final_dir
            return await asyncio.to_thread(
                self._prepare,
                artifact_id,
                storage_key,
                normalized,
                final_dir,
            )

    async def _lock_for(self, checksum: str) -> asyncio.Lock:
        async with self._guard:
            return self._locks.setdefault(checksum, asyncio.Lock())

    def _prepare(
        self,
        artifact_id: str,
        storage_key: str,
        checksum: str,
        final_dir: Path,
    ) -> Path:
        source = self.store.resolve(storage_key)
        if not source.is_file():
            raise SkillArtifactCacheError("SKILL_ARTIFACT_UNAVAILABLE")

        digest = hashlib.sha256()
        with source.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != checksum:
            raise SkillArtifactCacheError("SKILL_ARTIFACT_CHECKSUM_MISMATCH")

        temp_root = Path(tempfile.mkdtemp(prefix=f".tmp-{checksum[:12]}-", dir=self.cache_root))
        try:
            local_zip = temp_root / "skill.zip"
            shutil.copy2(source, local_zip)
            unpack_dir = temp_root / "unpacked"
            unpack_dir.mkdir()

            with zipfile.ZipFile(local_zip) as archive:
                self._safe_extract(archive, unpack_dir)

            (unpack_dir / "READY").write_text(
                f"artifact_id={artifact_id}\nchecksum={checksum}\n",
                encoding="utf-8",
            )

            if final_dir.exists():
                shutil.rmtree(final_dir)
            os.replace(unpack_dir, final_dir)
            self._memory_index[checksum] = final_dir
            return final_dir
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)

    @staticmethod
    def _safe_extract(archive: zipfile.ZipFile, destination: Path) -> None:
        root = destination.resolve()
        for member in archive.infolist():
            target = (destination / member.filename).resolve()
            if target != root and root not in target.parents:
                raise SkillArtifactCacheError("SKILL_ARTIFACT_UNAVAILABLE")
        archive.extractall(destination)
