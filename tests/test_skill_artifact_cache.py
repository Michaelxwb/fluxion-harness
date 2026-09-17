import asyncio
import hashlib
import zipfile

from muad_artifact_store import NfsArtifactStore, SkillArtifactCache


def test_skill_artifact_cache_hit_does_not_require_nfs_source(tmp_path):
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

    first = asyncio.run(
        cache.ensure(artifact_id='a1', storage_key=storage_key, checksum=checksum)
    )
    assert (first / 'READY').exists()
    assert (first / 'SKILL.md').exists()

    source.unlink()
    second = asyncio.run(
        cache.ensure(artifact_id='a1', storage_key=storage_key, checksum=checksum)
    )
    assert second == first
