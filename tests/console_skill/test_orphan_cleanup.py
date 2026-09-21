"""[RULE-skill-001] Artifact 不可变与孤儿清理（真实 NFS/POSIX 目录 + 真实 PostgreSQL）。"""

from __future__ import annotations

import os
import time
import uuid
from pathlib import Path

import pytest
from muad_console_platform.application.skill_service import SkillService
from muad_console_platform.infrastructure.db import get_session_factory
from muad_console_platform.infrastructure.models.control import Skill, SkillArtifact
from muad_console_platform.infrastructure.skill_artifact_store import (
    artifact_path,
    write_artifact,
)

GRACE_SECONDS = 3600


def _touch_orphan(storage_key: str, *, age_seconds: float, root: Path) -> Path:
    target = artifact_path(storage_key, root=root)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"PK\x03\x04 orphan")
    stamp = time.time() - age_seconds
    os.utime(target, (stamp, stamp))
    return target


def test_write_artifact_is_immutable(tmp_path: Path) -> None:
    """[RULE-skill-001] 同一 storage_key 二次写入被拒（不可变）。"""
    key = f"skills/{uuid.uuid4()}/{uuid.uuid4()}/skill.zip"
    write_artifact(key, b"first", root=tmp_path)
    with pytest.raises(FileExistsError):
        write_artifact(key, b"second", root=tmp_path)
    assert artifact_path(key, root=tmp_path).read_bytes() == b"first"


async def test_cleanup_orphan_artifacts_removes_only_stale_orphans(
    tmp_path: Path,
    database_guard: None,
) -> None:
    """[RULE-skill-001] 孤儿清理：删过期无 DB 记录文件，保留 DB 记录文件与宽限期内文件。"""
    tenant = f"orphan-test-{uuid.uuid4()}"
    async with get_session_factory()() as session:
        skill = Skill(tenant_id=tenant, key=f"skill-{uuid.uuid4()}", name="a", description="d")
        session.add(skill)
        await session.flush()
        recorded = SkillArtifact(
            skill_id=skill.id,
            version=f"1.0.{uuid.uuid4().int % 100000}",
            checksum="sha256:" + uuid.uuid4().hex + uuid.uuid4().hex,
            storage_key=f"skills/{skill.id}/{uuid.uuid4()}/skill.zip",
            package_size=4,
            validation_status="READY",
            created_by=uuid.uuid4(),
        )
        session.add(recorded)
        await session.commit()
        recorded_key = recorded.storage_key

        # DB 记录的文件：保留
        recorded_file = _touch_orphan(recorded_key, age_seconds=GRACE_SECONDS * 10, root=tmp_path)
        # 过期孤儿：删除
        stale_key = f"skills/{uuid.uuid4()}/{uuid.uuid4()}/skill.zip"
        _touch_orphan(stale_key, age_seconds=GRACE_SECONDS * 2, root=tmp_path)
        # 宽限期内孤儿（可能是事务进行中）：保留
        fresh_key = f"skills/{uuid.uuid4()}/{uuid.uuid4()}/skill.zip"
        fresh_orphan = _touch_orphan(fresh_key, age_seconds=30, root=tmp_path)
        # 崩溃残留的 .tmp-* 写入文件：过期删除、宽限期内保留
        stale_tmp = artifact_path(stale_key, root=tmp_path).parent / ".tmp-stale"
        stale_tmp.write_bytes(b"partial")
        os.utime(stale_tmp, (time.time() - GRACE_SECONDS * 2, time.time() - GRACE_SECONDS * 2))
        fresh_tmp = artifact_path(fresh_key, root=tmp_path).parent / ".tmp-fresh"
        fresh_tmp.write_bytes(b"partial")
        os.utime(fresh_tmp, (time.time() - 30, time.time() - 30))

        service = SkillService(session)
        removed = await service.cleanup_orphan_artifacts(
            grace_seconds=GRACE_SECONDS, root=tmp_path
        )

        await session.execute(
            SkillArtifact.__table__.delete().where(SkillArtifact.skill_id == skill.id)
        )
        await session.execute(Skill.__table__.delete().where(Skill.id == skill.id))
        await session.commit()

    assert set(removed) == {stale_key, stale_tmp.relative_to(tmp_path).as_posix()}
    assert artifact_path(stale_key, root=tmp_path).exists() is False
    assert stale_tmp.exists() is False
    assert recorded_file.exists() is True
    assert fresh_orphan.exists() is True
    assert fresh_tmp.exists() is True
