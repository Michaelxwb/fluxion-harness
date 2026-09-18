from __future__ import annotations

import asyncio
import hashlib
import zipfile
from pathlib import Path

import pytest
from muad_artifact_store import NfsArtifactStore, SkillArtifactCache
from muad_artifact_store.skill_cache import SkillArtifactCacheError

STORAGE_KEY = "skills/s1/a1/skill.zip"


class CountingStore(NfsArtifactStore):
    def __init__(self, root: str | Path) -> None:
        super().__init__(root)
        self.resolve_calls = 0

    def resolve(self, storage_key: str) -> Path:
        self.resolve_calls += 1
        return super().resolve(storage_key)


def _write_artifact(root: Path) -> str:
    source = root / STORAGE_KEY
    source.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("SKILL.md", "---\nname: demo\n---\n")
        archive.writestr("scripts/main.py", "print('ok')\n")
    return hashlib.sha256(source.read_bytes()).hexdigest()


async def test_s04_second_ensure_hits_ready_without_nfs(tmp_path: Path) -> None:
    artifact_root = tmp_path / "nfs"
    checksum = _write_artifact(artifact_root)
    store = CountingStore(artifact_root)
    cache = SkillArtifactCache(store, tmp_path / "cache")

    first = await cache.ensure(artifact_id="a1", storage_key=STORAGE_KEY, checksum=checksum)
    second = await cache.ensure(artifact_id="a1", storage_key=STORAGE_KEY, checksum=checksum)

    assert first == second
    assert (first / "READY").exists()
    assert store.resolve_calls == 1, "READY hit must not touch NFS"


async def test_s05_concurrent_ensure_copies_once(tmp_path: Path) -> None:
    artifact_root = tmp_path / "nfs"
    checksum = _write_artifact(artifact_root)
    store = CountingStore(artifact_root)
    cache = SkillArtifactCache(store, tmp_path / "cache")

    results = list(
        await asyncio.gather(
            *(cache.ensure(artifact_id="a1", storage_key=STORAGE_KEY, checksum=checksum) for _ in range(10))
        )
    )

    assert len(set(results)) == 1
    assert (results[0] / "READY").exists()
    assert store.resolve_calls == 1, "singleflight must copy/unpack exactly once"


async def test_e01_missing_artifact_is_unavailable_without_partial_ready(tmp_path: Path) -> None:
    cache_root = tmp_path / "cache"
    store = CountingStore(tmp_path / "nfs")
    cache = SkillArtifactCache(store, cache_root)
    checksum = hashlib.sha256(b"absent").hexdigest()

    with pytest.raises(SkillArtifactCacheError) as excinfo:
        await cache.ensure(artifact_id="a1", storage_key=STORAGE_KEY, checksum=checksum)

    assert excinfo.value.code == "SKILL_ARTIFACT_UNAVAILABLE"
    assert not (cache_root / checksum / "READY").exists()
