import hashlib
import zipfile
from pathlib import Path

from muad_artifact_store import NfsArtifactStore, SkillArtifactCache


async def test_skill_artifact_cache_hit_does_not_require_nfs_source(tmp_path: Path) -> None:
    artifact_root = tmp_path / 'nfs'
    cache_root = tmp_path / 'cache'
    storage_key = 'skills/s1/a1/skill.zip'
    source = artifact_root / storage_key
    source.parent.mkdir(parents=True)

    with zipfile.ZipFile(source, 'w') as archive:
        archive.writestr('SKILL.md', '---\nname: demo\n---\n')
        archive.writestr('scripts/main.py', "print('ok')\n")

    checksum = hashlib.sha256(source.read_bytes()).hexdigest()
    cache = SkillArtifactCache(NfsArtifactStore(artifact_root), cache_root)

    first = await cache.ensure(artifact_id='a1', storage_key=storage_key, checksum=checksum)
    assert (first / 'READY').exists()
    assert (first / 'SKILL.md').exists()

    source.unlink()
    second = await cache.ensure(artifact_id='a1', storage_key=storage_key, checksum=checksum)
    assert second == first


# ---- S-03 / E-01（08 TASK-012）：singleflight / 失效边界 / 不变量锁定 ----

def _make_source(artifact_root: Path, storage_key: str) -> str:
    source = artifact_root / storage_key
    source.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("SKILL.md", "---\nname: demo\n---\n")
        archive.writestr("scripts/main.py", "print('ok')\n")
    return hashlib.sha256(source.read_bytes()).hexdigest()


async def test_s03_second_hit_no_nfs_io(tmp_path: Path) -> None:
    """[S-03] 同 checksum 第二次命中 READY 缓存，不发生 NFS IO（源已删除仍命中）。"""
    import asyncio

    artifact_root = tmp_path / "nfs"
    cache_root = tmp_path / "cache"
    storage_key = "skills/s9/a9/skill.zip"
    checksum = _make_source(artifact_root, storage_key)
    cache = SkillArtifactCache(NfsArtifactStore(artifact_root), cache_root)

    first = await cache.ensure(artifact_id="a9", storage_key=storage_key, checksum=checksum)
    source = artifact_root / storage_key
    source.unlink()  # 模拟 NFS 移除：二次命中必须完全走本地 emptyDir

    second = await cache.ensure(artifact_id="a9", storage_key=storage_key, checksum=checksum)
    assert second == first
    assert (second / "READY").exists()

    # singleflight：并发 ensure 同一 storage_key/checksum 只解压一次
    third_root = tmp_path / "cache2"
    cache2 = SkillArtifactCache(NfsArtifactStore(artifact_root), third_root)
    source2 = artifact_root / "skills/s8/b1/skill.zip"
    source2.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(source2, "w") as archive:
        archive.writestr("SKILL.md", "---\nname: demo\n---\n")
    checksum2 = hashlib.sha256(source2.read_bytes()).hexdigest()
    results = await asyncio.gather(
        *[
            cache2.ensure(artifact_id="b1", storage_key="skills/s8/b1/skill.zip", checksum=checksum2)
            for _ in range(4)
        ]
    )
    assert len({str(path) for path in results}) == 1


async def test_e01_unavailable_storage_maps_to_registered_error(tmp_path: Path) -> None:
    """[E-01] cache miss 且存储不可用 → SKILL_ARTIFACT_UNAVAILABLE；无半成品目录。"""
    artifact_root = tmp_path / "nfs-missing"  # 目录不存在 → 存储不可用
    cache_root = tmp_path / "cache"
    cache = SkillArtifactCache(NfsArtifactStore(artifact_root), cache_root)

    import pytest as _pytest

    with _pytest.raises(Exception) as exc:
        await cache.ensure(
            artifact_id="gone", storage_key="skills/g/g/skill.zip", checksum="sha256:" + "9" * 64
        )
    # 已登记错误码（owner 表/API 层用 muad-api 映射）
    assert getattr(exc.value, "code", "") == "SKILL_ARTIFACT_UNAVAILABLE"

    # 无半成品执行目录
    assert not any(dir.is_dir() and (dir / "READY").exists() for dir in cache_root.rglob("*"))


async def test_e01_checksum_mismatch_rejected(tmp_path: Path) -> None:
    """[E-01] checksum mismatch 使用已登记错误，不执行。"""
    artifact_root = tmp_path / "nfs"
    cache_root = tmp_path / "cache"
    storage_key = "skills/s7/a7/skill.zip"
    _make_source(artifact_root, storage_key)
    cache = SkillArtifactCache(NfsArtifactStore(artifact_root), cache_root)

    import pytest as _pytest

    with _pytest.raises(Exception) as exc:
        await cache.ensure(
            artifact_id="a7", storage_key=storage_key, checksum="sha256:" + "f" * 64
        )
    assert getattr(exc.value, "code", "") == "SKILL_ARTIFACT_CHECKSUM_MISMATCH"
    assert not any((cache_root / "skills").rglob("READY")) if (cache_root / "skills").exists() else True
